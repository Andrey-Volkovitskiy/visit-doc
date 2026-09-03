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
 * A turn's `done` event is streamed only once the server has committed the assistant
 * row, so a read *issued after* a turn ends always carries its reply. A read still in
 * flight *across* one does not: the mount/chat-switch fetch and the poll-driven
 * refetch are each answered from the thread as it stood when they were issued, and
 * anything this pane appended in the meantime is not in that answer - the patient's own
 * bubble, put up the moment they hit send and before its insert has landed, or a reply
 * that arrived while the fetch was outstanding. Replacing the thread with that answer
 * blanks them until the next poll tick notices the inserts: a couple of seconds in
 * which the patient's question looks unsent, or unanswered.
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
   * When it advances past the value this pane last read, something wrote into the
   * thread that this pane did not — a staff reply — and the history is refetched. That
   * is the whole mechanism by which a staff reply appears here without a reload, and it
   * rides the one poll that already runs rather than opening a channel of its own.
   */
  lastMessageAt?: string | null;
  /**
   * How many times that poll has answered, which changes on every tick.
   *
   * It is what makes a *retry* possible at all. `lastMessageAt` stops changing the
   * moment the newest message is the newest message, so an effect watching only it runs
   * once per new message and never again — a read that failed would have nothing left to
   * wake it. Ticking this instead lets the refetch below be attempted every couple of
   * seconds for as long as it is still owed, and cost nothing on the ticks where it is
   * not. Omitted by a caller not feeding this pane the poll, which then behaves as it
   * always did: a refetch per new value, and no retry.
   */
  pollTick?: number;
}

