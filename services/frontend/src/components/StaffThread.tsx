import { SendHorizontal } from "lucide-react";
import { useRef, useState } from "react";
import type { Message } from "../lib/chatStream";
import { fetchThread, postStaffMessage } from "../lib/consoleApi";
import { useBottomPin } from "../lib/useBottomPin";
import { isSendKey } from "../lib/sendKey";
import { useBusyLatch } from "../lib/useBusyLatch";
import { useThreadReads, type Banner } from "../lib/useThreadReads";
import { NO_DIRTY_REPORT, useDirtyReport } from "./adminSection";
import { ErrorBanner } from "./ErrorBanner";
import { MessageView } from "./MessageView";
import { Button } from "./ui/button";
import { Switch } from "./ui/switch";
import { Textarea } from "./ui/textarea";

// Matches `StaffMessageWrite.content`'s `max_length` in
// services/chat/src/chat/domain/schemas.py - checked here too so a staff member gets
// immediate feedback instead of a round trip to hit the same 422.
const MAX_REPLY_LENGTH = 2000;

/**
 * The length from which the character count is shown (FR-019a).
 *
 * Derived from this composer's own limit rather than imported from `ChatWindow`'s:
 * `MAX_MESSAGE_LENGTH` and `MAX_REPLY_LENGTH` each mirror their own endpoint's
 * `max_length` and are deliberately not merged (data-model.md), so a shared threshold
 * would couple two contracts that only happen to agree today.
 */
