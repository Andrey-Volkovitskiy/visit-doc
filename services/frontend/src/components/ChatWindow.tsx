import { useEffect, useRef, useState } from "react";
import { askChat, fetchChatHistory, type Message } from "../lib/chatStream";
import { MessageView } from "./MessageView";

let nextMessageId = 0;

// Marks a message this pane put on screen itself, which the server has not published
// back to it yet. Server ids are ULIDs, so the two can never collide.
const LOCAL_ID_PREFIX = "local-";

function localId(): string {
  nextMessageId += 1;
  return `${LOCAL_ID_PREFIX}${nextMessageId}`;
}

/**
 * Fold a freshly fetched history into what is on screen, keeping what it does not
 * carry yet.
 *
 * A turn's `done` event is streamed before the server commits the assistant row - the
 * stream is the fast path, and the write follows it - so a history read fired the
 * instant a turn ends is legitimately answered without the reply already rendered
 * here. Replacing the thread with that answer blanks the reply until the next poll
 * tick notices the insert: a couple of seconds in which the patient's question looks
 * unanswered. The same holds for the patient's own message while its insert is in
 * flight.
 *
 * So fetched history is the authority on everything it *does* carry, and locally
 * appended messages it does not account for are kept on the end rather than dropped.
 * Each one leaves as soon as a later read accounts for it, so a reply is never shown
 * twice once the server publishes its own row for it.
 *
 * Matched on sender and content, one server row consumed per local message, because a
 * local message has no server id to match on. Only the rows the server has grown since
 * this pane's last read are searched, so re-sending the same text is not mistaken for
 * the earlier identical send.
 */
function reconcile(shown: Message[], history: Message[]): Message[] {
  // Local messages are only ever appended, and a fetch only ever puts server rows
  // ahead of them, so they are always a suffix of what is on screen.
  const local = shown.filter((message) => message.id.startsWith(LOCAL_ID_PREFIX));
  if (local.length === 0) return history;

  const grown = history.slice(Math.max(shown.length - local.length, 0));
  const unpublished: Message[] = [];
  for (const message of local) {
    const index = grown.findIndex(
      (row) => row.sender === message.sender && row.content === message.content,
    );
    if (index === -1) {
      unpublished.push(message);
    } else {
      grown.splice(index, 1);
    }
  }
  return unpublished.length === 0 ? history : [...history, ...unpublished];
}

// Must match `ChatRequest.message`'s `max_length` in
// services/chat/src/chat/domain/schemas.py - checked client-side too so the
// patient gets immediate feedback instead of a round trip to hit the same 422.
const MAX_MESSAGE_LENGTH = 2000;

interface ChatWindowProps {
  /** The chat to show and send to. Null when the session holds no chats at all. */
  chatId: string | null;
  /** Called after a turn completes, so the chat list can refresh its ordering. */
  onTurnComplete?: () => void;
  /**
   * The newest message time the console poll reports for this chat.
   *
   * When it advances past the value this pane last acted on, something wrote into the
   * thread that this pane did not — a staff reply — and the history is refetched. That
   * is the whole mechanism by which a staff reply appears here without a reload, and it
   * rides the one poll that already runs rather than opening a channel of its own.
   */
  lastMessageAt?: string | null;
}

