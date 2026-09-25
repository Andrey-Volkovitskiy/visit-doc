import { Info, TriangleAlert } from "lucide-react";
import { useState } from "react";
import type {
  BookingAct,
  BookingActOperation,
  BookingActOutcome,
  FaqVerdict,
  RequestOutcome,
} from "../lib/chatStream";
import { dayAndTime } from "../lib/localTime";

interface OutcomeDisclosureProps {
  /**
   * What each request of this turn got, or null when no FAQ half ran.
   *
   * Null and `[]` are not the same thing and neither is treated as the other: null
   * means no retrieval happened — a booking reply, a hand-off, a staff message — while
   * an empty list would mean a half that ran and produced nothing, which the server
   * never sends.
   */
  requestOutcomes: RequestOutcome[] | null;
  /**
   * Whether anything in the turn this marker stands for called a person (FR-026a).
   *
   * A boolean rather than the mark itself, because a turn may hold several marked
   * messages and no single one of them is "the" mark — a value that named one would be
   * describing a message this marker is not only about. The *reason* is not wanted
   * here in any case: the mark's own words stay rendered on the message that carries
   * them by `MessageView`, unexpanded, because that is the one thing a staff member
   * must see while scanning a thread rather than by opening every marker in it.
   */
  marked: boolean;
  /**
   * What the assistant tried to do to the schedule for this message, in the order
   * tried — or null when it tried nothing (spec 016).
   *
   * Only a patient message ever carries a list: the act is recorded on the message the
   * turn was answering, because that message exists in every case where the act does,
   * including the turn that failed before replying. Null is the whole of "nothing was
   * attempted", and it renders nothing — no line saying so (FR-020).
   */
  bookingActs: BookingAct[] | null;
}

/**
 * Why one request went unanswered, in words, per verdict.
 *
 * Four wordings for four verdicts, because they call for four different things: an
 * empty corpus is somebody's cue to write an entry; a pool that came back empty, a
 * similarity floor and a rerank floor each say the corpus has something but not this.
 * Collapsing them into one sentence would undo exactly what spec 008 split apart when
 * it replaced a boolean `grounded` with a verdict naming the gate that stopped the turn.
 */
const ABSTENTION_REASON: Record<string, string> = {
  abstained_empty_corpus:
    "Not answered: the FAQ is empty, so the AI assistant has nowhere to look — forwarded to staff.",
  abstained_empty_pool:
    "Not answered: nothing in the FAQ came up for this question — forwarded to staff.",
  abstained_similarity_floor:
    "Not answered: only loosely related FAQ entries came up — forwarded to staff.",
  abstained_rerank_floor:
    "Not answered: no FAQ entry actually answered it — forwarded to staff.",
};

function abstentionReason(verdict: FaqVerdict): string {
  // A verdict this build does not recognise still reports the one thing every
  // abstention has in common, rather than rendering `undefined`.
  return (
    ABSTENTION_REASON[verdict] ??
    "Not answered from the clinic's FAQ — forwarded to staff."
  );
}

/**
 * A scheduler refusal code, in words a staff member can act on.
 *
 * The codes are the scheduler's closed set — `ChangeFailureReason` in
 * `shared_models.scheduling`, whose twelve include booking's eight under the same
 * strings — so one table serves all three operations. Worded for the person reading the
 * record, not for the patient: `_EXPLANATION_BY_REASON` in the chat service's
 * scheduling tools is what the patient was told, and says "you".
 */
const REFUSAL_REASON: Record<string, string> = {
  practitioner_busy: "the practitioner already had something booked then",
  patient_busy: "the patient already had an appointment overlapping that time",
  outside_schedule: "outside the practitioner's working hours",
  off_grid: "not one of the fixed appointment start times",
  in_past: "that time had already passed",
  beyond_horizon: "further ahead than appointments can be booked",
  practitioner_not_found: "no such practitioner at this clinic",
  patient_not_found: "this chat has no patient record",
  appointment_not_found: "no such appointment on the patient's record",
  already_cancelled: "the appointment was already cancelled",
  already_started: "the appointment had already started",
  stale_confirmation: "the appointment had changed since it was described",
};

/** The verb an unknown outcome is hedged with: "may or may not have been …". */
const PAST_PARTICIPLE: Record<BookingActOperation, string> = {
  book: "booked",
  reschedule: "moved",
  cancel: "cancelled",
};

/** How a done act is headed, per operation. */
const DONE_HEADING: Record<BookingActOperation, string> = {
  book: "Booked",
  reschedule: "Moved",
  cancel: "Cancelled",
};

/** Said for a name the record does not hold — never left blank, never guessed. */
const UNNAMED_PRACTITIONER = "a practitioner not named in the record";

/** "Andreas Vesalius, Tuesday 12 January 2027 at 10:00". */
function nameAndTime(name: string | null, localDateTime: string): string {
  return `${name ?? UNNAMED_PRACTITIONER}, ${dayAndTime(localDateTime)}`;
}

