"""Pydantic request/response DTOs."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chat.domain.validation import is_meaningless

_ULID_LENGTH = 26


class ChatRequest(BaseModel):
    """`POST /chat` request body.

    `local_now` is the visitor's own clock, sent on every turn. It resolves relative
    phrasing ("tomorrow", "next Tuesday at 3") and is the only clock any past,
    upcoming, or booking-horizon judgement is made against - so it must carry no
    timezone, because there is none to carry.
    """

    chat_id: str = Field(min_length=_ULID_LENGTH, max_length=_ULID_LENGTH)
    message: str = Field(min_length=1, max_length=2000)
    local_now: datetime

    @field_validator("message")
    @classmethod
    def _reject_meaningless_message(cls, value: str) -> str:
        """Raises: ValueError if `value` has no meaningful text."""
        if is_meaningless(value):
            raise ValueError("message must contain meaningful text")
        return value

    @field_validator("local_now")
    @classmethod
    def _reject_timezone_aware(cls, value: datetime) -> datetime:
        """Raises: ValueError if `value` carries a timezone offset."""
        if value.tzinfo is not None:
            raise ValueError("local_now must carry no timezone offset")
        return value


class IntentLabel(StrEnum):
    """Legal values for a classified patient-message intent.

    Every member but the last is the classifier's own closed output set;
    `CLASSIFICATION_FAILED` is assigned only by orchestration code on a failed/invalid
    classification call, never returned by the classifier itself - excluded from its
    request schema's `enum`, so it's structurally unreachable from a model response,
    not just a convention.
    """

    FAQ_QUESTION = "faq_question"
    BOOKING = "booking"
    SMALL_TALK = "small_talk"
    URGENT_CONDITION = "urgent_condition"
    DISTRESS = "distress"
    BOOKING_FOR_ANOTHER = "booking_for_another"
    CALL_STAFF = "call_staff"
    UNKNOWN = "unknown"
    CLASSIFICATION_FAILED = "classification_failed"


# How many requests one message may be split into. A bound on fan-out, not a claim
# that a message never carries more: above it the segmenter combines the least
# separable requests rather than dropping any, and says so with `cap_bound`.
MAX_SEGMENTS = 3


class RequestSegment(BaseModel):
    """One thing the visitor asked for, as a request that stands on its own.

    `text` is a restatement, not a substring: "do you have parking, and is it free?"
    yields a second segment reading "is parking free?", which is a question a corpus can
    answer where "is it free?" is not. It is what the turn retrieves for and what the
    specialist is asked to answer, which is why a blank one is rejected rather than
    carried - it would be searched for as though it were a question.

    A part of a message that asks for nothing is not a segment: the segments are the
    requests. A message that asks for nothing at all is one segment, labelled
    `SMALL_TALK`, so the turn still reaches a specialist through the same derived label
    list as every other turn.
    """

    intent: IntentLabel
    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def _reject_blank_text(cls, value: str) -> str:
        """Reject a segment whose text is whitespace only."""
        if not value.strip():
            raise ValueError("a request segment's text must not be blank")
        return value


class IntentClassificationResult(BaseModel):
    """What one classification attempt yielded: the requests a message carries.

    `classify_intent()` never *returns* one containing `CLASSIFICATION_FAILED` - it
    raises instead. That label reaches a result only when orchestration builds one
    itself after the call raised, which it does so that a failed attempt and a
    classified one leave behind the same type, read the same way.

    `cap_bound` is the segmenter's own report that it had to combine requests to fit
    `MAX_SEGMENTS`, never inferred from the segment count: a message carrying exactly
    three requests fits, and reading a full list as a truncated one would mark every
    such turn as having lost something.
    """

    segments: list[RequestSegment] = Field(
        min_length=1,
        max_length=MAX_SEGMENTS,
        # Stated here as well as in the prompt because the description travels with the
        # request schema, where a model attends to it: the bounds themselves cannot be
        # sent (the API rejects array bounds in a constrained-output schema), so this
        # is the only place in the schema the limit can appear at all.
        description=(
            "The requests the message contains, in the order they appear. "
            f"At most {MAX_SEGMENTS} items, never more."
        ),
    )
    cap_bound: bool = False

    @property
    def intents(self) -> list[IntentLabel]:
        """Return one label per segment, in the order the requests appear.

        Derived rather than returned by the model, so the labels every routing rule
        reads cannot drift from the segments they describe.
        """
        return [segment.intent for segment in self.segments]


class FaqVerdict(StrEnum):
    """What the FAQ half of a turn did, and for an abstention, where it stopped.

    Six values, each one situation. An abstention names where it stopped, because the
    four call for four different fixes - add entries, re-index the corpus, rewrite the
    entry or lower the similarity floor, or lower the rerank floor.

    `ABSTAINED_EMPTY_POOL` is not the similarity floor's doing: the session publishes
    live revisions and the search still matched no chunk of them, which says the index
    is behind the rows, not that the bar is too high. Lowering the floor cannot fix it.

    `ANSWERED_UNRERANKED` is a separate value rather than a flag beside `ANSWERED`
    because the answer rests on different evidence: up to five chunks no cross-encoder
    approved, rather than at most three it did.

    The four abstentions are identical in behaviour - same message, same call to
    staff, same absence of a generation call. Nothing may branch on which one it is.
    """

    ANSWERED = "answered"
    ANSWERED_UNRERANKED = "answered_unreranked"
    ABSTAINED_EMPTY_CORPUS = "abstained_empty_corpus"
    ABSTAINED_EMPTY_POOL = "abstained_empty_pool"
    ABSTAINED_SIMILARITY_FLOOR = "abstained_similarity_floor"
    ABSTAINED_RERANK_FLOOR = "abstained_rerank_floor"

    @property
    def answered(self) -> bool:
        """Return True if this verdict means the patient received a generated answer."""
        return self in (FaqVerdict.ANSWERED, FaqVerdict.ANSWERED_UNRERANKED)


class Citation(BaseModel):
    """A retrieved chunk the answer was generated from, verbatim."""

    entry_id: int
    chunk_index: int
    chunk_text: str


class RequestOutcome(BaseModel):
    """What one request of a message got: its answer, its verdict, its evidence.

    The only place a verdict lives (FR-002). A message may carry several requests, and
    each is retrieved for, gated and answered on its own - so a turn that answered one
    and abstained on another has no single verdict to report, and nothing derives one.

    `question` is the request **as the classifier restated it**: the text this run
    actually retrieved for, not the patient's own wording. That is what staff act on,
    and it is deliberately not what the patient-facing reply quotes back.

    `answer` is what the FAQ half generated for this request alone, before any merge -
    which is what makes a merged reply checkable against the parts it was built from.

    Two invariants are enforced here rather than left to callers, because each is a
    second way of saying what the verdict already says, and a reader could disagree
    with it:

    1. `answer` is None exactly when the verdict is an abstention. An empty string
       would be that second way.
    2. `citations` is empty exactly then. An answered request cites the survivors that
       were placed in its prompt, which is at least one - generation is what produced
       the verdict.

    A chunk may appear under two outcomes of one message. That is two provenances, not
    a duplicate: it supported two answers.
    """

    position: int
    question: str
    answer: str | None
    verdict: FaqVerdict
    citations: list[Citation]

    @model_validator(mode="after")
    def _answer_and_citations_follow_the_verdict(self) -> "RequestOutcome":
        """Refuse an outcome whose answer or evidence disagrees with its verdict."""
        if self.verdict.answered:
            if not self.answer:
                raise ValueError("an answered request carries the text it produced")
            if not self.citations:
                raise ValueError("an answered request cites what it stood on")
        else:
            if self.answer is not None:
                raise ValueError("an abstained request generated nothing")
            if self.citations:
                raise ValueError("an abstained request cites nothing")
        return self


class ChatTokenEvent(BaseModel):
    """An incremental slice of the streamed answer."""

    type: Literal["token"] = "token"
    text: str


class AnswerSource(StrEnum):
    """What wrote the reply a turn ended with.

    `HAND_OFF` is the one that produced no answer at all: a person now has this, and
    the turn told the visitor so in fixed text, having retrieved, booked and generated
    nothing. *Why* a person was fetched is the escalation reason's to say, not this
    field's - one fact, one field (spec 009 FR-049).

    `MERGED` means the composing model wrote the reply, and nothing more. It does *not*
    say two specialists ran: a turn carrying a not-authorized notice beside one servable
    intent is composed too (FR-022c1). What went into the merge is recorded beside it -
    `request_outcomes`, `booking_outcome`, and `notice_included` on the completion -
    rather than encoded here, because those are orthogonal facts. A notice can
    accompany one specialist or two, so folding it in would need an enum value per
    combination, and the first reader to see `merged_with_notice` would still not know
    how many specialists ran.
    """

    FAQ = "faq"
    BOOKING = "booking"
    SMALL_TALK = "small_talk"
    MERGED = "merged"
    HAND_OFF = "hand_off"


class ChatDoneEvent(BaseModel):
    """Terminal NDJSON event: provenance, what each request got, and message.

    `request_outcomes` is None when no FAQ specialist ran, since a booking reply is
    streamed text that was never retrieved against and so had no gate to stop at. It is
    never `[]`: a half that ran answered or abstained on at least one request.

    `message` keeps its meaning: set only when there is no streamed text to show. Two
    paths do that - a turn whose every request abstained, and a turn routed as several
    parts that collapsed to one, where nothing streamed because the route expected a
    merge. The second carries an answer rather than an abstention, so `message` being
    set says nothing about any verdict; read the outcomes for that. A client renders
    `message` if present, otherwise the tokens it accumulated.

    The outcomes are carried on the patient's path as well as the console's, and the
    patient pane simply does not draw them. That is presentation, not access: one
    session owns both panes and can read and edit every entry of the corpus on the FAQ
    screen, so a payload that omitted them would protect nothing while splitting one
    message record into two shapes free to drift apart.
    """

    type: Literal["done"] = "done"
    request_outcomes: list[RequestOutcome] | None
    message: str | None = None
    answer_source: AnswerSource = AnswerSource.FAQ


class ChatCancelledEvent(BaseModel):
    """Terminal NDJSON event: this request produced no reply, and nothing was stored.

    Emitted instead of `ChatDoneEvent` when a newer message on the same chat arrived
    before this one finished generating, or when a person took the conversation over
    before its reply could be written. Either way no reply was stored for this request;
    any `token` events already received for it should be discarded, not shown as final
    or as an error.
    """

    type: Literal["cancelled"] = "cancelled"


class ChatSilentEvent(BaseModel):
    """Terminal NDJSON event: the assistant may not speak in this conversation.

    Emitted instead of `ChatDoneEvent` when the conversation is escalated or the
    assistant is paused in it. The message was accepted and stored, and it carries the
    mark saying nothing answered it - so a client renders nothing for this and leaves
    the message in the thread.

    A third terminal value rather than a reuse of the other two, because both already
    mean something else: `cancelled` tells a client to discard a message that is in fact
    being kept, and an empty `done` announces a reply that does not exist.
    """

    type: Literal["silent"] = "silent"


class MessageOut(BaseModel):
    """A single message in a chat's history.

    `request_outcomes` is only meaningful for `sender="assistant"`; always None for a
    patient message and for a staff one, which was never retrieved against - and None
    for an assistant reply whose turn ran no FAQ half, which is a different thing from
    a half that ran and produced nothing (`[]`, which nothing writes).

    `attention_mark` is only ever set on a patient message: which of the eight kinds it
    is, or None for no mark. There is deliberately no field naming the person who wrote
    a staff message - `sender` carries everything a client's label states, and this
    system has no such person to name.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    sender: Literal["patient", "assistant", "staff"]
    content: str
    request_outcomes: list[RequestOutcome] | None = None
    attention_mark: (
        Literal[
            "urgent_condition",
            "distress",
            "patient_asked_for_person",
            "booking_for_another_person",
            "not_authorized",
            "corpus_could_not_answer",
            "assistant_failed",
            "unanswered",
        ]
        | None
    ) = None
    created_at: datetime


