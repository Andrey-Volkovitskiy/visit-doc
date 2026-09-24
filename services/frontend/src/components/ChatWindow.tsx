import { Bot, SendHorizontal } from "lucide-react";
import { useRef, useState } from "react";
import { askChat, fetchChatHistory, type Message } from "../lib/chatStream";
import { useBottomPin } from "../lib/useBottomPin";
import { isSendKey } from "../lib/sendKey";
import { useThreadReads, type Banner } from "../lib/useThreadReads";
import { MessageView } from "./MessageView";
import { Button } from "./ui/button";
import { Textarea } from "./ui/textarea";

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
  const local = shown.filter((message) =>
    message.id.startsWith(LOCAL_ID_PREFIX),
  );
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
export const MAX_MESSAGE_LENGTH = 2000;

/**
 * The length from which the character count is shown (FR-019a).
 *
 * "Approaching the limit" needs a number, and this is it, declared once so the
 * component and its test cannot disagree about where the approach begins. The counter
 * must not appear for a message nowhere near the limit, which is almost all of them —
 * so this is a late warning on purpose, not a running tally.
 */
export const CHAR_COUNT_FROM = MAX_MESSAGE_LENGTH - 200;

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
  /**
   * Whether the assistant is permitted to reply in this conversation.
   *
   * Read by `App` off the console poll row it already holds, which is where the value
   * lives — no request of this pane's own, and no second source able to disagree with
   * the console about a conversation a staff member just took (`research.md` Decision
   * 3). It decides one thing: whether a turn in flight shows a running indicator
   * (FR-016) or nothing at all (FR-017).
   *
   * Defaults to true. A brand-new chat has no poll row yet, and showing the indicator
   * for a turn that turns out to be silent is a smaller error than withholding it for
   * every turn in a chat's first two seconds.
   */
  assistantMayReply?: boolean;
}