const CHAR_COUNT_FROM = MAX_REPLY_LENGTH - 200;

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
   * This conversation's booking-record version, from the same poll row (016 FR-016a).
   *
   * It changes when an act is recorded or settled, and the thread is refetched when it
   * does, exactly as for a new message. That is the only thing that re-reads a turn
   * which settled an act and then failed before writing a reply: no message moved
   * `lastMessageAt`, and without this the act would read "unknown" until one did.
   */
  bookingActsVersion?: number;
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
  /**
   * Report whether the reply box holds text that leaving would lose (015 FR-035b).
   *
   * The staff tab set unmounts this pane to show another section, and the unsent reply
   * goes with it — only `App`, which performs the switch, can hold one back. So the pane
   * reports, and **retracts on unmount**, exactly as the practitioner and FAQ editors
   * do. Optional: rendered on its own, the pane has nobody to report to.
   */
  onDirtyChange?: (dirty: boolean) => void;
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
  bookingActsVersion,
  pollTick,
  onSetAssistant,
  onDirtyChange = NO_DIRTY_REPORT,
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
  // Whether a read has come back for the conversation on screen. Waiting, arrived-empty
  // and failed are three different situations (FR-010a), and only the middle one gets
  // the empty-thread statement (FR-025a) — so it cannot be inferred from an empty array.
  const [threadLoaded, setThreadLoaded] = useState(false);
  // FR-015b puts this thread on FR-015a's exact terms, so it is not a second mechanism
  // that happens to agree: it is the same hook the patient thread uses.
  const threadScroll = useBottomPin<HTMLDivElement>();

  // Whitespace alone is nothing a staff member would miss. Reported up and retracted on
  // unmount by the same hook the admin editors use, since the tab switch that destroys
  // this pane is the one it guards.
  useDirtyReport(reply.trim() !== "", onDirtyChange);

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
    bookingActsVersion,
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
      setThreadLoaded(false);
      // A different conversation has no position to hold: it opens at its most recent
      // message, however far up the previous one had been scrolled.
      threadScroll.pin();
      setThread([]);
      setReply("");
      setBanner(null);
      pendingPostsRef.current = [];
    },
    onLoaded: (messages) => {
      setThreadLoaded(true);
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
        // Posting is not unbidden content: the staff member just acted, so their own
        // reply follows to the bottom whatever they had scrolled back to.
        threadScroll.pin();
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
      <div
        data-testid="staff-no-thread"
        className="text-ink-muted flex min-h-0 flex-1 items-center justify-center p-6 text-sm"
      >
        <p>Open a conversation to read it.</p>
      </div>
    );
  }

  const overLimit = reply.length > MAX_REPLY_LENGTH;
  const empty = reply.trim().length === 0;
  // Three reasons, one control. `posting` is the latch that stops a second copy of the
  // same sentence reaching a patient; the other two are FR-019b making the control's
  // appearance agree with what activating it would do.
  const sendDisabled = posting || empty || overLimit;
  const sendReason = overLimit
    ? "Reply is too long to send."
    : empty
      ? "Type a reply to send."
      : posting
        ? "Sending…"
        : null;

  return (
    <div className="flex min-h-0 flex-col">
      {/* Always shown, never only while something is wrong: a control that appears
          only in the silenced case makes a staff member infer the ordinary one from
          its absence. */}
      <div className="border-rule-soft border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium" id="assistant-switch-label">
            Assistant
          </span>
          {pauseSecondsRemaining !== null && (
            <p
              data-testid="pause-countdown"
              data-seconds={pauseSecondsRemaining}
              // One of FR-010b's two named exceptions: a deadline on a control, not a
              // record of when anything happened.
              className="border-rule text-ink-muted border-l pl-2 text-sm tabular-nums"
            >
              Quiet for another {formatRemaining(pauseSecondsRemaining)}
            </p>
          )}
          <span className="ml-auto flex items-center gap-2">
            <span
              className={`text-sm font-semibold ${
                assistantMayReply ? "text-accent-dark" : "text-ink-muted"
              }`}
            >
              {assistantMayReply ? "on" : "off"}
            </span>
            {/*
              The rendered position follows the server's answer rather than being set
              optimistically (FR-024): `checked` is the prop the poll re-reads, so two
              open tabs cannot disagree about a conversation one of them just took.
            */}
            <Switch
              data-testid="assistant-switch"
              aria-labelledby="assistant-switch-label"
              aria-describedby="assistant-explanation"
              checked={assistantMayReply}
              onCheckedChange={onSetAssistant}
            />
          </span>
        </div>
        {/*
          A permanently visible sentence, not a tooltip and not a `?` to discover
          (FR-024a). This is the one control on the page whose effect reaches a real
          patient immediately, so its explanation may not be the one that has to be
          found.
        */}
        <p
          id="assistant-explanation"
          data-testid="assistant-explanation"
          className="text-ink-muted mt-1.5 text-sm"
        >
          Turned off, the assistant stops replying to this patient and a person is
          expected to. The pause expires on its own; turning it back on ends it
          immediately.
        </p>
      </div>
      <div
        data-testid="staff-thread"
        ref={threadScroll.ref}
        onScroll={threadScroll.onScroll}
        role="log"
        // Distinct from the patient pane's live region, which is on screen beside it:
        // two regions sharing one accessible name leaves a screen-reader user unable to
        // tell which conversation just announced.
        aria-label="This patient's conversation"
        tabIndex={0}
        className="min-h-0 flex-1 overflow-y-auto p-4"
      >
        {/*
          An open conversation that has answered and holds nothing (FR-025a). Distinct
          from `staff-no-thread`, which means none is selected, and deliberately not the
          patient side's greeting — that exists to orient a first-time visitor, and a
          staff member needs neither the orientation nor the offer.
        */}
        {threadLoaded && thread.length === 0 && (
          <p
            data-testid="staff-empty-thread"
            className="text-ink-muted border-rule-soft rounded-md border border-dashed p-4 text-sm"
          >
            This conversation has no messages yet.
          </p>
        )}
        {thread.map((message, i) => (
          <MessageView
            key={message.id}
            sender={message.sender}
            content={message.content}
            startsBurst={i === 0 || thread[i - 1]!.sender !== message.sender}
            // This pane's reader is a staff member, so their own replies take the
            // reader's side and their own background (FR-023) — the mirror of what the
            // patient pane does with the patient's.
            readerIs="staff"
            requestOutcomes={message.request_outcomes}
            showOutcomes
            mark={message.attention_mark}
            bookingActs={message.booking_acts}
          />
        ))}
      </div>
      <div className="border-rule bg-surface flex flex-col gap-2 border-t px-4 py-3">
        <div className="flex items-end gap-2">
          <Textarea
            aria-label="reply as staff"
            aria-describedby={sendReason === null ? undefined : "staff-composer-reason"}
            rows={2}
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            onKeyDown={(e) => {
              if (isSendKey(e)) {
                e.preventDefault();
                void handleSend();
              }
            }}
            placeholder="Reply to this patient..."
            className="min-h-0 resize-none"
          />
          {/* Disabled while the post is out so the send visibly *is* happening. That is
              what stops the second click being made at all; the handler's own guard is
              what stops one made anyway — a repeat click landing before React has
              repainted, or any call that never went through this button. */}
          <Button
            disabled={sendDisabled}
            onClick={() => void handleSend()}
            aria-describedby={sendReason === null ? undefined : "staff-composer-reason"}
          >
            Send as staff
            <SendHorizontal aria-hidden="true" />
          </Button>
        </div>
        {overLimit ? (
          <p
            id="staff-composer-reason"
            data-testid="staff-length-error"
            className="text-attention text-xs font-medium"
          >
            Reply is too long ({reply.length}/{MAX_REPLY_LENGTH} characters).
          </p>
        ) : (
          <>
            {reply.length >= CHAR_COUNT_FROM && (
              <p data-testid="char-count" className="text-ink-muted text-xs">
                {reply.length}/{MAX_REPLY_LENGTH}
              </p>
            )}
            {sendReason !== null && (
              <p id="staff-composer-reason" className="sr-only">
                {sendReason}
              </p>
            )}
          </>
        )}
        {banner && <ErrorBanner testId="staff-error" message={banner.text} />}
      </div>
    </div>
  );
}

export default StaffThread;
