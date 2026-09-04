import { useRef, useState } from "react";
import type { Message } from "../lib/chatStream";
import { fetchThread, postStaffMessage } from "../lib/consoleApi";
import { isSendKey } from "../lib/sendKey";
import { useBusyLatch } from "../lib/useBusyLatch";
import { useThreadReads, type Banner } from "../lib/useThreadReads";
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
  // The banner carries *why* it is up, not just its words. Two things raise one here —
  // a thread that would not load and a reply that would not send — and only the first
  // is disproved by a later read landing. Told apart by a `kind` rather than by "has a
  // read ever landed", which was the same question for both and cleared a failed send's
  // banner while the unsent reply still sat in the box.
  const [banner, setBanner] = useState<Banner | null>(null);
  // The post latch, keyed by conversation rather than one shared flag: a single flag
  // belonged to whichever conversation posted last, so switching away mid-post left the
  // newly opened one with Send painted disabled and its own guard closed, for a post
  // that was never about it. See `useBusyLatch` for why it is a ref and a state both.
  const latch = useBusyLatch();
  const posting = chatId !== null && latch.isBusy(chatId);
  // Staff messages this pane posted that no read has published back to it yet. Kept
  // rather than retiring the reads that cannot carry them: retiring on a post threw
  // away the read that was still fetching the conversation itself, leaving the pane
  // holding a staff reply and nothing it was answering. Matched by the server's own id,
  // which the post response carries, so a message leaves this list the moment a read
  // accounts for it and can never be shown twice.
  const pendingPostsRef = useRef<Message[]>([]);
  // The conversation on screen right now, as an async handler can see it. A handler
  // captured `chatId` when it started; this is what it has moved on to.
  const chatIdRef = useRef(chatId);
  chatIdRef.current = chatId;

  /** Put a reply this pane just posted on screen, unless a read already brought it. */
  function showPosted(posted: Message): void {
    // Matched by the server's own id, which the post response carries. It can already
    // be here: leaving a conversation and coming back reloads the thread, and if the
    // post lands after that reload the reload already published it. Appending anyway
    // would show a staff member their own reply twice, in the conversation they wrote
    // it in, with nothing to remove the copy.
    if (!pendingPostsRef.current.some((p) => p.id === posted.id)) {
      pendingPostsRef.current = [...pendingPostsRef.current, posted];
    }
    setThread((previous) =>
      previous.some((message) => message.id === posted.id)
        ? previous
        : [...previous, posted],
    );
  }

  function applyRead(messages: Message[]): void {
    // A ref rather than state, and read outside any updater: React may run an updater
    // more than once for one update, and this decides what to keep as well as what to
    // show. Doing it twice would be harmless only by luck.
    const missing = pendingPostsRef.current.filter(
      (post) => !messages.some((message) => message.id === post.id),
    );
    pendingPostsRef.current = missing;
    setThread(missing.length === 0 ? messages : [...messages, ...missing]);
  }

  useThreadReads<Message[]>({
    chatId,
    lastMessageAt,
    pollTick,
    // Nothing pauses this pane's reads. A refetch answered from before a post was
    // stored used to take the reply back off the screen, which is what the pause was
    // for; `pendingPosts` keeps it on instead, and a pause that is no longer needed is
    // one more way for a conversation to stop refreshing.
    paused: false,
    // Refetched whole rather than appended to, which is also what carries the *marks*:
    // a staff reply clears every clearable mark in the conversation, on messages already
    // on screen, and no append can express that.
    read: (id, signal) => fetchThread(id, signal),
    onReset: () => {
      setThread([]);
      setReply("");
      setBanner(null);
      pendingPostsRef.current = [];
    },
    onLoaded: (messages) => {
      applyRead(messages);
      // This is the thread the failed opening read could not load, so the banner it
      // raised goes with it — and only that one. A banner about a reply that would not
      // send is not disproved by a thread that loaded, and clearing it would tell a
      // staff member their unsent message went through.
      setBanner((previous) => (previous?.kind === "read" ? null : previous));
    },
    onOpenFailed: (err) => {
      // Without this the pane sits empty with nothing explaining why.
      setBanner({
        kind: "read",
        text: err instanceof Error ? err.message : "Could not load this conversation.",
      });
    },
  });

  async function handleSend(): Promise<void> {
    const content = reply;
    const target = chatId;
    if (target === null) return;
    if (!content.trim() || content.length > MAX_REPLY_LENGTH) return;
    // A second send while the first is still out would read the same box — the text
    // is only cleared once the post lands — and put the same sentence into the
    // patient's thread twice, stored server-side both times. Unlike the patient side,
    // where several turns in flight at once is a real thing a person does, nothing
    // about a staff reply wants two copies. Keyed to this conversation: a post out for
    // another one says nothing about whether this reply has been sent.
    await latch.run(target, async () => {
      setBanner(null);
      try {
        const posted = await postStaffMessage(target, content);
        // Everything below touches this pane's state, so it runs only while the pane is
        // still showing the conversation posted to. Unguarded, a reply to one patient
        // was appended to whichever conversation the staff member had opened in the
        // meantime, and their draft in it cleared.
        if (chatIdRef.current !== target) return;
        // The response *is* the stored message, so the thread is extended from it rather
        // than refetched — which is what puts the reply on screen the moment it lands,
        // without waiting on a poll tick. It is held as a pending post until a read
        // publishes it back, so a read still in flight from before the post cannot take
        // it off again. What such a read brings that this cannot is the marks the post
        // just cleared, which is why nothing here files the poll value as handled.
        showPosted(posted);
        setReply("");
      } catch (err) {
        if (chatIdRef.current !== target) return;
        // What was typed stays in the box: the reply was not sent, and asking a staff
        // member to write it again is the one thing a failed send must not do.
        setBanner({
          kind: "send",
          text: err instanceof Error ? err.message : "Could not send that reply.",
        });
      }
    });
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
        onKeyDown={(e) => {
          if (isSendKey(e)) {
            e.preventDefault();
            void handleSend();
          }
        }}
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
      {banner && <p data-testid="staff-error">{banner.text}</p>}
    </div>
  );
}

export default StaffThread;