class StaffMessageWrite(BaseModel):
    """`POST /console/chats/{chat_id}/messages` request body.

    The same bounds and the same meaningless-content rule a patient message faces: a
    staff member typing whitespace into the composer has said nothing either.
    """

    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def _reject_meaningless_content(cls, value: str) -> str:
        """Raises: ValueError if `value` has no meaningful text."""
        if is_meaningless(value):
            raise ValueError("content must contain meaningful text")
        return value


class ConsoleConversationOut(BaseModel):
    """One conversation as the staff side lists it.

    `emphasized`, `assistant_may_reply` and `pause_seconds_remaining` are derived rather
    than stored, so the switch a staff member sees and the gate a turn obeys are the
    same answer. `pause_seconds_remaining` is null whenever no pause is running -
    including while escalated, which has no deadline for a countdown to show.

    Carries no session id: no response on this surface repeats the credential the
    browser is not allowed to read.
    """

    chat_id: str
    patient_name: str | None
    last_message_at: datetime | None
    emphasized: bool
    escalated: bool
    escalation_reason: str | None
    attention_since: datetime | None
    assistant_may_reply: bool
    pause_seconds_remaining: int | None


class ConsoleConversationsResponse(BaseModel):
    """`GET /console/conversations` response body - the one polled read model.

    `attention_total` counts *conversations* needing a person, once each however many
    marks sit inside one: four unanswered messages in a thread are one person's problem,
    not four.
    """

    attention_total: int
    conversations: list[ConsoleConversationOut]


