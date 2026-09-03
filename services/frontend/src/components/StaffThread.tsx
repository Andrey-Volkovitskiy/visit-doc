import { useEffect, useRef, useState } from "react";
import type { Message } from "../lib/chatStream";
import { fetchThread, postStaffMessage } from "../lib/consoleApi";
import { MessageView } from "./MessageView";

// Matches `StaffMessageWrite.content`'s `max_length` in
// services/chat/src/chat/domain/schemas.py - checked here too so a staff member gets
// immediate feedback instead of a round trip to hit the same 422.
const MAX_REPLY_LENGTH = 2000;

/**
 * Render a server-computed number of seconds as `m:ss`.
 *
 * Only the display is derived here. The number itself is the server's arithmetic over
 * a stored deadline, re-read on every poll, which is what makes two open tabs agree.
 */
function formatRemaining(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

interface StaffThreadProps {
  /** The conversation to read and reply in. Null when none is open. */
  chatId: string | null;
  /**
   * Whether the assistant may speak here, derived server-side from the escalation and
   * the pause together — so the switch cannot disagree with the gate a turn obeys.
   */
  assistantMayReply: boolean;
  /**
   * Seconds left on a running pause, or null when none is running.
   *
   * Null while escalated too: an escalation has no deadline, and a zero would claim a
   * countdown had run out. Computed server-side and re-read on every poll, which is
   * what makes two open tabs agree instead of each counting from when it loaded.
   */
  pauseSecondsRemaining: number | null;
  /**
   * The newest message time the console poll reports for this conversation.
   *
   * When it advances past the value this pane last read, something was written into the
   * conversation that this pane did not write — a patient message arriving while a
   * staff member sits reading it — and the thread is refetched. Same mechanism as
   * `ChatWindow`'s prop of the same name, and for the same reason: it rides the one poll
   * that already runs rather than opening a second channel that would have to be kept in
   * step with the first.
   */
  lastMessageAt?: string | null;
  /**
   * How many times that poll has answered, which changes on every tick.
   *
   * What makes a *retry* possible: `lastMessageAt` stops changing once the newest
   * message is the newest message, so an effect watching only it gets one attempt per
   * message and nothing would ever wake it for a second. Same prop, same reason, as
   * `ChatWindow`'s.
   */
  pollTick?: number;
  onSetAssistant: (enabled: boolean) => void;
}

/**
 * One conversation, read and answered by a person.
 *
 * The thread is the *whole* conversation, every sender included — a staff member is
 * answering a patient who has already read the assistant's replies, so a filtered
 * extract would hide what the patient is responding to. Each message carries whatever
 * mark it holds, because the conversation list only says that a person is needed and
 * the message is where the reason lives.
 */
export function StaffThread({
  chatId,
  assistantMayReply,
  pauseSecondsRemaining,
  lastMessageAt,
  pollTick,
  onSetAssistant,
}: StaffThreadProps) {
  const [thread, setThread] = useState<Message[]>([]);
  const [reply, setReply] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Whether a staff post is in flight. State rather than a ref because the refetch
  // effect below has to *re-run* when this drops back to false: a poll tick that arrives
  // mid-post is deliberately left unhandled, and `lastMessageAt` has already stopped
  // changing by then. `pollTick` would wake the effect a second or two later anyway;
  // this wakes it the moment the post lands, which is when the read it was waiting on
  // became answerable.
  const [posting, setPosting] = useState(false);
  // The poll value this pane has already read the thread for — recorded when that read
  // *lands*, never when it is issued, so it means "accounted for" and not "attempted".
  // Every read files it on landing, the one that opens the conversation included: a read
  // that was only attempted accounts for nothing, and filing a value on the strength of
  // having asked is what leaves a pane blank behind an error banner — nothing was
  // loaded, and every later tick reports the very value already filed as handled.
  // Compared by identity, never by clock arithmetic: these strings are the server's own,
  // and turning them into a comparison of instants would put a browser's clock in charge
  // of whether a patient's message is ever shown.
  //
  // `undefined` is "nothing accounted for yet" and is deliberately *not* the same as
  // `null`. A conversation with no messages polls as `last_message_at: null`, which is a
  // real answer this pane must be able to record as handled — collapsing the two would
  // make the first message ever sent into an open conversation look like the value that
  // described the (empty) thread already loaded, and it would never be fetched. That is
  // the exact shape of the gap this whole effect exists to close, so the two states are
  // kept apart rather than both spelled `null`.
  const handledLastMessageAtRef = useRef<string | null | undefined>(undefined);
  // Numbers every read of this thread as it is issued — the one that opens a
  // conversation and the poll-driven ones alike, since either can be answered long after
  // the screen has moved past what it asked about.
  const issuedReadsRef = useRef(0);
  // The newest read whose answer the screen already reflects. An answer numbered at or
  // below it describes a thread older than what is displayed, so it is dropped rather
  // than applied — applying it would be a rewind. Three things move it, and together
  // they are the whole staleness rule:
  //   * a read landing, the ordinary case: nothing issued before it is wanted after it;
  //   * opening a conversation, which retires every read still in flight — each answers
  //     about a thread that is no longer on screen. A *number* and not a comparison of
  //     chat ids, because leaving a conversation and coming back to it is a second
  //     visit and not the same one: a read issued during the first visit still names the
  //     conversation now open, and only its number says it belongs to a thread this pane
  //     has since thrown away and reloaded;
  //   * a staff post landing, which puts a message on screen that no read issued before
  //     it can carry — so applying such an answer would take the reply straight back off
  //     for as long as a poll interval, in front of the person who just wrote it.
  // A read that *failed* deliberately does not move it: it put nothing on screen, so an
  // answer issued before it is still a correction rather than a rewind.
  const appliedThroughRef = useRef(0);
  // The read that opens a conversation, while it is still outstanding, and the poll
  // value it will file if it lands — null once it has settled either way. It is what
  // stops the effect below asking, on this conversation's very first tick, the question
  // already out. Keyed to the value rather than to "a read is in flight": a read that
  // hangs then holds up nothing but the tick reporting exactly what it went to fetch, so
  // a message arriving meanwhile is still read here instead of waiting on an answer that
  // may never come.
  const openingReadRef = useRef<{ accountsFor: string | null | undefined } | null>(
    null,
  );
  // Whether a staff post is in flight, as the *handler* can see it. A ref rather than
  // reading `posting`, because two clicks dispatched in one batch both run against the
  // render that preceded them: the state the first set has not committed, so a guard
  // reading it lets the second through, and the button is still painted enabled for the
  // same reason. Set synchronously here, it is true by the time the second call reads
  // it. `posting` stays as well — the two are not alternatives, since a ref cannot
  // repaint the button and state cannot stop a call made before React repaints.
  const postingRef = useRef(false);

  useEffect(() => {
    setThread([]);
    setReply("");
    setError(null);
    handledLastMessageAtRef.current = undefined;
    // Retired here, before the refetch effect below can run for the newly opened
    // conversation, since effects run in the order they are declared: every read issued
    // so far — for the conversation just closed, or for an earlier visit to this very
    // one — is now answering about a thread that is no longer on screen, and its number
    // is what says so however many poll ticks pass while it is in flight.
    appliedThroughRef.current = issuedReadsRef.current;
    openingReadRef.current = null;
    if (chatId === null) return;
    // The value the poll is reporting as this conversation opens, which is what this
    // read will account for. Captured here rather than read when the answer lands: a
    // message written while the read is out is not in its answer, so filing the value
    // that describes *that* message would lose it. Deliberately not a dependency of this
    // effect — it is a snapshot of the moment the read went out, and re-running the
    // reset above on every new poll value is the last thing this pane wants.
    const accountsFor = lastMessageAt;
    const generation = ++issuedReadsRef.current;
    openingReadRef.current = { accountsFor };
    void fetchThread(chatId)
      .then((messages) => {
        if (generation <= appliedThroughRef.current) return;
        appliedThroughRef.current = generation;
        openingReadRef.current = null;
        // The first marker is filed by this read, on landing, and by nothing else. The
        // effect below used to file the first value it saw on the assumption that this
        // read described it — an assumption a read that failed does not keep.
        handledLastMessageAtRef.current = accountsFor;
        setThread(messages);
      })
      .catch((err: unknown) => {
        if (generation <= appliedThroughRef.current) return;
        // Cleared with nothing filed as handled, which is what lets the effect below
        // take the read over on the next tick instead of leaving this pane empty behind
        // the banner for as long as nobody writes into the conversation.
        openingReadRef.current = null;
        // Without this the pane sits empty with nothing explaining why.
        setError(
          err instanceof Error ? err.message : "Could not load this conversation.",
        );
      });
  }, [chatId]);

  useEffect(() => {
    // `undefined` here means the caller is not feeding this pane the poll at all, which
    // is a different thing from a conversation the poll says is empty.
    if (chatId === null || lastMessageAt === undefined) return;
    if (handledLastMessageAtRef.current === lastMessageAt) return;
    const openingRead = openingReadRef.current;
    if (openingRead !== null && openingRead.accountsFor === lastMessageAt) {
      // The read that opened this conversation is already out for exactly this value,
      // and files it itself when it lands. Asking again here would only be the same
      // question twice. Note what this does *not* wait for: any other value, so a
      // message arriving while that read hangs is fetched below rather than held behind
      // an answer that may never arrive.
      return;
    }
    // Left unhandled while a staff post is in flight, so this tick is retried once it
    // lands: a refetch issued now would be answered from before the post was stored, and
    // replacing the thread with that answer would take the reply back off the screen for
    // a poll interval. Deferring is not a delay to anything — the value that triggered
    // this is still there to act on afterwards. The other half of that is in `handleSend`:
    // this stops a read being *issued* across the post, and retiring the outstanding
    // generations there is what discards one that was already out when it started.
    if (posting) return;

    // Numbered as it goes out, and judged by that number when it lands. Every tick that
    // is still owed a read issues one, deliberately: skipping while another is
    // outstanding makes this pane's liveness depend on that read completing, and a read
    // that hangs — a wedged connection, the case a retry exists for — would then stop
    // the refetch for as long as it hangs, or for good. That is the trade
    // `useConsolePoll` refuses for the same reason; only losers are discarded here too.
    const generation = ++issuedReadsRef.current;
    // The value this read goes out to account for, captured now rather than read when
    // the answer lands: by then the poll may be reporting a later message, and filing
    // *that* value would mark as handled a message this answer does not contain.
    const accountsFor = lastMessageAt;
    // Whether this read is standing in for an opening read that filed nothing, which is
    // the one case where a banner is on screen that this answer disproves.
    const takingOverOpeningRead = handledLastMessageAtRef.current === undefined;
    // Refetched whole rather than appended to, which is also what carries the *marks*:
    // a staff reply clears every clearable mark in the conversation, on messages already
    // on screen, and no append can express that. It is why this pane refetches after its
    // own post too, where ChatWindow's equivalent would have nothing to gain.
    void fetchThread(chatId)
      .then((messages) => {
        // Judged by its number, never by whether this effect has re-run since: with the
        // tick among its dependencies it re-runs every couple of seconds, and a read
        // outstanding across one of those is not stale — it is only slower than the
        // poll. What makes an answer wrong is something newer already being on screen,
        // and the number is what says so: a conversation opened or reopened since, a
        // later read already applied, or a staff reply this answer was composed before.
        if (generation <= appliedThroughRef.current) return;
        appliedThroughRef.current = generation;
        if (takingOverOpeningRead) {
          // The read that opened the conversation failed and left a banner; this answer
          // is the thread it could not load, so the banner goes with it.
          setError(null);
        }
        // Marked handled on the answer that actually arrived, and with the value it went
        // out for. Marking it where the read was *issued* made the marker mean
        // "attempted", which loses the message a failed read was fetching: the patient's
        // message is still the newest one afterwards, so the poll goes on reporting the
        // very time already filed as handled.
        handledLastMessageAtRef.current = accountsFor;
        setThread(messages);
      })
      // A failed refetch leaves the thread as it was, the tick unhandled so the next one
      // genuinely does try again, and its number unretired — it put nothing on screen,
      // so an answer issued before it is still a correction rather than a rewind. It
      // says nothing, because a banner for a blip a staff member cannot act on would
      // only teach them to ignore the one that matters.
      .catch(() => undefined);
  }, [chatId, lastMessageAt, pollTick, posting]);

  async function handleSend(): Promise<void> {
    const content = reply;
    if (chatId === null) return;
    if (!content.trim() || content.length > MAX_REPLY_LENGTH) return;
    // A second send while the first is still out would read the same box — the text
    // is only cleared once the post lands — and put the same sentence into the
    // patient's thread twice, stored server-side both times. Unlike the patient side,
    // where several turns in flight at once is a real thing a person does, nothing
    // about a staff reply wants two copies.
    if (postingRef.current) return;

    setError(null);
    postingRef.current = true;
    setPosting(true);
    try {
      const posted = await postStaffMessage(chatId, content);
      // Every read still in flight is retired here, because this reply is now on screen
      // and none of them can be carrying it: each was composed before the post existed,
      // so applying one would take the reply straight back off for as long as a poll
      // interval, in front of the person who just wrote it. The `posting` guard above
      // only stops a read being *issued* across the post; this is what discards one that
      // was already out when it started.
      appliedThroughRef.current = issuedReadsRef.current;
      // The response *is* the stored message, so the thread is extended from it rather
      // than refetched — which is what puts the reply on screen the moment it lands,
      // without waiting on a poll tick. The refetch the poll goes on to trigger replaces
      // the thread rather than adding to it, so this cannot end up shown twice; what
      // that refetch brings that this cannot is the marks it just cleared — which is why
      // the marker is deliberately *not* filed here, leaving that refetch still owed.
      setThread((prev) => [...prev, posted]);
      setReply("");
    } catch (err) {
      // What was typed stays in the box: the reply was not sent, and asking a staff
      // member to write it again is the one thing a failed send must not do.
      setError(err instanceof Error ? err.message : "Could not send that reply.");
    } finally {
      // Released on the way out however this ended, including the failure above: the
      // text is still in the box by design, and a latch left closed would leave a staff
      // member holding a reply they can no longer send.
      postingRef.current = false;
      setPosting(false);
    }
  }

  if (chatId === null) {
    return (
      <div data-testid="staff-no-thread" style={{ opacity: 0.5 }}>
        <p>Open a conversation to read it.</p>
      </div>
    );
  }

  return (
    <div>
      {/* Always shown, never only while something is wrong: a control that appears
          only in the silenced case makes a staff member infer the ordinary one from
          its absence. */}
      <label>
        <input
          type="checkbox"
          data-testid="assistant-switch"
          checked={assistantMayReply}
          onChange={(e) => onSetAssistant(e.target.checked)}
        />
        Assistant {assistantMayReply ? "on" : "off"}
      </label>
      {pauseSecondsRemaining !== null && (
        <p data-testid="pause-countdown" data-seconds={pauseSecondsRemaining}>
          Quiet for another {formatRemaining(pauseSecondsRemaining)}
        </p>
      )}
      <div data-testid="staff-thread">
        {thread.map((message) => (
          <MessageView
            key={message.id}
            sender={message.sender}
            content={message.content}
            citations={message.citations}
            grounded={message.grounded}
            mark={message.attention_mark}
          />
        ))}
      </div>
      <textarea
        aria-label="reply as staff"
        value={reply}
        onChange={(e) => setReply(e.target.value)}
        placeholder="Reply to this patient..."
      />
      {/* Disabled while the post is out so the send visibly *is* happening. That is
          what stops the second click being made at all; the handler's own guard is
          what stops one made anyway — a repeat click landing before React has
          repainted, or any call that never went through this button. */}
      <button disabled={posting} onClick={() => void handleSend()}>
        Send as staff
      </button>
      {reply.length > MAX_REPLY_LENGTH && (
        <p data-testid="staff-length-error" style={{ color: "red" }}>
          Reply is too long ({reply.length}/{MAX_REPLY_LENGTH} characters).
        </p>
      )}
      {error && <p data-testid="staff-error">{error}</p>}
    </div>
  );
}

export default StaffThread;
