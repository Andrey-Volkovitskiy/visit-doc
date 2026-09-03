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
  // Whether a poll-driven read is still outstanding. Since the effect below now re-runs
  // on every tick, a tick arriving mid-read would otherwise ask the same question a
  // second time; it is skipped instead, and whatever the outstanding answer does not
  // cover is still unhandled when it arrives, so the tick after it acts on it.
  const refetchInFlightRef = useRef(false);
  // The conversation this pane is showing, as a read that closed over an earlier one can
  // see it. A poll-driven read is judged stale by *this*, and only by this: `chatId`
  // inside such a callback is the conversation the read was issued for, and comparing
  // the two is the whole question of whether its answer still belongs on screen.
  // Unmounting needs no guard of its own — React drops a state update for a component
  // that is gone.
  const shownChatIdRef = useRef<string | null>(chatId);
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
    // Set before the refetch effect below can run for the newly opened conversation,
    // since effects run in the order they are declared: a read still outstanding for the
    // one just closed now compares unequal and its answer is dropped, where a read
    // issued from here on compares equal however many poll ticks pass while it is in
    // flight.
    shownChatIdRef.current = chatId;
    if (chatId === null) return;
    let current = true;
    void fetchThread(chatId)
      .then((messages) => {
        if (current) setThread(messages);
      })
      .catch((err: unknown) => {
        // Without this the pane sits empty with nothing explaining why.
        if (current) {
          setError(
            err instanceof Error
              ? err.message
              : "Could not load this conversation.",
          );
        }
      });
    return () => {
      current = false;
    };
  }, [chatId]);

  useEffect(() => {
    // `undefined` here means the caller is not feeding this pane the poll at all, which
    // is a different thing from a conversation the poll says is empty.
    if (chatId === null || lastMessageAt === undefined) return;
    if (handledLastMessageAtRef.current === lastMessageAt) return;
    if (handledLastMessageAtRef.current === undefined) {
      // The first value seen for this conversation describes the thread the effect above
      // has just loaded, so there is nothing new in it to fetch.
      handledLastMessageAtRef.current = lastMessageAt;
      return;
    }
    // Left unhandled while a staff post is in flight, so this tick is retried once it
    // lands: a refetch issued now would be answered from before the post was stored, and
    // replacing the thread with that answer would take the reply back off the screen for
    // a poll interval. Deferring is not a delay to anything — the value that triggered
    // this is still there to act on afterwards.
    if (posting) return;
    // One read at a time; see the ref's own note.
    if (refetchInFlightRef.current) return;

    refetchInFlightRef.current = true;
    // Refetched whole rather than appended to, which is also what carries the *marks*:
    // a staff reply clears every clearable mark in the conversation, on messages already
    // on screen, and no append can express that. It is why this pane refetches after its
    // own post too, where ChatWindow's equivalent would have nothing to gain.
    void fetchThread(chatId)
      .then((messages) => {
        // Judged by the conversation it was issued for, never by whether this effect has
        // re-run since it was: with the tick among its dependencies it re-runs every
        // couple of seconds, and a read outstanding across one of those is not stale —
        // it is only slower than the poll. Discarding it for that would waste every read
        // that crosses a tick boundary, and against a backend slower than the interval
        // the thread would never update at all. Closing the conversation is what makes
        // an answer wrong, and it is what this compares.
        if (shownChatIdRef.current !== chatId) return;
        // Marked handled here, on the answer that actually arrived, and not up at the
        // point the read was issued. Marking it there made the marker mean "attempted",
        // which loses the message a failed read was fetching: the patient's message is
        // still the newest one afterwards, so the poll goes on reporting the very time
        // already filed as handled, and the staff member would not see it until they
        // clicked away and back.
        handledLastMessageAtRef.current = lastMessageAt;
        setThread(messages);
      })
      // A failed refetch leaves the thread as it was, and the tick unhandled so the next
      // one genuinely does try again. It says nothing, because a banner for a blip a
      // staff member cannot act on would only teach them to ignore the one that matters.
      .catch(() => undefined)
      .finally(() => {
        // Cleared whether or not this pane still wants the answer: a conversation closed
        // mid-read must not leave the next one's refetch blocked forever.
        refetchInFlightRef.current = false;
      });
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
      // The response *is* the stored message, so the thread is extended from it rather
      // than refetched — which is what puts the reply on screen the moment it lands,
      // without waiting on a poll tick. The refetch the poll goes on to trigger replaces
      // the thread rather than adding to it, so this cannot end up shown twice; what
      // that refetch brings that this cannot is the marks it just cleared.
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