class AssistantSwitchWrite(BaseModel):
    """`POST /console/chats/{chat_id}/assistant` request body.

    `enabled` is the position the switch is being moved to, not a toggle: two tabs
    showing the same conversation must not turn it into a race over whose click was
    second.
    """

    enabled: bool


class AssistantStateOut(BaseModel):
    """What the assistant may do in one conversation, after a change to it.

    Both fields are derived from the stored columns, so the answer returned here is the
    same one the poll will report a moment later.
    """

    assistant_may_reply: bool
    pause_seconds_remaining: int | None


class ChatHistoryResponse(BaseModel):
    """`GET /chats/{chat_id}/messages` response: the chat's messages, chronological.

    Not guaranteed to alternate sender.
    """

    messages: list[MessageOut]


class ChatSummary(BaseModel):
    """One row of the session's chat list.

    `patient_name` is None while this chat's patient record does not exist yet; the
    client renders its own placeholder from `created_at` rather than the server
    inventing a label it would then have to keep consistent.
    """

    id: str
    patient_name: str | None
    created_at: datetime
    last_message_at: datetime | None


class ChatListResponse(BaseModel):
    """`GET /chats` response body.

    `chats` is already in display order - chats holding messages first, newest message
    first, then chats with none, newest-created first - so the client opens `chats[0]`
    rather than re-deriving the rule on every render. May be empty: a session with zero
    chats is a valid state.

    `session_exists` is what tells an empty list apart from a session the user emptied,
    and the two require opposite behavior - a first arrival is given a chat, an emptied
    session is left alone. The client cannot make that distinction itself: the session
    cookie is `HttpOnly`, so it never sees one.
    """

    chats: list[ChatSummary]
    session_exists: bool


class FaqEntryWrite(BaseModel):
    """`POST`/`PUT /faq` request body."""

    content: str = Field(min_length=1, max_length=20000)

    @field_validator("content")
    @classmethod
    def _reject_meaningless_content(cls, value: str) -> str:
        """Raises: ValueError if `value` has no meaningful text."""
        if is_meaningless(value):
            raise ValueError("content must contain meaningful text")
        return value


class FaqEntry(FaqEntryWrite):
    """`FaqEntryWrite` plus server-assigned fields."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