export function ChatWindow({
  chatId,
  onTurnComplete,
  lastMessageAt,
  pollTick,
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
  // The poll value this pane has already read the history for — recorded when that read
  // *lands*, never when it is issued, so it means "accounted for" and not "attempted".
  // Compared by identity rather than by clock arithmetic: an optimistic local message
  // carries the browser's own time, and comparing a server timestamp against it would
  // make a skewed clock decide whether a staff reply is ever shown.
  //
  // `undefined` is "nothing accounted for yet" and is distinct from `null`, which is a
  // real answer the poll gives about a chat holding no messages. Collapsing the two
  // would lose exactly one message: the first ever written into an open, empty chat by
  // someone other than this pane. Its timestamp would be the first non-null value seen,
  // the branch below would file it as "describes the history just loaded", and a staff
  // member's opening line would sit unfetched until a reload.
  const handledLastMessageAtRef = useRef<string | null | undefined>(undefined);
  // Numbers every read of this history as it is issued — the one that opens a chat and
  // the poll-driven ones alike, since either can be answered long after the screen has
  // moved past what it asked about.
  const issuedReadsRef = useRef(0);
  // The newest read whose answer the screen already reflects. An answer numbered at or
  // below it describes a history older than what is displayed, so it is dropped rather
  // than applied — applying it would be a rewind. Two things move it, and together they
  // are the whole staleness rule:
  //   * a read landing, the ordinary case: nothing issued before it is wanted after it;
  //   * opening a chat, which retires every read still in flight — each answers about a
  //     history that is no longer on screen. A *number* and not a comparison of chat
  //     ids, because leaving a chat and coming back to it is a second visit and not the
  //     same one: a read issued during the first visit still names the chat now open,
  //     and only its number says it belongs to a history this pane has since thrown away
  //     and reloaded.
  // A read that *failed* deliberately does not move it: it put nothing on screen, so an
  // answer issued before it is still a correction rather than a rewind.
  //
  // `StaffThread` has a third mover, a staff post landing, and this pane needs no
  // equivalent for a patient's own send: a local message is `reconcile`d back on top of
  // whatever history arrives, so an older answer cannot take it off the screen. What an
  // older answer *can* take off is a staff reply a later read already brought in, which
  // is what the number is here for.
  const appliedThroughRef = useRef(0);
  // The read that opens a chat, while it is still outstanding, and the poll value it
  // will file if it lands — null once it has settled either way. It is what stops the
  // effect below asking, on this chat's very first tick, the question already out. Keyed
  // to the value rather than to "a read is in flight": a read that hangs then holds up
  // nothing but the tick reporting exactly what it went to fetch, so a message arriving
  // meanwhile is still read here instead of waiting on an answer that may never come.
  const openingReadRef = useRef<{ accountsFor: string | null | undefined } | null>(
    null,
  );

  useEffect(() => {
    // Switching chats abandons whatever the previous one had in flight: its reply
    // belongs to a thread that is no longer on screen, and letting it land would
    // append it to the wrong history.
    for (const controller of activeControllersRef.current) {
      controller.abort();
    }
    activeControllersRef.current.clear();
    handledLastMessageAtRef.current = undefined;
    // Retired here, before the refetch effect below can run for the newly opened chat,
    // since effects run in the order they are declared: every read issued so far — for
    // the chat just closed, or for an earlier visit to this very one — is now answering
    // about a history that is no longer on screen, and its number is what says so
    // however many poll ticks pass while it is in flight.
    appliedThroughRef.current = issuedReadsRef.current;
    openingReadRef.current = null;
    setMessages([]);
    setStreaming({});
    setError(null);

    if (chatId === null) return;
    // The value the poll is reporting as this chat opens, which is what this read will
    // account for. Captured here rather than read when the answer lands: a message
    // written while the read is out is not in its answer, so filing the value that
    // describes *that* message would lose it. Deliberately not a dependency of this
    // effect — it is a snapshot of the moment the read went out, and re-running the
    // reset above on every new poll value is the last thing this pane wants.
    const accountsFor = lastMessageAt;
    const generation = ++issuedReadsRef.current;
    openingReadRef.current = { accountsFor };
    void fetchChatHistory(chatId)
      .then((history) => {
        if (generation <= appliedThroughRef.current) return;
        appliedThroughRef.current = generation;
        openingReadRef.current = null;
        // The first marker is filed by this read, on landing, and by nothing else. The
        // effect below used to file the first value it saw on the assumption that this
        // read described it — an assumption a read that failed does not keep, which left
        // the pane blank behind a banner with every later tick reporting the very value
        // already filed as handled.
        handledLastMessageAtRef.current = accountsFor;
        // Reconciled rather than assigned: a message sent before this first read lands
        // is already on screen and is not in the answer to it.
        setMessages((shown) => reconcile(shown, history));
      })
      .catch((err: unknown) => {
        if (generation <= appliedThroughRef.current) return;
        // Cleared with nothing filed as handled, which is what lets the effect below
        // take the read over on the next tick instead of leaving this pane empty behind
        // the banner until somebody writes into the chat.
        openingReadRef.current = null;
        // A chat deleted in another tab 404s here. Reporting it beats leaving the
        // pane silently empty, and leaves `messages` a real array either way.
        setError(
          err instanceof Error ? err.message : "Could not load this chat's history.",
        );
      });
  }, [chatId]);

  const streamingCount = Object.keys(streaming).length;

  useEffect(() => {
    // `undefined` here means the caller is not feeding this pane the poll at all, which
    // is a different thing from a chat the poll says is empty.
    if (chatId === null || lastMessageAt === undefined) return;
    if (handledLastMessageAtRef.current === lastMessageAt) return;
    const openingRead = openingReadRef.current;
    if (openingRead !== null && openingRead.accountsFor === lastMessageAt) {
      // The read that opened this chat is already out for exactly this value, and files
      // it itself when it lands. Asking again here would only be the same question
      // twice. Note what this does *not* wait for: any other value, so a message
      // arriving while that read hangs is fetched below rather than held behind an
      // answer that may never arrive.
      return;
    }
    // Left unhandled while a reply is streaming, so this tick is retried once the turn
    // finishes: replacing the history mid-stream would race the reply about to be
    // appended to it.
    if (streamingCount > 0) return;

    // Numbered as it goes out, and judged by that number when it lands. Every tick that
    // is still owed a read issues one, deliberately: skipping while another is
    // outstanding makes this pane's liveness depend on that read completing, and a read
    // that hangs — a wedged connection, the case a retry exists for — would then stop
    // the refetch for as long as it hangs, or for good. That is the trade
    // `useConsolePoll` refuses for the same reason, and the cost is bounded by how far a
    // read runs behind the interval.
    const generation = ++issuedReadsRef.current;
    // The value this read goes out to account for, captured now rather than read when
    // the answer lands: by then the poll may be reporting a later message, and filing
    // *that* value would mark as handled a message this answer does not contain.
    const accountsFor = lastMessageAt;
    // Whether this read is standing in for an opening read that filed nothing, which is
    // the one case where a banner is on screen that this answer disproves.
    const takingOverOpeningRead = handledLastMessageAtRef.current === undefined;
    void fetchChatHistory(chatId)
      .then((history) => {
        // Judged by its number, never by whether this effect has re-run since: with the
        // tick among its dependencies it re-runs every couple of seconds, and a read
        // outstanding across one of those is not stale — it is only slower than the
        // poll. What makes an answer wrong is something newer already being on screen,
        // and the number is what says so: a chat opened or reopened since, or a later
        // read already applied.
        if (generation <= appliedThroughRef.current) return;
        appliedThroughRef.current = generation;
        if (takingOverOpeningRead) {
          // The read that opened the chat failed and left a banner; this answer is the
          // history it could not load, so the banner goes with it.
          setError(null);
        }
        // Marked handled on the answer that actually arrived, and with the value it went
        // out for. Marking it where the read was *issued* made the marker mean
        // "attempted", which loses the message a failed read was fetching: the staff
        // reply is still the newest message afterwards, so the poll goes on reporting
        // the very time already filed as handled, and no tick until someone writes into
        // the thread again would disagree with it.
        handledLastMessageAtRef.current = accountsFor;
        // Deferred until no turn was streaming, but issued before whatever started
        // afterwards: a send made while this was in flight, and the reply it drew, are
        // both missing from an answer that was composed before either existed.
        // Reconciling keeps them on screen instead of blanking them until the next tick.
        setMessages((shown) => reconcile(shown, history));
      })
      // A failed refetch leaves the thread as it was, the tick unhandled so the next one
      // genuinely does try again, and its number unretired — it put nothing on screen,
      // so an answer issued before it is still a correction rather than a rewind. That
      // is worth more than an error banner over a message the patient has not missed.
      .catch(() => undefined);
  }, [chatId, lastMessageAt, pollTick, streamingCount]);

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
          // This turn produced no reply - superseded by a newer message, or a person
          // took the conversation over before it was written. Remove the in-progress
          // bubble and any partial tokens entirely; never shown as final, never as an
          // error. Only this turn's, so a sibling turn still streaming keeps its own.
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
              // Falsy rather than nullish, so an empty `message` falls back to the
              // tokens exactly as the server's own `done_event.message or answer`
              // does. Diverging here renders a bubble holding text the thread does
              // not hold, and `reconcile` matches on content - so that bubble would
              // never be accounted for by a history read, and would sit on screen
              // until the chat is switched away from.
              content: event.message || accumulated,
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
      // The client half of the server's guarantee that every turn ends in exactly one
      // terminal event: whatever ended this one - a terminal event, an abort, a broken
      // stream, or a stream that simply stopped - the in-progress bubble goes with it.
      // A no-op on every path that has already cleared its own, and the only thing
      // standing between a stream that ends without an ending and a bubble the patient
      // watches until they switch chats.
      clearStreaming(turnKey);
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