/**
 * What an act concerned: the appointment, and for a move both where it was and where
 * it went (FR-019).
 *
 * A move with one practitioner on both sides names them once; a move between two names
 * both, since which practitioner it now sits with is half of what changed.
 *
 * "One practitioner" is decided by a *known* name matching, never by two absences: the
 * wire carries no ids, and two names the record does not hold say nothing about whether
 * they are the same person. Folding them into one would present a move between two
 * practitioners as a move with one, so each side is named for what the record holds.
 */
function subject(act: BookingAct): string {
  const to = nameAndTime(act.practitioner_full_name, act.starts_at);
  if (act.operation !== "reschedule") return to;
  // A reschedule always records where it moved from. Should one ever arrive without,
  // it still says that it moved rather than inventing a time.
  const fromWhen =
    act.previous_starts_at === null
      ? "a time not in the record"
      : dayAndTime(act.previous_starts_at);
  const fromName = act.previous_practitioner_full_name;
  if (fromName !== null && fromName === act.practitioner_full_name) {
    return `${fromName}, ${fromWhen} → ${dayAndTime(act.starts_at)}`;
  }
  return `${fromName ?? UNNAMED_PRACTITIONER}, ${fromWhen} → ${to}`;
}

/**
 * One act as one sentence, per the wording table in console-ui.md.
 *
 * Every outcome opens with its own words, so none of them is carried by colour alone
 * (FR-019). An unknown one hedges rather than reassuring: it says the change may or may
 * not have happened and sends the reader to the schedule, and it never says nothing
 * changed — a timeout does not prove that.
 */
function actSentence(act: BookingAct, outcome: BookingActOutcome): string {
  const what = subject(act);
  switch (outcome) {
    case "done":
      return `${DONE_HEADING[act.operation]}: ${what}`;
    case "unchanged":
      return `No change needed: ${what}`;
    case "refused": {
      // A code this build does not know still says who refused it, rather than
      // rendering `undefined` or the raw code.
      const reason =
        act.refusal_reason === null ? undefined : REFUSAL_REASON[act.refusal_reason];
      return reason === undefined
        ? `Refused by the scheduler: ${what}. Nothing was changed.`
        : `Refused (${reason}): ${what}. Nothing was changed.`;
    }
    case "not_sent":
      return `Not sent: ${what}. Nothing was changed.`;
    case "unknown":
      return (
        `Outcome unknown: ${what} may or may not have been ` +
        `${PAST_PARTICIPLE[act.operation]}. Check the schedule.`
      );
  }
}

/**
 * The outcome as the reader must take it: a null — an act never settled — is unknown
 * (FR-012b), whether its turn is still running or ended before the answer came.
 */
function settledAs(act: BookingAct): BookingActOutcome {
  return act.outcome ?? "unknown";
}

/**
 * One request's block: what was asked, and either what the answer stood on or that
 * nobody answered it and staff now have it.
 *
 * The degraded marker sits here rather than on the message, because the verdict does:
 * a marker on the message would be a claim about the requests it does not describe.
 */
