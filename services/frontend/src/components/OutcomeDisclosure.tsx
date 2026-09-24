import { Info, TriangleAlert } from "lucide-react";
import { useState } from "react";
import type {
  AttentionMark,
  FaqVerdict,
  RequestOutcome,
} from "../lib/chatStream";

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
   * Why this message needs a person, when something decided one is needed.
   *
   * Read here for the marker's *state* only (FR-026a). The mark's own words stay
   * rendered on the message by `MessageView`, unexpanded: it is the one thing in this
   * block a staff member must be able to see while scanning a thread, and putting it
   * behind a click would mean opening every marker to find the conversation that needs
   * them.
   */
  mark: AttentionMark | null;
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
    "Not answered: this session's documents hold no entries to search — forwarded to staff.",
  abstained_empty_pool:
    "Not answered: nothing in the clinic's documents came back for this question — forwarded to staff.",
  abstained_similarity_floor:
    "Not answered: what came back was too loosely related to stand on — forwarded to staff.",
  abstained_rerank_floor:
    "Not answered: the closer reading found nothing that actually addressed it — forwarded to staff.",
};

function abstentionReason(verdict: FaqVerdict): string {
  // A verdict this build does not recognise still reports the one thing every
  // abstention has in common, rather than rendering `undefined`.
  return (
    ABSTENTION_REASON[verdict] ??
    "Not answered from the clinic's documents — forwarded to staff."
  );
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
 * The evidence marker for one message, and the block it opens.
 *
 * **Its state is derived from the message it sits on, never from a neighbour's**
 * (FR-026). Outcomes are written to the assistant's reply, because they describe what
 * the assistant did; an attention mark is written to the patient's message, because
 * that is the thing a person has to act on. A turn may therefore put a marker on both
 * of its messages, each describing its own. Pairing a message with the reply that
 * follows it was rejected outright: a patient message may have no reply yet, may be
 * superseded, or may be one of a burst, and each of those makes "the reply to this
 * message" ambiguous.
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
  mark,
}: OutcomeDisclosureProps) {
  const [expanded, setExpanded] = useState(false);

  const outcomes = requestOutcomes ?? [];
  const hasMark = mark !== null;
  if (outcomes.length === 0 && !hasMark) return null;

  // Something in this message is still owed to a person when it carries a mark or any
  // request that went unanswered. A message holding one answered and one unanswered
  // request takes this reading, not the gentler one (FR-026a).
  const needsPerson =
    hasMark || outcomes.some((outcome) => outcome.answer === null);
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
        aria-label={
          needsPerson
            ? "Why this message needs a person"
            : "What this answer drew on"
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
          {outcomes.length === 0 ? (
            // No FAQ half ran for this message — it is a patient message carrying a
            // mark. Saying so beats rendering an empty request list, which would claim
            // a half ran and answered nothing.
            <p className="text-ink-muted text-sm">
              Nothing was retrieved for this message; it is marked for a person.
            </p>
          ) : (
            outcomes.map((outcome) => (
              <OutcomeView key={outcome.position} outcome={outcome} />
            ))
          )}
          {/*
            FR-031. The backend does not record what a booking request actually did, so
            this says so — as a deliberate gap, not as an error, an empty result or a
            failure. Rendering an empty list here would assert that the turn booked
            nothing, which is a claim nobody can make.
          */}
          <p
            data-testid="booking-outcome-stub"
            className="border-rule text-ink-muted mt-3 border-t border-dashed pt-2 text-xs"
          >
            Booking outcome: not yet recorded. When it is, this will say what the turn
            checked, booked, moved or cancelled.
          </p>
        </div>
      )}
    </div>
  );
}

export default OutcomeDisclosure;
