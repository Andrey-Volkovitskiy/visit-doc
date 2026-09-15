"""Serving metrics: what each labelled question got, and what each abstention was worth.

Two zero-target shares that share part of a numerator and differ in denominator.

- **Unserved-answerable share** (C1) asks how much of what could be served was not. Its
  denominator comes from the label: every `faq_question` labelled answerable in a case
  whose turn was permitted to answer. A turn that was handed off or silenced was not -
  not answering there is the specified behaviour - so those cases leave the denominator
  and are listed by id. An `unknown` request escalates without silencing, so a case
  pairing one with a question stays. Its numerator is every such request that did not
  get an answered verdict, by cause: the turn abstained on it, the case was unaligned so
  it never became an outcome, or it aligned but was produced under another intent.
- **Wrong-abstention share** (C2) asks how often an abstention was wrong: of every
  abstention the run produced, those aligned to a labelled-answerable request.

`answered_unreranked` is an answer for both, and is listed separately as a degraded
answer. The verdict distribution counts every outcome the run produced across all six
verdicts, zeros included, because which gate stopped a request is what says what to fix.

Every case-scoped exclusion, and `handed_off_turn`, sets a case aside from all three.
"""

from collections.abc import Sequence
from enum import StrEnum

from chat.domain.schemas import FaqVerdict, IntentLabel, RequestOutcome
from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from golden_harness.cases import Case, LabelledRequest
from golden_harness.record import CaseRun, ExclusionReason
from golden_harness.scoring.alignment import AlignmentState, RunAlignment
from golden_harness.scoring.metric import Exclusions, Metric

UNSERVED_ANSWERABLE_SHARE = "unserved_answerable_share"
WRONG_ABSTENTION_SHARE = "wrong_abstention_share"
VERDICT_DISTRIBUTION = "verdict_distribution"
SERVING_METRICS = (
    UNSERVED_ANSWERABLE_SHARE,
    WRONG_ABSTENTION_SHARE,
    VERDICT_DISTRIBUTION,
)

# The exclusions that mean the turn was not permitted to answer (FR-031a), as opposed
# to the ones that mean nothing was measured. Both leave C1's denominator; only these
# are listed by name.
_NOT_PERMITTED: frozenset[ExclusionReason] = frozenset(
    {ExclusionReason.HANDED_OFF_TURN, ExclusionReason.SILENCED_TURN}
)


class UnservedCause(StrEnum):
    """Why a labelled-answerable request did not get an answered verdict.

    `misclassified` is a request whose case aligned by count but whose produced request
    at that position was not a `faq_question`, so the FAQ half never received it.
    """

    ABSTAINED = "abstained"
    LOST_TO_COUNT_MISMATCH = "lost_to_count_mismatch"
    MISCLASSIFIED = "misclassified"


class StoppingGate(StrEnum):
    """Where an abstention stopped - the values of `faq.verdict`'s `blocked_gate`."""

    EMPTY_CORPUS = "empty_corpus"
    EMPTY_POOL = "empty_pool"
    SIMILARITY_FLOOR = "similarity_floor"
    RERANK_FLOOR = "rerank_floor"

    @classmethod
    def of(cls, verdict: FaqVerdict) -> "StoppingGate":
        """Name the gate an abstained verdict stopped at.

        Raises: ValueError for an answered verdict, which stopped nowhere.
        """
        gates = {
            FaqVerdict.ABSTAINED_EMPTY_CORPUS: cls.EMPTY_CORPUS,
            FaqVerdict.ABSTAINED_EMPTY_POOL: cls.EMPTY_POOL,
            FaqVerdict.ABSTAINED_SIMILARITY_FLOOR: cls.SIMILARITY_FLOOR,
            FaqVerdict.ABSTAINED_RERANK_FLOOR: cls.RERANK_FLOOR,
        }
        if verdict not in gates:
            raise ValueError(f"{verdict.value} is not an abstention")
        return gates[verdict]


class UnservedRequest(BaseModel):
    """A labelled-answerable request behind C1's numerator.

    `position` is the labelled position. `question` is the produced request's text -
    the outcome's `question`, or the produced segment's text for a misclassified one -
    and the patient's message for a request lost to a count mismatch, which has no
    produced counterpart. `gate` is set exactly when the cause is `abstained`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    position: int
    question: str
    cause: UnservedCause
    gate: StoppingGate | None

    @model_validator(mode="after")
    def _a_gate_exactly_for_an_abstention(self) -> "UnservedRequest":
        """Refuse a gate on anything but an abstention, or an abstention without one."""
        if (self.cause is UnservedCause.ABSTAINED) != (self.gate is not None):
            raise ValueError("a gate is named exactly for an abstained request")
        return self


class Abstention(BaseModel):
    """A produced abstention behind C2's numerator, with the gate it stopped at."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    position: int
    question: str
    gate: StoppingGate


