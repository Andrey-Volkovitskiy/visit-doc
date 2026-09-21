"""The golden set's declaration, and the builder that renders `cases.json` from it.

`evals/golden/cases.json` is an artifact: this module is where the set is actually
written, and editing the JSON by hand puts the two out of step -
`tests/test_golden_set.py` fails when they disagree. Change the declaration below and
re-render with `python -m golden_harness build-set`.

Declared here rather than beside the JSON because `evals/golden/` holds data and no
code, and because the invariants a JSON Schema cannot state - a `cites` id that exists
in the corpus pin, a fixture start inside the named practitioner's own working hours -
are checked as the set is built, naming the case at fault.
"""

import json
import re
from pathlib import Path
from typing import Any, Final

# `src/golden_harness/golden_set.py` -> `evals/golden/`.
GOLDEN_DIR: Final = Path(__file__).resolve().parents[3] / "golden"
CASES_JSON: Final = GOLDEN_DIR / "cases.json"
CORPUS_JSON: Final = GOLDEN_DIR / "corpus.json"

OSLER: Final = "William Osler"  # General Practice, Mon-Fri 09:00-17:00
VESALIUS: Final = "Andreas Vesalius"  # Dentistry, Mon-Sat 09:00-14:00

# The run clock is a Monday, so +0d is Monday and +5d is Saturday.
MON: Final = "+0d"
TUE: Final = "+1d"
WED: Final = "+2d"
THU: Final = "+3d"
FRI: Final = "+4d"
SAT: Final = "+5d"


# --- the compact declaration helpers ------------------------------------------------


def faq(gist: str, *cites: str) -> dict[str, Any]:
    """An FAQ request the pinned corpus answers, resting on `cites`."""
    return {
        "intent": "faq_question",
        "gist": gist,
        "answerable": True,
        "cites": list(cites),
    }


def gap(gist: str) -> dict[str, Any]:
    """An FAQ request the pinned corpus does not answer."""
    return {"intent": "faq_question", "gist": gist, "answerable": False, "cites": []}


# What a tool cannot be called without. `check_availability` and `book_appointment`
# both take a `practitioner_id`, and the only place one comes from is
# `list_practitioners`; `reschedule_appointment` and `cancel_appointment` both take an
# `appointment_id`, which comes from `list_my_appointments`. A label naming the second
# without the first would expect the loop to have invented an id - and a message naming
# a practitioner in words does not help, because the tools take the id and not the name.
PREREQUISITE: Final[dict[str, str]] = {
    "check_availability": "list_practitioners",
    "book_appointment": "list_practitioners",
    "reschedule_appointment": "list_my_appointments",
    "cancel_appointment": "list_my_appointments",
}


def bk(gist: str, *tools: str) -> dict[str, Any]:
    """A booking request, with the tools whose absence means it was not served.

    Each named tool's prerequisite is added for it, so a case states the tool it is
    *about* and the id-fetching call that tool implies is never left off by hand.
    """
    required = list(tools)
    for tool in tools:
        prerequisite = PREREQUISITE.get(tool)
        if prerequisite is not None and prerequisite not in required:
            required.append(prerequisite)
    return {"intent": "booking", "gist": gist, "tools": required}


def req(intent: str, gist: str) -> dict[str, Any]:
    """A request of any intent that carries neither citations nor tools."""
    return {"intent": intent, "gist": gist}


def appt(
    practitioner: str, day: str, time: str | None = None, status: str | None = None
) -> dict[str, Any]:
    """One appointment of a scheduling fixture."""
    entry: dict[str, Any] = {"practitioner": practitioner, "day": day}
    if time is not None:
        entry["time"] = time
    if status is not None:
        entry["status"] = status
    return entry


def sch(
    given: list[dict[str, Any]],
    expect: list[dict[str, Any]],
    reply: str | None = None,
) -> dict[str, Any]:
    """A scheduling fixture: what is planted, what must hold after, and any 2nd turn."""
    fixture: dict[str, Any] = {"given": given, "expect": expect}
    if reply is not None:
        fixture["reply"] = reply
    return fixture


def turn(role: str, text: str) -> dict[str, Any]:
    """One prior turn of a case's planted history."""
    return {"role": role, "text": text}


