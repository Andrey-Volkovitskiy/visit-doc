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
   * When it advances past the value this pane last acted on, something was written into
   * the conversation that this pane did not write — a patient message arriving while a
   * staff member sits reading it — and the thread is refetched. Same mechanism as
   * `ChatWindow`'s prop of the same name, and for the same reason: it rides the one poll
   * that already runs rather than opening a second channel that would have to be kept in
   * step with the first.
   */
  lastMessageAt?: string | null;
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
  onSetAssistant,
}: StaffThreadProps) {
  const [thread, setThread] = useState<Message[]>([]);
  const [reply, setReply] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Whether a staff post is in flight. State rather than a ref because the refetch
  // effect below has to *re-run* when this drops back to false: a poll tick that arrives
  // mid-post is deliberately left unhandled, and nothing else would wake the effect to
  // retry it — `lastMessageAt` has already stopped changing by then.
  const [posting, setPosting] = useState(false);
  // The poll value this pane has already accounted for. Compared by identity, never by
  // clock arithmetic: these strings are the server's own, and turning them into a
  // comparison of instants would put a browser's clock in charge of whether a patient's
  // message is ever shown.
  //
  // `undefined` is "nothing accounted for yet" and is deliberately *not* the same as
  // `null`. A conversation with no messages polls as `last_message_at: null`, which is a
  // real answer this pane must be able to record as handled — collapsing the two would
  // make the first message ever sent into an open conversation look like the value that
  // described the (empty) thread already loaded, and it would never be fetched. That is
  // the exact shape of the gap this whole effect exists to close, so the two states are
  // kept apart rather than both spelled `null`.
  const handledLastMessageAtRef = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    setThread([]);
    setReply("");
    setError(null);
    handledLastMessageAtRef.current = undefined;
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
    handledLastMessageAtRef.current = lastMessageAt;

    let current = true;
    // Refetched whole rather than appended to, which is also what carries the *marks*:
    // a staff reply clears every clearable mark in the conversation, on messages already
    // on screen, and no append can express that. It is why this pane refetches after its
    // own post too, where ChatWindow's equivalent would have nothing to gain.
    void fetchThread(chatId)
      .then((messages) => {
        if (current) setThread(messages);
      })
      // A failed refetch leaves the thread as it was and says nothing: the next tick
      // tries again, and a banner for a blip a staff member cannot act on would only
      // teach them to ignore the one that matters.
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, [chatId, lastMessageAt, posting]);

  async function handleSend(): Promise<void> {
    const content = reply;
    if (chatId === null) return;
    if (!content.trim() || content.length > MAX_REPLY_LENGTH) return;

    setError(null);
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
      <button onClick={() => void handleSend()}>Send as staff</button>
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