export function ChatWindow({
  chatId,
  onTurnComplete,
  lastMessageAt,
}: ChatWindowProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  // Keyed by turn rather than a single string: several turns can be in flight at once
  // (see below), and one shared slot means whichever finishes first clears the other's
  // in-progress bubble, while their tokens interleave in one bubble until it does.
  const [streaming, setStreaming] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  // Every send's own controller lives here for the duration of its request, not
  // just the latest one - several turns can be genuinely in flight at once (a
  // burst of quick patient messages), and each must run to completion
  // independently. The server alone decides whether an earlier turn in the same
  // chat gets superseded (`cancelled` event) - a still-genuinely-completing
  // earlier request must never be aborted from here just because a newer send
  // started, or its final `done` event (and the reply the server already
  // persisted) would be thrown away client-side, only reappearing on reload.
  const activeControllersRef = useRef<Set<AbortController>>(new Set());
  // The poll value this pane has already accounted for. Compared by identity rather
  // than by clock arithmetic: an optimistic local message carries the browser's own
  // time, and comparing a server timestamp against it would make a skewed clock decide
  // whether a staff reply is ever shown.
  //
  // `undefined` is "nothing accounted for yet" and is distinct from `null`, which is a
  // real answer the poll gives about a chat holding no messages. Collapsing the two
  // would lose exactly one message: the first ever written into an open, empty chat by
  // someone other than this pane. Its timestamp would be the first non-null value seen,
  // the branch below would file it as "describes the history just loaded", and a staff
  // member's opening line would sit unfetched until a reload.
  const handledLastMessageAtRef = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    // Switching chats abandons whatever the previous one had in flight: its reply
    // belongs to a thread that is no longer on screen, and letting it land would
    // append it to the wrong history.
    for (const controller of activeControllersRef.current) {
      controller.abort();
    }
    activeControllersRef.current.clear();
    handledLastMessageAtRef.current = undefined;
    setMessages([]);
    setStreaming({});
    setError(null);

    if (chatId === null) return;
    let current = true;
    void fetchChatHistory(chatId)
      .then((history) => {
        // Reconciled rather than assigned: a message sent before this first read lands
        // is already on screen and is not in the answer to it.
        if (current) setMessages((shown) => reconcile(shown, history));
      })
      .catch((err: unknown) => {
        // A chat deleted in another tab 404s here. Reporting it beats leaving the
        // pane silently empty, and leaves `messages` a real array either way.
        if (current) {
          setError(
            err instanceof Error
              ? err.message
              : "Could not load this chat's history.",
          );
        }
      });
    return () => {
      current = false;
    };
  }, [chatId]);

  const streamingCount = Object.keys(streaming).length;

  useEffect(() => {
    // `undefined` here means the caller is not feeding this pane the poll at all, which
    // is a different thing from a chat the poll says is empty.
    if (chatId === null || lastMessageAt === undefined) return;
    if (handledLastMessageAtRef.current === lastMessageAt) return;
    if (handledLastMessageAtRef.current === undefined) {
      // The first value seen for this chat describes the history just loaded, so there
      // is nothing new in it to fetch.
      handledLastMessageAtRef.current = lastMessageAt;
      return;
    }
    // Left unhandled while a reply is streaming, so this tick is retried once the turn
    // finishes: replacing the history mid-stream would race the reply about to be
    // appended to it.
    if (streamingCount > 0) return;
    handledLastMessageAtRef.current = lastMessageAt;

    let current = true;
    void fetchChatHistory(chatId)
      .then((history) => {
        // This tick was deferred until the turn finished, and the reply the turn just
        // rendered is committed *after* the `done` that finished it - so this answer
        // can predate the assistant row. Reconciling keeps it on screen instead of
        // blanking it until the next tick.
        if (current) setMessages((shown) => reconcile(shown, history));
      })
      // A failed refetch leaves the thread as it was; the next poll tick tries again,
      // which is not worth an error banner over a message the patient has not missed.
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [chatId, lastMessageAt, streamingCount]);

  function clearStreaming(turnKey: string): void {
    setStreaming((prev) => {
      const { [turnKey]: _removed, ...rest } = prev;
      return rest;
    });
  }

  async function handleSend(): Promise<void> {
    const messageText = input;
    if (chatId === null) return;
    if (!messageText.trim() || messageText.length > MAX_MESSAGE_LENGTH) return;

    setInput("");
    setError(null);

    const turnKey = localId();
    setStreaming((prev) => ({ ...prev, [turnKey]: "" }));

    const controller = new AbortController();
    activeControllersRef.current.add(controller);

    setMessages((prev) => [
      ...prev,
      {
        id: localId(),
        sender: "patient",
        content: messageText,
        grounded: null,
        citations: null,
        attention_mark: null,
        created_at: new Date().toISOString(),
      },
    ]);

    let accumulated = "";
    try {
      const events = await askChat(chatId, messageText, controller.signal);
      for await (const event of events) {
        if (event.type === "token") {
          accumulated += event.text;
          setStreaming((prev) => ({ ...prev, [turnKey]: accumulated }));
        } else if (event.type === "silent") {
          // A person is handling this conversation, so nothing was generated and there
          // is nothing to render. The message stays in the thread exactly as sent.
          clearStreaming(turnKey);
          return;
        } else if (event.type === "cancelled") {
          // Superseded by a newer message - remove the in-progress bubble and any
          // partial tokens entirely; never shown as final, never as an error. Only
          // this turn's, so a sibling turn still streaming keeps its own.
          clearStreaming(turnKey);
          return;
        } else {
          clearStreaming(turnKey);
          setMessages((prev) => [
            ...prev,
            {
              id: localId(),
              sender: "assistant",
              // `message` is set only when there is no streamed text to show (the
              // FAQ abstention case); otherwise the accumulated tokens are the
              // reply, whether it came from the FAQ path, the booking path, or both.
              content: event.message ?? accumulated,
              grounded: event.grounded,
              citations: event.citations,
              attention_mark: null,
              created_at: new Date().toISOString(),
            },
          ]);
          onTurnComplete?.();
        }
      }
    } catch (err) {
      if (controller.signal.aborted) {
        // Aborted by a chat switch or a deletion, both of which already reset the
        // display state - there is nothing left for this stale request to do.
        return;
      }
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
      clearStreaming(turnKey);
      setInput(messageText);
    } finally {
      activeControllersRef.current.delete(controller);
    }
  }

  if (chatId === null) {
    return (
      <div data-testid="no-chat" style={{ opacity: 0.5 }}>
        <p>No chat selected. Create one to start talking.</p>
      </div>
    );
  }

  return (
    <div>
      <div data-testid="messages">
        {messages.map((message) => (
          <MessageView
            key={message.id}
            sender={message.sender}
            content={message.content}
            citations={message.citations}
            grounded={message.grounded}
          />
        ))}
        {Object.entries(streaming).map(([turnKey, text]) => (
          <MessageView key={turnKey} sender="assistant" content={text} />
        ))}
      </div>
      <textarea
        aria-label="question"
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            void handleSend();
          }
        }}
        placeholder="Ask a question..."
      />
      <button onClick={() => void handleSend()}>Send</button>
      {input.length > MAX_MESSAGE_LENGTH && (
        <p data-testid="length-error" style={{ color: "red" }}>
          Message is too long ({input.length}/{MAX_MESSAGE_LENGTH} characters).
        </p>
      )}
      {error && <p data-testid="error">{error}</p>}
    </div>
  );
}

export default ChatWindow;