function OutcomeView({ outcome }: { outcome: RequestOutcome }) {
  const degraded = outcome.verdict === "answered_unreranked";
  const unanswered = outcome.answer === null;
  return (
    <div
      data-testid="request-outcome"
      data-position={outcome.position}
      data-verdict={outcome.verdict}
      className={[
        "mb-3 border-l-2 pl-3 last:mb-0",
        // Never colour alone (FR-030): the border is a tint, and the block below states
        // in words that nothing answered this. Either carries the difference on its own.
        unanswered ? "border-attention" : "border-rule",
      ].join(" ")}
    >
      <p data-testid="outcome-question" className="text-ink text-sm font-medium">
        {outcome.question}
      </p>
      {degraded && (
        <p
          data-testid="verdict-mark"
          title={
            "Produced without the reranking stage, so this answer rests on more, " +
            "less precisely selected clinic documents than usual."
          }
          className="text-ink-muted mt-0.5 text-xs"
        >
          Answered without reranking
        </p>
      )}
      {unanswered ? (
        <p data-testid="outcome-unanswered" className="text-attention mt-1 text-sm">
          {abstentionReason(outcome.verdict)}
        </p>
      ) : (
        // No emptiness check: an answered request cites what it stood on, which is at
        // least one chunk, and a guard here would read as a case that can happen.
        <ul data-testid="citations" className="text-ink-muted mt-1 text-sm">
          {outcome.citations.map((citation) => (
            <li
              key={`${citation.entry_id}-${citation.chunk_index}`}
              className="border-rule-soft mt-1 border-l-2 pl-2"
            >
              {citation.chunk_text}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * One act's line. The hook's `data-outcome` carries the outcome as read — a null is
 * attributed as `unknown`, never as a sixth state — and the words carry it too.
 */
function BookingActView({ act }: { act: BookingAct }) {
  const outcome = settledAs(act);
  const unknown = outcome === "unknown";
  return (
    <li
      data-testid="booking-act"
      data-operation={act.operation}
      data-outcome={outcome}
      className={[
        "mt-1 border-l-2 pl-3 first:mt-0",
        // Tinted only where a person is owed a check, and never by the tint alone:
        // "Outcome unknown" opens the sentence either way.
        unknown ? "border-attention text-attention" : "border-rule text-ink",
      ].join(" ")}
    >
      {actSentence(act, outcome)}
    </li>
  );
}

/**
 * The evidence marker for one message, and the block it opens.
 *
 * **One marker per turn, not per message** — narrowing FR-026, which had it derive from
 * the message it sits on and nothing else. The two halves of a turn are stored apart:
 * outcomes on the assistant's reply, because they describe what the assistant did; the
 * attention mark on the patient's message, because that is the thing a person has to
 * act on; booking acts there too (spec 016), so a turn that booked and then failed
 * before replying still has somewhere to say so. Each deriving its own state put two
 * red markers on one corpus gap, which reads as two problems needing two people.
 *
 * FR-026 rejected pairing a message with the reply that follows it, and was right to:
 * a patient message may have no reply yet, may be superseded, or may be one of a burst,
 * so "the reply to this message" is not a thing row order can answer. What changed is
 * that nothing here pairs anything — `lib/turns.ts` reads `reply_to_message_ids`, which
 * the server writes with the reply and which names its turn's questions outright. This
 * component still renders only what it is handed; deciding what a turn holds is that
 * module's job, and it is not row order's.
 *
 * The open/closed boolean is **owned here, not lifted**. Nothing else reads it, and
 * FR-028's "more than one open at once" falls out for free when each instance holds its
 * own — a `Set` in `StaffThread` would put state in a component with no other interest
 * in it. It survives the 2-second poll repaint because the thread is keyed by message
 * id: the poll replaces the array, React reconciles to the same instances, and the
 * state rides along. Keyed by array index it would not, and an arriving message would
 * shift every open block onto a different message.
 */
export function OutcomeDisclosure({
  requestOutcomes,
  marked,
  bookingActs,
}: OutcomeDisclosureProps) {
  const [expanded, setExpanded] = useState(false);

  const outcomes = requestOutcomes ?? [];
  const acts = bookingActs ?? [];
  if (outcomes.length === 0 && !marked && acts.length === 0) return null;

  // Something in this message is still owed to a person when it carries a mark, any
  // request that went unanswered, or any act whose outcome nobody knows. A message
  // holding one answered and one unanswered request takes this reading, not the
  // gentler one (FR-026a). A refused or not-sent act does not on its own: the first
  // was already reported to the patient and the second is known to have changed
  // nothing, whereas an unknown one may have moved the schedule unseen (016 FR-018).
  const needsPerson =
    marked ||
    outcomes.some((outcome) => outcome.answer === null) ||
    acts.some((act) => settledAs(act) === "unknown");
  const state = needsPerson ? "needs-person" : "served";
  const Icon = needsPerson ? TriangleAlert : Info;

  return (
    <div className="mt-1">
      <button
        type="button"
        data-testid="outcome-marker"
        data-outcome-state={state}
        aria-expanded={expanded}
        // The state in words, not only in a colour and an attribute (FR-006): the
        // attribute is what a test reads, this is what a reader hears.
        //
        // A served message holding only acts is the patient's own message, which answered
        // nothing, so it is named for the booking record it carries. One holding request
        // outcomes keeps the answer's name even if it also held acts — which does not
        // happen: outcomes are stored on the reply and acts on the patient's message.
        aria-label={
          needsPerson
            ? "Why this message needs a person"
            : outcomes.length > 0
              ? "What this answer drew on"
              : "What the assistant did to the schedule"
        }
        onClick={() => setExpanded((open) => !open)}
        className={[
          "grid size-5 place-items-center rounded-full border",
          needsPerson
            ? "border-attention bg-attention-wash text-attention"
            : "border-accent bg-accent-wash text-accent-dark",
        ].join(" ")}
      >
        <Icon className="size-3" aria-hidden="true" />
      </button>
      {expanded && (
        <div
          className={[
            "bg-surface-sunken border-rule mt-2 max-w-[82%] rounded-md border border-l-3 p-3 text-sm",
            needsPerson ? "border-l-attention" : "border-l-accent",
          ].join(" ")}
        >
          {outcomes.length > 0
            ? outcomes.map((outcome) => (
                <OutcomeView key={outcome.position} outcome={outcome} />
              ))
            : acts.length === 0 && (
                // No FAQ half ran and nothing was attempted — a patient message carrying
                // only a mark. Saying so beats rendering an empty request list, which
                // would claim a half ran and answered nothing. A message with acts has
                // them to show instead.
                <p className="text-ink-muted text-sm">
                  Nothing was retrieved for this message.
                </p>
              )}
          {/*
            Present only when something was attempted. 015's "not yet recorded" line is
            gone and nothing replaces it (016 FR-020): with acts now recorded, the absence
            of this list is what says no change to the schedule was attempted.
          */}
          {acts.length > 0 && (
            <ul
              className={[
                "text-sm",
                outcomes.length > 0 ? "border-rule mt-3 border-t pt-2" : "",
              ].join(" ")}
            >
              {acts.map((act, index) => (
                // Keyed by position: the list is append-only in attempt order and an
                // act has no id on the wire, so its index is its identity.
                <BookingActView key={index} act={act} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export default OutcomeDisclosure;
