"""The starter corpus every new session is given.

A corpus belongs to one session, and this is what that session starts with: the
clinic's own answers, planted once when the session is created, so a first-time visitor
is answered from something rather than handed to staff for every question the clinic
already knows the answer to.

The entries are plain text and carry no schema of their own. Each is one question and
its answer, labelled, because that is what a retrieved chunk is read as: the question
wording is what a patient's own phrasing is matched against, and the answer is what the
generation step is allowed to say. Nothing here is privileged once it is planted - a
session may edit or delete any of these exactly as if it had typed them in, and one it
deleted is gone.
"""

# Spec 007 FR-039b required a new session's corpus to start empty and deferred "a
# starting template" to later work; this module and the seeding step in
# `chat.api.provisioning` are that work, and supersede that requirement.

# One entry per string, and the order is the order the console lists them in. Kept as a
# tuple so nothing can append to the corpus a session is about to be given by mutating
# a module-level list.
DEFAULT_FAQ_ENTRIES: tuple[str, ...] = (
    (
        "Question: Do I need a referral from a primary care doctor to book with a "
        "specialist?\n"
        "Answer: You can book a specialist appointment without a referral."
    ),
    (
        "Question: What should I bring to my first appointment?\n"
        "Answer: Please bring a valid photo ID, your insurance card, and any relevant "
        "prior lab or test results related to your case."
    ),
    (
        "Question: Do you offer virtual or telehealth consultations?\n"
        "Answer: No, we currently only offer in-person visits at our clinic location."
    ),
    (
        "Question: Which health insurance plans do you accept?\n"
        "Answer: We accept most major insurance providers, including Blue Cross Blue "
        "Shield, Aetna, Cigna, UnitedHealthcare, and Medicare. Please contact our "
        "front desk to verify your specific coverage."
    ),
    (
        "Question: What should I do if my insurance isn't listed or I am "
        "out-of-network?\n"
        "Answer: You can pay out-of-pocket for your visit, and we can provide you with "
        "an itemized receipt to submit to your insurer for potential reimbursement."
    ),
    (
        "Question: How much will my visit cost if I am paying out-of-pocket?\n"
        "Answer: Out-of-pocket rates are $120 for a General Practitioner (GP) visit, "
        "$180 for a Dentist appointment, and $160 for all other specialist "
        "consultations."
    ),
    (
        "Question: When is payment due, and what payment methods do you accept?\n"
        "Answer: Payment is due at the time of service. We accept cash, major credit "
        "and debit cards (Visa, Mastercard, American Express), and Flexible Spending "
        "Account (FSA) / Health Savings Account (HSA) cards."
    ),
    (
        "Question: What are your clinic hours and locations?\n"
        "Answer: We are open Monday through Saturday from 9:00 AM to 6:00 PM at 15a "
        "Willson St. The nearest parking is a 3-minute walk away at the Mega Mall "
        "parking garage (9 Willson St.)."
    ),
    (
        "Question: How early should I arrive before my scheduled appointment time?\n"
        "Answer: If it is your first visit to our clinic, please arrive 15 minutes "
        "before your appointment time to complete your registration paperwork. "
        "Returning patients can arrive 5 to 10 minutes prior to their slot."
    ),
)