class Answer(BaseModel):
    """A produced answer listed for what it rests on: degraded, or given on a gap."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str
    position: int
    question: str
    verdict: FaqVerdict


class VerdictDistribution(BaseModel):
    """A count of the run's outcomes per `FaqVerdict`, every verdict present.

    `excluded` counts the cases set aside, by reason, since an excluded case's outcomes
    are not counted at all.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    counts: dict[FaqVerdict, int]
    excluded: Exclusions

    @model_validator(mode="after")
    def _every_verdict_is_counted(self) -> "VerdictDistribution":
        """Refuse a distribution missing a verdict, or with a negative count."""
        missing = sorted(v.value for v in FaqVerdict if v not in self.counts)
        if missing:
            raise ValueError(f"the distribution has no count for {missing}")
        if any(count < 0 for count in self.counts.values()):
            raise ValueError("verdict counts cannot be negative")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total(self) -> int:
        """Return how many outcomes were counted."""
        return sum(self.counts.values())

    def metrics(self) -> list[Metric]:
        """Return one share per verdict, in `FaqVerdict` order, each over the total."""
        return [
            Metric(
                name=f"{VERDICT_DISTRIBUTION}.{verdict.value}",
                numerator=self.counts[verdict],
                denominator=self.total,
                excluded=self.excluded,
            )
            for verdict in FaqVerdict
        ]