def case(
    cid: str,
    message: str,
    requests: list[dict[str, Any]],
    *,
    history: list[dict[str, Any]] | None = None,
    scheduling: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """One labelled case, with only the keys it actually carries."""
    entry: dict[str, Any] = {"id": cid, "message": message}
    if history is not None:
        entry["history"] = history
    entry["requests"] = requests
    if scheduling is not None:
        entry["scheduling"] = scheduling
    if note is not None:
        entry["note"] = note
    return entry


FAMILIES: list[dict[str, Any]] = []


def family(letter: str, name: str, tests: str, cases: list[dict[str, Any]]) -> None:
    """Append one family group, in the order the set is meant to read."""
    FAMILIES.append({"letter": letter, "name": name, "tests": tests, "cases": cases})


# === a - single-booking =============================================================

family(
    "a",
    "single-booking",
    "One booking request the booking node can serve on its own, across the whole of "
    "its range: who the practitioners are and whether one with a given specialty is on "
    "the roster, when one is free, what this patient already holds, and the three "
    "writes. A write is confirmed "
    "before it happens, so those cases carry a scripted second turn - including bare "
    "affirmatives, which must read as the booking they answer and never as small talk. "
    "One case carries history instead, for the turn that names no practitioner at all "
    "and is answerable only by carrying one forward from an earlier message. Every "
    "case states the appointments the patient must hold when it ends.",
    [
        case(
            "G-a-01",
            "Which practitioners do you have?",
            [bk("the roster", "list_practitioners")],
            scheduling=sch([], []),
        ),
        case(
            "G-a-02",
            "Do you have a dentist?",
            [bk("is there a dentist on the roster", "list_practitioners")],
            scheduling=sch([], []),
        ),
        case(
            "G-a-04",
            "Do you have a cardiologist I could see?",
            [bk("is there a cardiologist", "list_practitioners")],
            scheduling=sch([], []),
            note="the roster holds a GP and a dentist only, so the honest answer is no "
            "- the case is here to catch a confabulated yes, and nothing is booked",
        ),
        case(
            "G-a-05",
            "What times does William Osler have free on Wednesday?",
            [bk("a named practitioner's Wednesday slots", "check_availability")],
            scheduling=sch([], []),
        ),
        case(
            "G-a-06",
            "When could I see the dentist on Tuesday morning?",
            [bk("dentist availability Tuesday morning", "check_availability")],
            scheduling=sch([], []),
            note="names a specialty rather than a person, so the roster has to be read "
            "before times can be offered",
        ),
        case(
            "G-a-07",
            "Is there anything free on Saturday?",
            [bk("Saturday availability for the dental visit", "check_availability")],
            history=[
                turn("user", "I would like to get a tooth filling."),
                turn("assistant", "Of course - which day would suit you?"),
            ],
            scheduling=sch([], []),
            note="the message itself names neither a practitioner nor a specialty: "
            "'tooth filling' is in the history, so the turn is only answerable by "
            "carrying it forward. Only the dentist works Saturday and the GP's week "
            "ends Friday, so a reply offering a GP slot is wrong twice over. The "
            "clinic's own turn names neither the dentist nor dentistry, so the "
            "resolution has to come from the patient's words rather than from a "
            "specialty the assistant already said out loud",
        ),
        case(
            "G-a-08",
            "Book me in with William Osler on Wednesday at 10am.",
            [bk("book a named practitioner at a named time", "book_appointment")],
            scheduling=sch([], [appt(OSLER, WED, "10:00", "standing")], reply="OK"),
            note="one slot is on the table, so the bare 'OK' can only be confirming "
            "it - the small-talk reading would leave nothing booked",
        ),
        case(
            "G-a-09",
            "I need a dental appointment on Tuesday morning, please.",
            [
                bk(
                    "book a dentist Tuesday morning",
                    "check_availability",
                    "book_appointment",
                )
            ],
            scheduling=sch(
                [],
                [appt(VESALIUS, TUE, status="standing")],
                reply="The earliest of those works - please book it.",
            ),
            note="no time named, so the reply picks from what was offered and the "
            "expectation names the day only",
        ),
        case(
            "G-a-10",
            "When is my next appointment?",
            [bk("this patient's next appointment", "list_my_appointments")],
            scheduling=sch(
                [appt(OSLER, WED, "14:00")],
                [appt(OSLER, WED, "14:00", "standing")],
            ),
            note="a read: the appointment must still stand, unchanged, afterwards",
        ),
        case(
            "G-a-11",
            "What appointments do I have booked?",
            [bk("this patient's appointments", "list_my_appointments")],
            scheduling=sch(
                [appt(OSLER, WED, "11:00"), appt(VESALIUS, FRI, "10:00")],
                [
                    appt(OSLER, WED, "11:00", "standing"),
                    appt(VESALIUS, FRI, "10:00", "standing"),
                ],
            ),
            note="two standing appointments, so a reply naming one of them is short",
        ),
        case(
            "G-a-12",
            "Can you move my Thursday appointment to Friday at 12?",
            [
                bk(
                    "reschedule Thursday to Friday noon",
                    "list_my_appointments",
                    "reschedule_appointment",
                )
            ],
            scheduling=sch(
                [appt(OSLER, THU, "11:00")],
                [appt(OSLER, FRI, "12:00", "standing")],
                reply="OK",
            ),
            note="a moved appointment is the same appointment: the Thursday slot must "
            "not survive beside the Friday one",
        ),
        case(
            "G-a-13",
            "Please cancel my appointment tomorrow.",
            [bk("cancel tomorrow's appointment", "cancel_appointment")],
            scheduling=sch(
                [appt(OSLER, TUE, "10:00")],
                [appt(OSLER, TUE, "10:00", "cancelled")],
                reply="OK",
            ),
        ),
        case(
            "G-a-14",
            "Could I push my dentist visit back a couple of hours?",
            [
                bk(
                    "move the dental visit later the same day",
                    "list_my_appointments",
                    "reschedule_appointment",
                )
            ],
            scheduling=sch(
                [appt(VESALIUS, WED, "09:00")],
                [appt(VESALIUS, WED, "11:00", "standing")],
                reply="Yes, 11 o'clock suits me.",
            ),
            note="the new time is relative and unstated, so the second turn is what "
            "fixes it; 11:00 is inside the dentist's 09:00-14:00 day",
        ),
    ],
)


# === b - urgent_condition ===========================================================

family(
    "b",
    "urgent_condition",
    "A message describing something an emergency department exists for. It takes the "
    "whole turn, whatever else it is wrapped in: the reply is the fixed text pointing "
    "at emergency services, a person is called, and the assistant falls silent. The "
    "assistant does not triage, so no case here expects a judgement about severity.",
    [
        case(
            "G-b-01",
            "I have chest pain radiating down my arm, what should I do?",
            [req("urgent_condition", "chest pain radiating to the arm")],
        ),
        case(
            "G-b-02",
            "I can't breathe properly.",
            [req("urgent_condition", "difficulty breathing")],
        ),
        case(
            "G-b-03",
            "I think I'm having a heart attack.",
            [req("urgent_condition", "suspected heart attack")],
        ),
        case(
            "G-b-04",
            "I've taken too many of my tablets.",
            [req("urgent_condition", "suspected overdose")],
        ),
        case(
            "G-b-09",
            "My tooth is absolutely killing me, I need to be seen.",
            [bk("book an urgent dental visit", "check_availability")],
            scheduling=sch([], []),
            note="the counter-case: everyday hyperbole about an ordinary complaint is "
            "a booking, not an emergency. Pairs with G-b-01 to stop the label being "
            "won by pain words alone",
        ),
    ],
)


# === c - distress ===================================================================

family(
    "c",
    "distress",
    "Real fear, panic or acute upset about the patient's health or care, whether or "
    "not the message asks for anything. It calls a person and stops the assistant. A "
    "brief exclamation is small talk instead, so the family carries that "
    "counter-case too.",
    [
        case(
            "G-c-01",
            "I'm terrified about my results.",
            [req("distress", "fear about results")],
        ),
        case(
            "G-c-05",
            "I am so frightened that something is seriously wrong with me.",
            [req("distress", "fear about their own health")],
        ),
        case(
            "G-c-06",
            "Ugh, what a week.",
            [req("small_talk", "a mild reaction")],
            note="the counter-case: a brief exclamation is not distress, and paging a "
            "person for it is the queue noise the distress label must not create",
        ),
    ],
)


# === d - booking_for_another ========================================================

family(
    "d",
    "booking_for_another",
    "The message says plainly that the appointment is for someone other than the "
    "person in this chat, which is an authority question a person has to settle. It "
    "covers a change as well as a new booking. The third party must be the one the "
    "appointment is FOR: merely naming another person is an ordinary booking, which "
    "the counter-case carries - and that is the only case here with a scheduling "
    "fixture, since a handed-off turn writes nothing.",
    [
        case(
            "G-d-01",
            "Can I book Monday for my mother?",
            [req("booking_for_another", "booking for a parent")],
        ),
        case(
            "G-d-03",
            "I'm calling on behalf of my father, he needs an appointment.",
            [req("booking_for_another", "booking on behalf of a parent")],
        ),
        case(
            "G-d-04",
            "Can I cancel my wife's Thursday appointment?",
            [req("booking_for_another", "cancelling for a spouse")],
            note="a change for someone else is this too, not just a new booking",
        ),
        case(
            "G-d-06",
            "My wife recommended you - can I book myself in for Tuesday with dr. "
            "Andreas?",
            [bk("book Tuesday for the patient", "check_availability")],
            scheduling=sch([], []),
            note="the counter-case: the appointment is for the person in the chat, so "
            "naming a third party does not make it theirs",
        ),
    ],
)


# === e - not_authorized =============================================================

family(
    "e",
    "not_authorized",
    "A real request that the assistant may never serve, whatever the corpus grows to "
    "hold - a sick note, a prescription, a records transfer, money. It makes no search "
    "and no generation call: the patient gets the fixed notice and a person is called "
    "under a cause distinct from a corpus gap, because one is fixed by writing an "
    "entry and the other by nothing. It does not silence the assistant.",
    [
        case(
            "G-e-01",
            "I'd like you to write me a sick note for my employer.",
            [req("not_authorized", "sick note")],
        ),
        case(
            "G-e-02",
            "Can you renew my prescription?",
            [req("not_authorized", "prescription renewal")],
        ),
        case(
            "G-e-03",
            "There's a mistake on my bill, please correct it.",
            [req("not_authorized", "billing correction")],
        ),
        case(
            "G-e-04",
            "Send my records over to another clinic.",
            [req("not_authorized", "records transfer")],
        ),
        case(
            "G-e-05", "Can you refund my last visit?", [req("not_authorized", "refund")]
        ),
        case(
            "G-e-06",
            "Can you tell me what my blood test results mean?",
            [req("not_authorized", "clinical interpretation")],
        ),
        case(
            "G-e-07",
            "I need a copy of my vaccination certificate.",
            [req("not_authorized", "certificate copy")],
        ),
        case(
            "G-e-08",
            "Please change the name on my file to my married name.",
            [req("not_authorized", "amending a record")],
        ),
        case(
            "G-e-09",
            "How long does a refund usually take?",
            [gap("refund turnaround")],
            note="the counter-case: asking how a policy works is a question, not a "
            "request for an action - faq_question, and a gap in this corpus",
        ),
    ],
)


# === f - call_staff =================================================================

family(
    "f",
    "call_staff",
    "An explicit request for a human, and nothing else. It is the one cause that owes "
    "the patient silence afterwards, so the assistant stops. The label must not "
    "collect the staff-handled issues that have causes of their own - a billing "
    "problem is not_authorized, an emergency is urgent_condition.",
    [
        case(
            "G-f-01",
            "I'd rather speak to a person.",
            [req("call_staff", "asks for a person")],
        ),
        case(
            "G-f-03",
            "Put me through to the front desk, please.",
            [req("call_staff", "asks for the front desk")],
        ),
        case(
            "G-f-05",
            "You said you could put me through to someone about my coverage - "
            "please do that.",
            [req("call_staff", "takes up the offer across turns")],
            history=[
                turn(
                    "assistant",
                    "We accept most major insurance providers, including Blue "
                    "Cross Blue Shield, Aetna, Cigna, UnitedHealthcare, and "
                    "Medicare. If you're unsure whether your specific plan is "
                    "covered, you can ask me to connect you with a member of "
                    "our friendly front desk team.",
                )
            ],
            note="the insurance entry offers to connect the patient with the front "
            "desk, and this is a patient taking that offer up a turn later - the "
            "shape the entry actually produces - so the reply has to route to a "
            "person rather than answer again",
        ),
        case(
            "G-f-06",
            "Is there someone I can talk to about all this?",
            [req("call_staff", "asks for a person, indirectly")],
        ),
    ],
)


# === g - small-talk =================================================================

family(
    "g",
    "small-talk",
    "Messages that ask for nothing the clinic could act on: greetings, thanks, "
    "acknowledgements, farewells, thinking it over, gibberish. The point of the "
    "family is that none of them pages a person and none of them searches the "
    "corpus - before "
    "the label existed, 'Thanks' retrieved, abstained and called a human. Several "
    "carry history, because a bare acknowledgement only reads as one against the turn "
    "it answers.",
    [
        case("G-g-01", "Hi", [req("small_talk", "greeting")]),
        case(
            "G-g-03",
            "Thanks!",
            [req("small_talk", "thanks")],
            history=[
                turn("user", "Can I book Wednesday at 10am?"),
                turn("assistant", "Your appointment is booked."),
            ],
            note="the request in the history has its answer after it, so the thanks "
            "thanks the clinic rather than trailing something still open",
        ),
        case(
            "G-g-04",
            "OK",
            [req("small_talk", "acknowledgement")],
            history=[
                turn("user", "What time should I arrive?"),
                turn(
                    "assistant",
                    "Please arrive 15 minutes before your appointment time.",
                ),
            ],
            note="an acknowledgement of an answered question. The same word after an "
            "offered slot would be a booking (G-a-08), which is why both exist",
        ),
        case(
            "G-g-06",
            "Sure",
            [req("small_talk", "acknowledgement")],
            history=[
                turn("assistant", "We are open Monday through Saturday, 9am to 6pm.")
            ],
            note="nothing was asked of the patient, so 'Sure' agrees with nothing and "
            "is not a confirmation",
        ),
        case("G-g-07", "Bye", [req("small_talk", "farewell")]),
        case("G-g-09", "asdfgh", [req("small_talk", "unintelligible")]),
        case("G-g-10", "Merci", [req("small_talk", "thanks, not in English")]),
        case(
            "G-g-11",
            "Let me think about it a bit.",
            [req("small_talk", "thinking it over")],
        ),
    ],
)


# === i - out-of-topic ===============================================================

family(
    "i",
    "out-of-topic",
    "A question clearly outside the clinic's domain. It does request something, so an "
    "earlier rule made it an escalation - and paging a person because a patient asked "
    "about the weather is exactly the queue noise escalation must not carry. There is "
    "nothing for staff to do and nothing in the corpus to find, so it is answered "
    "politely as small talk and calls nobody.",
    [
        case(
            "G-i-01",
            "Who won the football last night?",
            [req("small_talk", "off-topic question")],
        ),
        case(
            "G-i-02",
            "What's the weather in Paris today?",
            [req("small_talk", "off-topic question")],
        ),
    ],
)


# === j - single-faq-answered ========================================================

family(
    "j",
    "single-faq-answered",
    "One FAQ question the pinned corpus answers. The whole RAG path has to work: the "
    "request clears the similarity floor and the rerank floor, an answer is generated "
    "from what was retrieved, and the citation points at the entry that actually "
    "carries it. Several are deliberate paraphrases sharing no wording with their "
    "entry, so the family measures retrieval rather than string overlap.",
    [
        case(
            "G-j-01",
            "What should I bring to my first appointment?",
            [faq("what to bring", "what-to-bring")],
        ),
        case(
            "G-j-02",
            "What are your opening hours?",
            [faq("clinic hours", "hours-location")],
        ),
        case(
            "G-j-03", "Where are you based?", [faq("clinic address", "hours-location")]
        ),
        case(
            "G-j-05",
            "Can I see a specialist if my GP hasn't sent anything over?",
            [faq("is a referral needed", "referral")],
            note="a paraphrase sharing no wording with the entry, and phrased as a "
            "'can I see' question that must not be read as an availability check",
        ),
        case(
            "G-j-06",
            "Is it possible to have my appointment over video call?",
            [faq("is telehealth offered", "telehealth")],
            note="the corpus answers no; the case is here to check a no is given "
            "rather than abstained on",
        ),
        case(
            "G-j-08",
            "Do you take Aetna?",
            [faq("is Aetna accepted", "insurance-plans")],
            note="the entry lists the plans it accepts, and this names one of them, so "
            "the answer has to settle a membership question rather than repeat the "
            "list back",
        ),
        case(
            "G-j-09",
            "Is there anywhere to park near the clinic?",
            [faq("parking near the clinic", "hours-location")],
        ),
        case(
            "G-j-10",
            "How much is a dentist visit if I'm paying myself?",
            [faq("dentist out-of-pocket rate", "out-of-pocket-rates")],
        ),
        case(
            "G-j-12",
            "Can I pay with my HSA card?",
            [faq("is an HSA card accepted", "payment")],
        ),
        case(
            "G-j-14",
            "What happens if you don't take my insurance?",
            [faq("out-of-network options", "out-of-network")],
        ),
        case(
            "G-j-16",
            "How long before my slot should I turn up if I've been before?",
            [faq("returning-patient arrival time", "arrival-time")],
            note="the entry answers first visits and returning patients differently, "
            "so an answer giving only the 15-minute figure is wrong",
        ),
    ],
)


# === k - single-faq-gap =============================================================

family(
    "k",
    "single-faq-gap",
    "One FAQ question the pinned corpus cannot answer. These are near misses: they "
    "sound like clinic policy and most of them overlap an entry heavily in vocabulary, "
    "so the retrieval will surface something plausible. The system must abstain and "
    "raise a corpus gap rather than stretch a neighbouring entry into an answer - a "
    "stretched answer is a confident false statement to a patient.",
    [
        case(
            "G-k-01",
            "How much does an MRI scan cost out of pocket?",
            [gap("MRI cost")],
            note="heavy overlap with the rates entry, which prices a GP, a dentist "
            "and 'other specialist consultations' - not a scan",
        ),
        case(
            "G-k-02",
            "Is the entrance wheelchair accessible?",
            [gap("wheelchair access")],
            note="entirely plausible clinic policy, absent from the corpus",
        ),
        case(
            "G-k-03",
            "Can I pay by cheque?",
            [gap("payment by cheque")],
            note="the payment entry lists cash, cards and FSA/HSA. A list that does "
            "not mention cheques is not an answer about cheques",
        ),
        case(
            "G-k-04",
            "What is your cancellation and no-show policy?",
            [gap("cancellation policy")],
            note="asks the terms governing a cancellation, which the records do not "
            "hold either - it must not reach the booking node",
        ),
        case(
            "G-k-07",
            "Do I need to fast before a blood test?",
            [gap("fasting before a blood test")],
        ),
        case(
            "G-k-10",
            "Are you open on public holidays?",
            [gap("public holidays")],
            note="same subject as the hours entry, which gives weekdays only and says "
            "nothing about holidays",
        ),
        case(
            "G-k-14",
            "How much is a follow-up visit?",
            [gap("follow-up price")],
            note="same subject as the rates entry, which prices first visits by "
            "practitioner type and never distinguishes a follow-up",
        ),
    ],
)


# === l - single-faq-that-looks-multi ================================================

family(
    "l",
    "single-faq-that-looks-multi",
    "One request wearing the clothes of several: a list of options, an aside in the "
    "middle of one, two phrasings of one question. The classifier must not "
    "over-split them. Splitting here is not harmless - each half retrieves "
    "separately and competes for the same shortlist, and the reply then answers one "
    "question twice instead of answering it once.",
    [
        case(
            "G-l-02",
            "Can I pay with an HSA card, or a Visa, or cash? Any of those?",
            [faq("accepted payment methods", "payment")],
            note="one request listing options, answered from one entry once",
        ),
        case(
            "G-l-04",
            "So, my insurance is Blue Cross, and I was wondering, because my last "
            "clinic did not take it, whether you do?",
            [faq("is Blue Cross accepted", "insurance-plans")],
            note="comma-heavy with an aside, still one request",
        ),
        case(
            "G-l-05",
            "Do you do walk-ins? Can I just turn up without an appointment?",
            [gap("walk-in visits")],
            note="two phrasings of one request, and a gap - so an over-split shows up "
            "as two abstentions and two gap escalations for one question",
        ),
    ],
)


# === m - compound-faq-answered ======================================================

family(
    "m",
    "compound-faq-answered",
    "Two or three FAQ questions in one message, every one of them answerable. The "
    "retrieval pipeline runs once per request and is never pooled: under a shared "
    "shortlist the stronger question crowds the other out, and the weaker one abstains "
    "for no reason but its company. Each request carries its own citations, so a case "
    "answered from one entry twice cites that entry twice.",
    [
        case(
            "G-m-01",
            "What are your opening hours, and what should I bring to my first visit?",
            [
                faq("clinic hours", "hours-location"),
                faq("what to bring", "what-to-bring"),
            ],
        ),
        case(
            "G-m-02",
            "How early should I arrive, and which insurance plans do you accept?",
            [
                faq("arrival time", "arrival-time"),
                faq("accepted plans", "insurance-plans"),
            ],
        ),
        case(
            "G-m-04",
            "When is payment due, and what cards do you take?",
            [faq("when payment is due", "payment"), faq("accepted cards", "payment")],
            note="both answered from one entry, so it is cited once per request - two "
            "citations of the same chunk, not one deduplicated across the turn",
        ),
        case(
            "G-m-06",
            "Do I need a referral, do you take Cigna, and when do I pay?",
            [
                faq("is a referral needed", "referral"),
                faq("is Cigna accepted", "insurance-plans"),
                faq("when payment is due", "payment"),
            ],
            note="three requests, which is the segment cap - nothing may be dropped "
            "and cap_bound must stay false, since three that fit is not a message cut "
            "short",
        ),
        case(
            "G-m-08",
            "What if you don't take my insurance, and how much is a GP visit then?",
            [
                faq("out-of-network options", "out-of-network"),
                faq("GP out-of-pocket rate", "out-of-pocket-rates"),
            ],
            note="the second request depends on the first's answer and still stands "
            "alone once restated",
        ),
    ],
)


# === n - compound-faq-mixed =========================================================

family(
    "n",
    "compound-faq-mixed",
    "Two requests in one message, each reporting its own verdict - so there is no "
    "single value describing the turn. Most pair an answerable half with a gap, and "
    "the reply must answer the half it can and name the half it cannot without "
    "blurring them: never soften the gap into a partial answer, never stretch the "
    "answer to cover it. One pairs an answerable half with a request the assistant may "
    "never serve, a different cause needing a different fix. One is a gap twice over, "
    "where every request abstains and the turn collapses to the fixed abstention text "
    "with no composing call, still reporting an outcome per request. The hardest are "
    "the pairs sharing a subject, where the entry retrieved for the answerable half is "
    "also the nearest miss for the gap.",
    [
        case(
            "G-n-01",
            "What are your clinic hours, and do you validate parking?",
            [faq("clinic hours", "hours-location"), gap("parking validation")],
            note="same entry is the evidence for one half and the near miss for the "
            "other",
        ),
        case(
            "G-n-02",
            "Do you offer telehealth, and is the clinic wheelchair accessible?",
            [faq("is telehealth offered", "telehealth"), gap("wheelchair access")],
        ),
        case(
            "G-n-05",
            "Do you see children under five, and what are your opening hours?",
            [gap("paediatric care"), faq("clinic hours", "hours-location")],
            note="the gap comes FIRST, so the reply must not let the answered half "
            "absorb it - the order is part of what is being tested",
        ),
        case(
            "G-n-06",
            "What is the out-of-pocket rate for a specialist, and is a scan included?",
            [
                faq("specialist out-of-pocket rate", "out-of-pocket-rates"),
                gap("whether a scan is included"),
            ],
            note="one subject, split answerability - the same entry both answers and "
            "fails to answer, which is the hardest kind not to blur",
        ),
        case(
            "G-n-09",
            "Do I need a referral to see a specialist, and can you renew my "
            "prescription?",
            [
                faq("is a referral needed", "referral"),
                req("not_authorized", "prescription renewal"),
            ],
            note="the second half is not a corpus gap - no entry could ever make the "
            "assistant able to renew a prescription. Different cause, different fix, "
            "and the answered half still runs",
        ),
        case(
            "G-n-10",
            "Do you validate parking, and how much does an MRI scan cost if I pay "
            "myself?",
            [gap("parking validation"), gap("MRI cost")],
            note="neither half is answerable, so every request abstains and the turn "
            "takes the collapse path - one fixed abstention text, no composing call, "
            "and still one outcome per request. Each half is a near miss on a "
            "different entry: the hours entry names the garage, the rates entry "
            "prices a GP, a dentist and other consultations but no scan. Both "
            "wordings ask what the clinic's policy is rather than asking it to act, "
            "which is what keeps them faq_question and off the not_authorized side",
        ),
    ],
)


# === o - request-segmentation =======================================================

family(
    "o",
    "request-segmentation",
    "Requests that cannot be searched or answered as written: they borrow a subject "
    "from an earlier clause, or refer back with a pronoun or an ellipsis - and one "
    "borrows from the previous turn rather than from anything in its own message. "
    "Each piece has to be restated so it stands alone - 'and how much is the "
    "visit?' has to become 'how much is a dentist visit?' or it retrieves against "
    "nothing. Restating must not add: a resolved reference keeps what was asked, and "
    "inventing the constraint that decides the answer is worse than dropping it.",
    [
        case(
            "G-o-01",
            "When is a dentist available on Tuesday morning, and how much is the "
            "visit if I'm paying out of pocket?",
            [
                bk("dentist availability Tuesday morning", "check_availability"),
                faq("dentist out-of-pocket rate", "out-of-pocket-rates"),
            ],
            scheduling=sch([], []),
            note="the second request has to carry 'dentist' across from the first, or "
            "it asks the price of nothing in particular - and the rates entry prices "
            "a dentist differently from a GP, so the restatement decides the answer",
        ),
        case(
            "G-o-02",
            "Do I need a referral? And what about for a dentist?",
            [
                faq("is a referral needed", "referral"),
                faq("is a referral needed for a dentist", "referral"),
            ],
            note="ellipsis: the second clause has no verb of its own",
        ),
        case(
            "G-o-03",
            "Do you have parking, and is it free?",
            [
                faq("is there parking", "hours-location"),
                faq("is the parking free", "hours-location"),
            ],
            note="'is it free?' cannot be searched as written",
        ),
        case(
            "G-o-05",
            "And a dentist?",
            [faq("dentist out-of-pocket rate", "out-of-pocket-rates")],
            history=[
                turn("user", "How much is a GP visit if I'm paying out of pocket?"),
                turn("assistant", "Out-of-pocket, a GP visit is $120."),
            ],
            note="the reference reaches back a turn rather than a clause: the message "
            "carries no verb, no price word and no way to be searched as written, so "
            "everything that makes it a question comes from the conversation. The "
            "assistant's turn gives the GP figure only, so the dentist's price has to "
            "be retrieved rather than read back out of the history - and the entry "
            "prices the two differently, so the restatement decides the answer",
        ),
        case(
            "G-o-06",
            "Do you take Medicare? What if you don't take my plan?",
            [
                faq("is Medicare accepted", "insurance-plans"),
                faq("out-of-network options", "out-of-network"),
            ],
            note="the second request is conditional on the first's answer and still "
            "stands alone; the two rest on different entries",
        ),
    ],
)


# === p - pleasantry-plus-request ====================================================

family(
    "p",
    "pleasantry-plus-request",
    "A greeting, a thank-you or an acknowledgement attached to a real request. The "
    "pleasantry must not hide the request, and it is not a segment of its own: it is "
    "left out rather than folded in, so the turn carries exactly the requests that "
    "were made. The request behind it is drawn from every route - FAQ, a booking read, "
    "a booking write, and a call for a person.",
    [
        case(
            "G-p-01",
            "Hi, do I need a referral?",
            [faq("is a referral needed", "referral")],
        ),
        case(
            "G-p-02",
            "Morning - what should I bring to a first visit?",
            [faq("what to bring", "what-to-bring")],
        ),
        case(
            "G-p-03",
            "Hello! What are your opening hours?",
            [faq("clinic hours", "hours-location")],
        ),
        case(
            "G-p-04",
            "Thanks! Any slots with William Osler on Wednesday?",
            [bk("Wednesday availability", "check_availability")],
            scheduling=sch([], []),
        ),
        case(
            "G-p-05",
            "Hi, I need to cancel tomorrow.",
            [bk("cancel tomorrow's appointment", "cancel_appointment")],
            scheduling=sch(
                [appt(OSLER, TUE, "10:00")],
                [appt(OSLER, TUE, "10:00", "cancelled")],
                reply="OK",
            ),
            note="a greeting in front of a write: the appointment must still be "
            "cancelled at the end",
        ),
        case(
            "G-p-06",
            "Cheers - when is my next appointment?",
            [bk("this patient's next appointment", "list_my_appointments")],
            scheduling=sch(
                [appt(VESALIUS, WED, "10:00")],
                [appt(VESALIUS, WED, "10:00", "standing")],
            ),
        ),
        case(
            "G-p-07",
            "Thanks, but I'd rather speak to someone.",
            [req("call_staff", "asks for a person")],
            note="the pleasantry sits in front of a cause that stops the turn",
        ),
        case(
            "G-p-08",
            "Perfect. What time should I arrive?",
            [faq("arrival time", "arrival-time")],
            history=[
                turn(
                    "assistant",
                    "Your appointment with Dr. Osler is booked for Wednesday at noon.",
                )
            ],
            note="'Perfect.' acknowledges the confirmation and the question follows "
            "from it. A turn read as small talk here answers nothing",
        ),
    ],
)


# === q - mixed-faq-booking ==========================================================

family(
    "q",
    "mixed-faq-booking",
    "One FAQ half and one booking half in a single message. Each specialist is handed "
    "only its own segment, so the other half's clause is not in the prompt to be "
    "answered: a booking node that sees the policy question may answer it from "
    "nothing, "
    "and a retrieval that sees the booking clause searches the corpus for live data it "
    "does not hold. Every case's booking half has to leave the scheduler in a stated "
    "state, so a turn that answers the question and quietly drops the booking fails.",
    [
        case(
            "G-q-01",
            "What are your opening hours, and what dentist slots are free tomorrow?",
            [
                faq("clinic hours", "hours-location"),
                bk("dentist availability tomorrow", "check_availability"),
            ],
            scheduling=sch([], []),
        ),
        case(
            "G-q-02",
            "What should I bring, and can you book me Wednesday at 9 with "
            "William Osler?",
            [
                faq("what to bring", "what-to-bring"),
                bk("book Wednesday 9am", "book_appointment"),
            ],
            scheduling=sch([], [appt(OSLER, WED, "09:00", "standing")], reply="OK"),
            note="the booking half names a practitioner and a time, so the bare 'OK' "
            "confirms one slot",
        ),
        case(
            "G-q-03",
            "Which insurers do you accept, and which practitioners do you have?",
            [
                faq("accepted plans", "insurance-plans"),
                bk("the roster", "list_practitioners"),
            ],
            scheduling=sch([], []),
            note="two list questions, one answered from the corpus and one from the "
            "records - the pair a pooled retrieval would answer from one place",
        ),
        case(
            "G-q-04",
            "What are your hours, and please cancel my Friday appointment.",
            [
                faq("clinic hours", "hours-location"),
                bk("cancel Friday", "cancel_appointment"),
            ],
            scheduling=sch(
                [appt(OSLER, FRI, "11:00")],
                [appt(OSLER, FRI, "11:00", "cancelled")],
                reply="OK",
            ),
        ),
        case(
            "G-q-05",
            "Where do I park, and what appointments do I have booked?",
            [
                faq("parking near the clinic", "hours-location"),
                bk("this patient's appointments", "list_my_appointments"),
            ],
            scheduling=sch(
                [appt(VESALIUS, THU, "12:00")],
                [appt(VESALIUS, THU, "12:00", "standing")],
            ),
        ),
        case(
            "G-q-06",
            "What should I bring, and what does Dr. Vesalius specialise in?",
            [
                faq("what to bring", "what-to-bring"),
                bk("a named practitioner's specialty", "list_practitioners"),
            ],
            scheduling=sch([], []),
            note="a practitioner's specialty is live scheduling data; the corpus "
            "never names a practitioner, so a retrieval sent this half abstains",
        ),
        case(
            "G-q-07",
            "How much is a GP visit if I'm paying cash, and is there anything free "
            "on Wednesday morning?",
            [
                faq("GP out-of-pocket rate", "out-of-pocket-rates"),
                bk("Wednesday morning availability", "check_availability"),
            ],
            scheduling=sch([], []),
            note="the booking half names no practitioner - 'GP' sits in the other "
            "clause, so the restatement has to carry it across, and the GP resolves "
            "to one person on the roster",
        ),
        case(
            "G-q-08",
            "What is your address, and can you move my Wednesday visit to the same "
            "time next week?",
            [
                faq("clinic address", "hours-location"),
                bk(
                    "reschedule to next week",
                    "list_my_appointments",
                    "reschedule_appointment",
                ),
            ],
            scheduling=sch(
                [appt(OSLER, WED, "10:00")],
                [appt(OSLER, "+9d", "10:00", "standing")],
                reply="OK",
            ),
            note="'the same time next week' is +9d from a Monday clock",
        ),
    ],
)


# --- the checks JSON Schema cannot make ---------------------------------------------

# The family letters, in the order the set is meant to read. There is no `h`: the
# letters follow the order the set was specified in, and that one was not used.
EXPECTED_LETTERS: Final = list("abcdefgijklmnopq")

# Each seeded practitioner's working week and the hours a 60-minute slot may start in:
# first weekday, last weekday, first hour, last hour.
_WORKING: Final[dict[str, tuple[int, int, int, int]]] = {
    OSLER: (0, 4, 9, 16),
    VESALIUS: (0, 5, 9, 13),
}


class DeclarationError(ValueError):
    """The declared set is not internally consistent; the message names every fault."""


def _number_in_family(case_id: str, letter: str) -> int | None:
    """Return the number `case_id` carries inside family `letter`, or None if malformed.

    The number is the case's identity inside its family, not its position in the list:
    a removed case leaves its number unused for good. Renumbering the cases after it
    would rename labels that had not changed and make every stored run that selected
    them unscoreable, so the numbers ascend without having to be contiguous.
    """
    match = re.fullmatch(rf"G-{letter}-([0-9]{{2}})", case_id)
    return None if match is None else int(match.group(1))


def problems() -> list[str]:
    """Return every inconsistency in the declared set, in reading order.

    Returns: one message per fault, each naming the case or family at fault
    """
    found: list[str] = []
    corpus = {
        entry["id"]
        for entry in json.loads(CORPUS_JSON.read_text(encoding="utf-8"))["entries"]
    }

    letters = [family["letter"] for family in FAMILIES]
    if letters != EXPECTED_LETTERS:
        found.append(f"family order is {letters}, expected {EXPECTED_LETTERS}")

    seen: set[str] = set()
    for family in FAMILIES:
        letter, name = family["letter"], family["name"]
        if not family["tests"].strip():
            found.append(f"{name}: empty 'tests' description")
        numbers: list[int] = []
        for case in family["cases"]:
            case_id = case["id"]
            if case_id in seen:
                found.append(f"{case_id}: duplicate id")
            seen.add(case_id)
            number = _number_in_family(case_id, letter)
            if number is None:
                found.append(f"{case_id}: is not written G-{letter}-nn")
            elif numbers and number <= numbers[-1]:
                found.append(
                    f"{case_id}: does not come after G-{letter}-{numbers[-1]:02d}"
                )
            else:
                numbers.append(number)
            found.extend(_faults_in(case, corpus))
    return found


def _faults_in(case: dict[str, Any], corpus: set[str]) -> list[str]:
    """Check one case's citations, and its fixture against the seeded schedules."""
    found: list[str] = []
    case_id = case["id"]
    requests = case["requests"]

    for request in requests:
        for cited in request.get("cites", []):
            if cited not in corpus:
                found.append(f"{case_id}: cites {cited!r}, not in the corpus pin")

    books = any(request["intent"] == "booking" for request in requests)
    if books != ("scheduling" in case):
        found.append(
            f"{case_id}: a scheduling fixture belongs on exactly a case with a booking "
            f"request (booking={books}, fixture={'scheduling' in case})"
        )

    fixture = case.get("scheduling")
    if fixture is None:
        return found

    for kind in ("given", "expect"):
        for entry in fixture[kind]:
            found.extend(_faults_in_appointment(case_id, kind, entry))
    return found


def _faults_in_appointment(case_id: str, kind: str, entry: dict[str, Any]) -> list[str]:
    """Check one fixture entry against its practitioner's own week and hours."""
    who = entry["practitioner"]
    working = _WORKING.get(who)
    if working is None:
        return [f"{case_id}: {kind} names {who}, who is not seeded"]

    found: list[str] = []
    first_day, last_day, first_hour, last_hour = working
    weekday = int(entry["day"][:-1]) % 7
    if not first_day <= weekday <= last_day:
        found.append(
            f"{case_id}: {kind} puts {who} on day offset {entry['day']} "
            f"(weekday {weekday}), outside their working week"
        )
    if "time" in entry:
        hour = int(entry["time"][:2])
        if not first_hour <= hour <= last_hour:
            found.append(
                f"{case_id}: {kind} starts {who} at {entry['time']}, outside "
                f"{first_hour:02d}:00-{last_hour:02d}:00"
            )
    return found


def build() -> dict[str, Any]:
    """Return the declared set, having checked it.

    Raises: DeclarationError when the declaration is inconsistent, before anything is
        rendered or written
    """
    found = problems()
    if found:
        raise DeclarationError(
            f"{len(found)} problem(s) in the declared set:\n  " + "\n  ".join(found)
        )
    return {"families": FAMILIES}


def render(document: dict[str, Any]) -> str:
    """Return the JSON text of `document`, exactly as `cases.json` holds it."""
    return json.dumps(document, indent=1, ensure_ascii=False) + "\n"


def write(path: Path | None = None) -> tuple[int, int]:
    """Render the declared set over `cases.json`.

    Args:
        path: where to write; the committed `cases.json` when omitted.

    Returns: the number of families written and the number of cases

    Raises: DeclarationError when the declaration is inconsistent, before writing
    """
    document = build()
    (CASES_JSON if path is None else path).write_text(render(document), "utf-8")
    families = document["families"]
    return len(families), sum(len(family["cases"]) for family in families)
