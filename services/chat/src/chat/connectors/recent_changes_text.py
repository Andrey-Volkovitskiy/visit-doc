"""The sentence the recent-changes tool offers Claude to relay as it stands.

"Three appointments were booked, one cancelled and two rescheduled." Built by one pure
function so its wording is pinned by a table of tests rather than by whatever a model
makes of the counts.
"""

from chat.repositories.booking_act_repository import RecentBookingChanges

_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
          "nine", "ten")  # fmt: skip
_NOTHING = "No appointments were booked, cancelled or rescheduled."


def _number(count: int) -> str:
    """A count as a person says it in a sentence: words to ten, digits beyond."""
    return _WORDS[count] if 0 <= count < len(_WORDS) else str(count)


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]


def _joined(clauses: list[str]) -> str:
    """Join clauses with commas and a final "and"."""
    if len(clauses) == 1:
        return clauses[0]
    return f"{', '.join(clauses[:-1])} and {clauses[-1]}"


def describe_recent_changes(changes: RecentBookingChanges) -> str:
    """Say what the counts say, the way a person would.

    The operations come in a fixed order - booked, cancelled, rescheduled - and those
    with a zero count are left out. The subject agrees with the first count, and later
    clauses borrow its verb. An unknown outcome adds a second sentence sending the
    reader to the console, the one place its details are.
    """
    counts = [
        (count, verb)
        for count, verb in (
            (changes.booked, "booked"),
            (changes.cancelled, "cancelled"),
            (changes.rescheduled, "rescheduled"),
        )
        if count > 0
    ]
    if counts:
        (first, first_verb), rest = counts[0], counts[1:]
        subject = (
            "One appointment was"
            if first == 1
            else f"{_capitalized(_number(first))} appointments were"
        )
        clauses = [f"{subject} {first_verb}"] + [
            f"{_number(count)} {verb}" for count, verb in rest
        ]
        sentence = f"{_joined(clauses)}."
    else:
        sentence = _NOTHING
    unknown = changes.outcome_unknown
    if unknown == 0:
        return sentence
    changed = (
        "One change has"
        if unknown == 1
        else f"{_capitalized(_number(unknown))} changes have"
    )
    return f"{sentence} {changed} an unknown outcome — check the Staff Console."