class ServingScores(BaseModel):
    """The serving metrics over a run, and the requests behind every non-zero value.

    `unserved_by_cause` carries every cause, zeros included, and sums to C1's
    numerator. `not_permitted_to_answer` lists the handed-off or silenced cases whose
    labelled-answerable requests left C1's denominator.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    unserved_answerable_share: Metric
    unserved_by_cause: dict[UnservedCause, int]
    unserved: list[UnservedRequest]
    not_permitted_to_answer: list[str]
    wrong_abstention_share: Metric
    wrong_abstentions: list[Abstention]
    degraded_answers: list[Answer]
    answers_on_labelled_gaps: list[Answer]
    verdict_distribution: VerdictDistribution

    def metrics(self) -> list[Metric]:
        """Return C1, C2 and the per-verdict shares, in report order."""
        return [
            self.unserved_answerable_share,
            self.wrong_abstention_share,
            *self.verdict_distribution.metrics(),
        ]


def score_serving(
    cases: Sequence[Case], alignment: RunAlignment, case_runs: Sequence[CaseRun]
) -> ServingScores:
    """Score what a run's turns served, from its alignment and stored outcomes.

    Args:
        cases: the labels `alignment` was taken against.
        case_runs: the recorded cases `alignment` was taken over.

    Raises: ValueError naming the case when an alignment has no recorded case or no
        label, when two outcomes share a position, when an aligned case's outcome sits
        at a position its label does not have, or when an aligned `faq_question` has
        no outcome or a request produced under another intent has one.
    """
    labels = {case.id: case for case in cases}
    runs = {run.case_id: run for run in case_runs}
    case_exclusions: list[ExclusionReason] = []
    request_exclusions: list[ExclusionReason] = []
    not_permitted: list[str] = []
    unserved: list[UnservedRequest] = []
    denominator = abstentions = 0
    wrong: list[Abstention] = []
    degraded: list[Answer] = []
    on_gaps: list[Answer] = []
    counts = dict.fromkeys(FaqVerdict, 0)

    for case_alignment in alignment.alignments:
        case_id = case_alignment.case_id
        label, case_run = labels.get(case_id), runs.get(case_id)
        if label is None or case_run is None:
            raise ValueError(f"{case_id} is aligned but not recorded and labelled")
        answerable = [
            position
            for position, request in enumerate(label.requests)
            if request.is_answerable_faq
        ]

        reason = case_alignment.turn_exclusion
        if case_alignment.state is AlignmentState.EXCLUDED or reason is not None:
            assert reason is not None
            case_exclusions.append(reason)
            request_exclusions.extend([reason] * len(answerable))
            if reason in _NOT_PERMITTED and answerable:
                not_permitted.append(case_id)
            continue

        aligned = case_alignment.state is AlignmentState.ALIGNED
        outcomes = _outcomes_by_position(case_run)
        for outcome in outcomes.values():
            counts[outcome.verdict] += 1
            labelled = _labelled_at(label, outcome, aligned)
            if outcome.verdict is FaqVerdict.ANSWERED_UNRERANKED:
                degraded.append(_answer(case_id, outcome))
            if outcome.verdict.answered:
                if (
                    labelled is not None
                    and _is_faq(labelled)
                    and not labelled.answerable
                ):
                    on_gaps.append(_answer(case_id, outcome))
                continue
            abstentions += 1
            if labelled is not None and labelled.is_answerable_faq:
                wrong.append(
                    Abstention(
                        case_id=case_id,
                        position=outcome.position,
                        question=outcome.question,
                        gate=StoppingGate.of(outcome.verdict),
                    )
                )

        denominator += len(answerable)
        if not aligned:
            message = (
                case_run.patient_message.content
                if case_run.patient_message is not None
                else label.message
            )
            unserved.extend(
                UnservedRequest(
                    case_id=case_id,
                    position=position,
                    question=message,
                    cause=UnservedCause.LOST_TO_COUNT_MISMATCH,
                    gate=None,
                )
                for position in answerable
            )
            continue
        for pair in case_alignment.pairs:
            if pair.position not in answerable:
                continue
            produced = outcomes.get(pair.position)
            if pair.produced.intent is not IntentLabel.FAQ_QUESTION:
                if produced is not None:
                    raise ValueError(
                        f"{case_id}: position {pair.position} was produced as "
                        f"{pair.produced.intent.value} but carries a FAQ outcome"
                    )
                unserved.append(
                    UnservedRequest(
                        case_id=case_id,
                        position=pair.position,
                        question=pair.produced.text,
                        cause=UnservedCause.MISCLASSIFIED,
                        gate=None,
                    )
                )
                continue
            if produced is None:
                raise ValueError(
                    f"{case_id}: the faq_question at position {pair.position} "
                    "has no outcome"
                )
            if not produced.verdict.answered:
                unserved.append(
                    UnservedRequest(
                        case_id=case_id,
                        position=pair.position,
                        question=produced.question,
                        cause=UnservedCause.ABSTAINED,
                        gate=StoppingGate.of(produced.verdict),
                    )
                )

    by_case = Exclusions.tally(case_exclusions)
    return ServingScores(
        unserved_answerable_share=Metric(
            name=UNSERVED_ANSWERABLE_SHARE,
            numerator=len(unserved),
            denominator=denominator,
            excluded=Exclusions.tally(request_exclusions),
        ),
        unserved_by_cause={
            cause: sum(1 for request in unserved if request.cause is cause)
            for cause in UnservedCause
        },
        unserved=unserved,
        not_permitted_to_answer=not_permitted,
        wrong_abstention_share=Metric(
            name=WRONG_ABSTENTION_SHARE,
            numerator=len(wrong),
            denominator=abstentions,
            excluded=by_case,
        ),
        wrong_abstentions=wrong,
        degraded_answers=degraded,
        answers_on_labelled_gaps=on_gaps,
        verdict_distribution=VerdictDistribution(counts=counts, excluded=by_case),
    )


def _is_faq(request: LabelledRequest) -> bool:
    """Return True for a labelled `faq_question`."""
    return request.intent is IntentLabel.FAQ_QUESTION


def _outcomes_by_position(case_run: CaseRun) -> dict[int, RequestOutcome]:
    """Return the stored reply's outcomes keyed by position, empty when none ran.

    Raises: ValueError naming the case when two outcomes share a position.
    """
    reply = case_run.assistant_message
    outcomes = reply.request_outcomes if reply is not None else None
    by_position: dict[int, RequestOutcome] = {}
    for outcome in outcomes or ():
        if outcome.position in by_position:
            raise ValueError(
                f"{case_run.case_id}: two outcomes at position {outcome.position}"
            )
        by_position[outcome.position] = outcome
    return by_position


def _labelled_at(
    label: Case, outcome: RequestOutcome, aligned: bool
) -> LabelledRequest | None:
    """Return the labelled request an outcome aligns to, or None in an unaligned case.

    Raises: ValueError naming the case when an aligned case's outcome sits at a
        position its label does not have.
    """
    if not aligned:
        return None
    if not 0 <= outcome.position < len(label.requests):
        raise ValueError(
            f"{label.id}: an outcome at position {outcome.position} has no label"
        )
    return label.requests[outcome.position]


def _answer(case_id: str, outcome: RequestOutcome) -> Answer:
    """List a produced answer by case, position, question and verdict."""
    return Answer(
        case_id=case_id,
        position=outcome.position,
        question=outcome.question,
        verdict=outcome.verdict,
    )