export function ChatWindow({
  chatId,
  onTurnComplete,
  lastMessageAt,
  pollTick,
  assistantMayReply = true,
}: ChatWindowProps) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  // Keyed by turn rather than a single string: several turns can be in flight at once
  // (see below), and one shared slot means whichever finishes first clears the other's
  // in-progress bubble, while their tokens interleave in one bubble until it does.
  const [streaming, setStreaming] = useState<Record<string, string>>({});
  // Why the banner is up, not just its words. Two things raise one — a history that
  // would not load and a turn that would not send — and only the first is disproved by
  // a later read landing. See `Banner`.
  const [banner, setBanner] = useState<Banner | null>(null);
  // Every send's own controller lives here for the duration of its request, not
  // just the latest one - several turns can be genuinely in flight at once (a
  // burst of quick patient messages), and each must run to completion
  // independently. The server alone decides whether an earlier turn in the same
  // chat gets superseded (`cancelled` event) - a still-genuinely-completing
  // earlier request must never be aborted from here just because a newer send
  // started, or its final `done` event (and the reply the server already
  // persisted) would be thrown away client-side, only reappearing on reload.
  const activeControllersRef = useRef<Set<AbortController>>(new Set());
  const streamingCount = Object.keys(streaming).length;
  // Whether a history read has come back for the chat on screen. Three-valued in
  // effect, and deliberately not inferred from `messages.length === 0`: waiting,
  // arrived-empty and failed are three different situations (FR-010a), and only the
  // middle one is greeted (FR-019c).
  const [historyLoaded, setHistoryLoaded] = useState(false);
  // Follow new content to the bottom, but only for a reader who was already there
  // (FR-015a). The rule, and the ref that holds the answer, live in `useBottomPin` —
  // the staff thread is held to the same terms by FR-015b and reads the same hook.
  const threadScroll = useBottomPin<HTMLDivElement>();

  useThreadReads<Message[]>({
    chatId,
    lastMessageAt,
    pollTick,
    // Left unhandled while a reply is streaming, so the tick is retried once the turn
    // finishes: replacing the history mid-stream would race the reply about to be
    // appended to it.
    paused: streamingCount > 0,
    read: (id, signal) => fetchChatHistory(id, signal),
    onReset: () => {
      setHistoryLoaded(false);
      // A different conversation has no position to hold: it opens at its most recent
      // message (FR-015), however far up the previous one had been scrolled.
      threadScroll.pin();
      // Switching chats abandons whatever the previous one had in flight: its reply
      // belongs to a thread that is no longer on screen, and letting it land would
      // append it to the wrong history. The *reads* are the hook's to abort; these are
      // this pane's own streaming turns.
      for (const controller of activeControllersRef.current) {
        controller.abort();
      }
      activeControllersRef.current.clear();
      setMessages([]);
      setStreaming({});
      setBanner(null);
    },
    // Reconciled rather than assigned, on every read: a message sent before this answer
    // was composed is already on screen and is not in it — the patient's own bubble,
    // put up the moment they hit send, or a reply that arrived while the read was out.
    onLoaded: (history) => {
      setHistoryLoaded(true);
      setMessages((shown) => reconcile(shown, history));
      // This is the history the failed opening read could not load, so the banner it
      // raised goes with it — and only that one. A banner about a turn that would not
      // send is not disproved by a history that loaded, and clearing it would tell the
      // patient their question went in.
      setBanner((previous) => (previous?.kind === "read" ? null : previous));
    },
    onOpenFailed: (err) => {
      // A chat deleted in another tab 404s here. Reporting it beats leaving the pane
      // silently empty, and leaves `messages` a real array either way.
      setBanner({
        kind: "read",
        text:
          err instanceof Error
            ? err.message
            : "Could not load this chat's history.",
      });
    },
  });

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
    setBanner(null);
    // Sending is not unbidden content: the patient just acted, so their own message
    // follows to the bottom whatever they had scrolled to beforehand.
    threadScroll.pin();

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
        // A patient message was never retrieved against, so it has no request outcome
        // to carry - null, never `[]`, which would read as a half that ran.
        request_outcomes: null,
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
              request_outcomes: event.request_outcomes,
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
      setBanner({
        kind: "send",
        text:
          err instanceof Error
            ? err.message
            : "Something went wrong. Please try again.",
      });
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
      <div
        data-testid="no-chat"
        className="text-ink-muted flex flex-1 items-center justify-center p-6 text-base"
      >
        <p>No chat selected. Create one to start talking.</p>
      </div>
    );
  }

  const overLimit = input.length > MAX_MESSAGE_LENGTH;
  const empty = input.trim().length === 0;
  const sendDisabled = empty || overLimit;
  // The reason the control is disabled, in words, so it reaches assistive technology
  // rather than being carried by the control's appearance alone (FR-019b).
  const sendReason = overLimit
    ? "Message is too long to send."
    : empty
      ? "Type a message to send."
      : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        data-testid="messages"
        ref={threadScroll.ref}
        // A scroll container of its own, so the thread scrolls and the composer and tab
        // strip stay put (FR-015).
        className="min-h-0 flex-1 overflow-y-auto p-4"
        role="log"
        aria-label="Conversation"
        tabIndex={0}
        onScroll={threadScroll.onScroll}
      >
        {/*
          Rendered only for a thread that has *answered* and holds nothing (FR-019c).
          Interface copy, not a bubble, and carrying no sender: a greeting inside a
          message would be something the patient could reasonably believe the assistant
          said. A thread still loading, and one that failed, each get neither — those
          are different situations under FR-010a and the banner speaks for the second.
        */}
        {historyLoaded && messages.length === 0 && streamingCount === 0 && (
          <div
            data-testid="thread-greeting"
            className="text-ink-muted border-rule-soft bg-surface-sunken rounded-md border border-dashed p-4 text-sm"
          >
            <p className="text-ink text-base font-medium">
              Ask the clinic anything.
            </p>
            <p className="mt-1">
              I can make, change and cancel appointments, say who practises here and
              when they are free, and answer questions about the clinic from its own
              documents. If I cannot help, I will pass you to a member of staff.
            </p>
          </div>
        )}
        {messages.map((message, i) => (
          // No `requestOutcomes`: this pane draws none of it, so passing them would
          // read as a rendering decision made somewhere else. The staff console is the
          // surface that shows how an answer was produced.
          <MessageView
            key={message.id}
            sender={message.sender}
            content={message.content}
            startsBurst={i === 0 || messages[i - 1]!.sender !== message.sender}
          />
        ))}
        {Object.entries(streaming).map(([turnKey, text]) =>
          text.length > 0 ? (
            // The reply has begun arriving, so the indicator has done its job and the
            // bubble takes over (FR-018). Always a burst start: it is the first thing
            // the assistant has said in this run by definition.
            <MessageView key={turnKey} sender="assistant" content={text} />
          ) : assistantMayReply ? (
            <WorkingIndicator key={turnKey} />
          ) : (
            // Nothing. FR-017 forbids a notice, a placeholder or a countdown in the
            // indicator's place: the patient's message sits in the thread as sent and
            // the interface makes no claim about who will reply or when. An empty
            // assistant bubble here would be exactly such a claim.
            null
          ),
        )}
      </div>
      <div className="border-rule bg-surface flex flex-col gap-2 rounded-b-md border-t px-4 py-3">
        <div className="flex items-end gap-2">
          <Textarea
            aria-label="question"
            aria-describedby={sendReason === null ? undefined : "composer-reason"}
            rows={2}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (isSendKey(e)) {
                e.preventDefault();
                void handleSend();
              }
            }}
            placeholder="Ask a question..."
            className="min-h-0 resize-none"
          />
          <Button
            onClick={() => void handleSend()}
            disabled={sendDisabled}
            aria-describedby={sendReason === null ? undefined : "composer-reason"}
          >
            Send
            <SendHorizontal aria-hidden="true" />
          </Button>
        </div>
        {/*
          Two hooks, not one. At exactly the limit the message is sendable, so there is
          no error — and that is precisely where the counter has to be present. One hook
          for both would make FR-019a unimplementable without breaking a test that is
          still right.
        */}
        {overLimit ? (
          <p
            id="composer-reason"
            data-testid="length-error"
            className="text-attention text-xs font-medium"
          >
            Message is too long ({input.length}/{MAX_MESSAGE_LENGTH} characters).
          </p>
        ) : (
          <>
            {input.length >= CHAR_COUNT_FROM && (
              <p data-testid="char-count" className="text-ink-muted text-xs">
                {input.length}/{MAX_MESSAGE_LENGTH}
              </p>
            )}
            {/* `sendReason` itself, not a second copy of its words: the control's
                `aria-describedby` is derived from it, so a literal here would be a
                wording only one of the two could be changed in. */}
            {sendReason !== null && (
              <p id="composer-reason" className="sr-only">
                {sendReason}
              </p>
            )}
          </>
        )}
        {banner && (
          <p
            data-testid="error"
            className="text-attention bg-attention-wash border-attention/30 rounded-md border px-3 py-2 text-sm"
          >
            {banner.text}
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * The three-dot running indicator: one per turn awaiting a reply (FR-016).
 *
 * `role="status"` rather than a bare decoration, so the wait is announced rather than
 * only drawn. Under `prefers-reduced-motion` the dots stop moving and keep a visible
 * resting opacity — the animation is suppressed, the state is not (FR-042); the rule
 * for that lives in `app.css` beside the keyframes it disables.
 */
function WorkingIndicator() {
  return (
    <div className="mt-4 flex items-end gap-3">
      {/*
        The assistant's own icon, the same one its messages carry. The indicator stands
        exactly where the reply's bubble will stand, so a different glyph here would have
        one turn showing two different assistants a second apart.
      */}
      <span
        aria-hidden="true"
        className="bg-accent text-surface grid size-7 flex-none place-items-center rounded-full"
      >
        <Bot className="size-3.5" />
      </span>
      <div
        data-testid="working-indicator"
        role="status"
        aria-label="The assistant is replying"
        className="bg-bubble-them border-rule-soft inline-flex items-center gap-1.5 rounded-md border px-3 py-3.5"
      >
        <i className="working-dot bg-ink-muted animate-dot size-1.5 rounded-full" />
        <i className="working-dot bg-ink-muted animate-dot size-1.5 rounded-full [animation-delay:0.18s]" />
        <i className="working-dot bg-ink-muted animate-dot size-1.5 rounded-full [animation-delay:0.36s]" />
      </div>
    </div>
  );
}

export default ChatWindow;
