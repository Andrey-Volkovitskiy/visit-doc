import { Bot, Stethoscope, User } from "lucide-react";
import type { AttentionMark, RequestOutcome } from "../lib/chatStream";
import { ATTENTION_MARK_LABEL } from "../lib/consoleApi";
import { OutcomeDisclosure } from "./OutcomeDisclosure";

interface MessageViewProps {
  sender: "patient" | "assistant" | "staff";
  content: string;
  /**
   * Whether this message begins a consecutive run from its sender (FR-014).
   *
   * Derived by the thread from the *previous* message's sender and passed in, rather
   * than worked out here: one message cannot see the one before it, and a component
   * that took the whole thread to answer a question about one message would be the
   * wrong shape for the job.
   *
   * Defaults to true so a message rendered on its own — the streaming bubble, a test —
   * carries its indicator rather than silently losing it.
   */
  startsBurst?: boolean;
  /**
   * What each request of this turn got — its question, its verdict, its citations.
   *
   * Null means no FAQ half ran: a booking reply is a real action's outcome, not a claim
   * about clinic policy, and a staff message was never retrieved against.
   */
  requestOutcomes?: RequestOutcome[] | null;
  /**
   * Whether to draw the outcome blocks at all.
   *
   * The staff console draws them; the patient pane does not. An outcome is evidence
   * about how an answer was produced — useful to a staff member auditing it, and the
   * clinic's working notes to a patient who asked a question and wants the answer.
   *
   * This is presentation, not access: the outcomes are in the payload either way, and
   * the session reading the patient pane owns the corpus and can read every entry of it
   * on the FAQ screen.
   */
  showOutcomes?: boolean;
  /**
   * Why this message needs a person, when something decided one is needed.
   *
   * Passed only by the staff side. The patient sees their own message plainly: a mark
   * is a note for whoever has to act on it, not a status the sender is owed.
   */
  mark?: AttentionMark | null;
  /**
   * Which sender counts as the reader's own, for this pane.
   *
   * Each pane answers it for its own reader, which is why it is a prop rather than a
   * rule this component could work out: the patient pane's reader is the patient
   * (FR-013) and the console's is a staff member (FR-023), and both requirements say
   * the same thing about different senders. The pane that renders the thread is the
   * only thing that knows who is reading it.
   *
   * Defaults to "patient" so a message rendered on its own keeps the patient pane's
   * reading, which is the one every existing caller meant.
   */
  readerIs?: "patient" | "staff";
}

/**
 * What each sender is called on screen.
 *
 * The patient's own messages are absent deliberately: they are the reader's own, and a
 * label would say nothing they do not already know. Neither label is a person's name —
 * there is no staff member to name, and a human-sounding one would invite the patient to
 * believe there is.
 */
const ROLE_LABEL: Record<string, string> = {
  assistant: "AI assistant",
  staff: "Staff",
};

/**
 * The sender indicator FR-014 groups by.
 *
 * A glyph rather than initials: the mockup draws a person's initials, and this app has
 * no name for the assistant or for staff that would not be an invention. It is
 * `aria-hidden` because it repeats what `role-label` already says in words for the two
 * senders that have one, and says nothing at all for the patient's own messages.
 */
const SENDER_ICON = {
  patient: User,
  assistant: Bot,
  staff: Stethoscope,
} as const;

const ICON_TONE: Record<string, string> = {
  patient: "bg-bubble-me text-accent-dark",
  assistant: "bg-accent text-surface",
  staff: "bg-surface-sunken text-ink-muted border border-rule",
};

/**
 * Renders one message by sender, reused for historical and in-progress messages.
 *
 * No derived "unanswered" treatment: a patient message with no reply yet is the
 * normal shape of a mid-burst message, not a failure signal.
 */
export function MessageView({
  sender,
  content,
  startsBurst = true,
  requestOutcomes,
  showOutcomes = false,
  mark,
  readerIs = "patient",
}: MessageViewProps) {
  const label = ROLE_LABEL[sender];
  // The reader's own side. Note what this does *not* do: it never puts two senders on
  // one side. In the patient pane the patient is opposite the assistant and staff
  // (FR-013); in the console a staff member is opposite the patient and the assistant
  // (FR-023). The third sender shares the far side in each case, which is what both
  // requirements ask for — each names one sender that must stand apart, not three that
  // must all differ.
  const mine = sender === readerIs;
  const Icon = SENDER_ICON[sender];

  return (
    <div
      data-testid="message"
      data-sender={sender}
      data-burst-start={startsBurst ? "true" : undefined}
      data-mine={mine ? "true" : undefined}
      className={[
        "flex min-w-0 items-end gap-3",
        startsBurst ? "mt-4 first:mt-0" : "mt-1",
        mine ? "flex-row-reverse" : "flex-row",
      ].join(" ")}
    >
      {startsBurst ? (
        <span
          data-testid="sender-icon"
          aria-hidden="true"
          className={`grid size-7 flex-none place-items-center rounded-full ${ICON_TONE[sender]}`}
        >
          <Icon className="size-3.5" />
        </span>
      ) : (
        // A spacer, so every bubble in a run keeps the same left edge. Not the icon made
        // invisible: an `aria-hidden` icon and an empty box read the same to everything
        // but the layout, and only one of them can be mistaken for a rendered indicator.
        <span aria-hidden="true" className="size-7 flex-none" />
      )}
      <div className="min-w-0 max-w-[82%]">
        <div
          className={[
            "rounded-md border px-3 py-2 text-base",
            mine
              ? "bg-bubble-me border-accent/20"
              : "bg-bubble-them border-rule-soft",
          ].join(" ")}
        >
          {label !== undefined && startsBurst && (
            <p
              data-testid="role-label"
              // `text-ink`, not `text-ink-muted`: with staff messages now taking the
              // reader's side in the console, this label can sit on `bubble-me`, where
              // `ink-muted` measures 4.32:1 and misses AA (FR-041). Measured, not
              // guessed — see T062's table.
              className="text-ink mb-0.5 text-xs font-semibold"
            >
              {label}
            </p>
          )}
          {/*
            `white-space: pre-wrap` stays an inline style on purpose. It is behaviour —
            it decides whether the newlines a patient typed survive — and a test can
            honestly assert it here. As the utility class `whitespace-pre-wrap` it
            becomes unassertable under jsdom, which computes nothing for a class name,
            so its existing test would have to be deleted rather than adapted.
          */}
          <p style={{ whiteSpace: "pre-wrap" }} className="break-words">
            {content}
          </p>
        </div>
        {/*
          The marker and everything behind it, for *this* message's own data — never a
          neighbour's (FR-026). The patient pane passes neither prop, so it renders
          nothing at all there: an outcome is the clinic's working note on how an answer
          was produced, and the console is where it is read (FR-019).
        */}
        {/*
          Rendered whenever a mark is passed, with no `showOutcomes` gate — which is
          what it always did. Only the staff side ever passes one: the patient sees
          their own message plainly, because a mark is a note for whoever has to act on
          it rather than a status the sender is owed.
        */}
        {mark !== null && mark !== undefined && (
          <p
            data-testid="attention-mark"
            data-mark={mark}
            className="text-attention mt-1 text-xs font-medium"
          >
            {ATTENTION_MARK_LABEL[mark]}
          </p>
        )}
        {showOutcomes && (
          <OutcomeDisclosure
            requestOutcomes={requestOutcomes ?? null}
            mark={mark ?? null}
          />
        )}
      </div>
    </div>
  );
}

export default MessageView;
