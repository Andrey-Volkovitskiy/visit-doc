"""The starter corpus every new session is given.

A corpus belongs to one session, and this is what that session starts with: the
clinic's own answers, planted once when the session is created, so a first-time visitor
is answered from something rather than handed to staff for every question the clinic
already knows the answer to.

The entries are plain text and carry no schema of their own, and they come in two
shapes. The first nine are one question and its answer each, labelled, because that is
what a retrieved chunk is read as: the question wording is what a patient's own phrasing
is matched against, and the answer is what the generation step is allowed to say. The
rest are the clinic's longer documents - an insurance plan guide, a dental care guide,
test preparation and so on - several sections each, so they are split into several
chunks, sit beside entries on neighbouring subjects, and make retrieval choose between
near neighbours rather than between nine unrelated answers. Nothing here is privileged
once it is planted - a session may edit or delete any of these exactly as if it had
typed them in, and one it deleted is gone.
"""

import textwrap

# Spec 007 FR-039b required a new session's corpus to start empty and deferred "a
# starting template" to later work; this module and the seeding step in
# `chat.api.provisioning` are that work, and supersede that requirement.


def _document(source: str) -> str:
    """Return a document written as an indented block as the text an entry holds.

    Lines are wrapped in the source to fit it; in the entry, each paragraph is one line,
    so the text a chunk carries and a model reads has no hard breaks mid-sentence. A
    heading (`#`) and a list item (`- `) start a line of their own, and a list item's
    continuation lines join it.
    """
    paragraphs: list[str] = []
    for block in textwrap.dedent(source).strip().split("\n\n"):
        lines: list[str] = []
        for raw in block.splitlines():
            line = raw.strip()
            if (
                lines
                and not line.startswith(("#", "- "))
                and not lines[-1].startswith("#")
            ):
                lines[-1] = f"{lines[-1]} {line}"
                continue
            lines.append(line)
        paragraphs.append("\n".join(lines))
    return "\n\n".join(paragraphs)


# One entry per string, and the order is the order the console lists them in. Kept as a
# tuple so nothing can append to the corpus a session is about to be given by mutating
# a module-level list.
DEFAULT_FAQ_ENTRIES: tuple[str, ...] = (
    (
        "Question: Do I need a referral from a primary care doctor to book with a "
        "specialist?\n"
        "Answer: You can book a visit with any of our practitioners without a referral."
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
        "Shield, Aetna, Cigna, UnitedHealthcare, and Medicare. "
        "If you're unsure whether your specific plan is covered, you can ask me to "
        "connect you with a member of our friendly front desk team. "
        "They'll be happy to help clarify your coverage."
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
        "Willson St. The nearest free parking is a 3-minute walk away at the Mega Mall "
        "parking garage (9 Willson St.)."
    ),
    (
        "Question: How early should I arrive before my scheduled appointment time?\n"
        "Answer: If it is your first visit to our clinic, please arrive 15 minutes "
        "before your appointment time to complete your registration paperwork. "
        "Returning patients can arrive 5 to 10 minutes prior to their slot."
    ),
    _document(
        """
        # Insurance plan guide

        This guide lists, plan by plan, the health insurance plans we are in-network
        with. Insurance companies sell many plans under one brand name, and being
        in-network with a company does not mean being in-network with every plan it
        sells. Check the plan name printed on the front of your insurance card - it
        usually appears next to the company logo or under "Plan" - and compare it with
        the lists below.

        ## Blue Cross Blue Shield

        We are in-network with Blue Cross Blue Shield PPO plans, including BlueCard PPO
        members whose plan was issued in another state; look for the suitcase logo with
        the letters "PPO" on your card. The Federal Employee Program (FEP) Basic Option
        and Standard Option plans are in-network too. We are not in-network with Blue
        Essentials HMO or Blue Advantage HMO plans, because those plans only cover care
        from their own medical groups.

        ## Aetna

        We are in-network with Aetna Choice POS II and Aetna Open Access Managed Choice.
        Aetna Medicare Advantage PPO plans are in-network as well. Plans administered by
        Aetna Signature Administrators use a separate network, and we are not part of
        it.

        ## Cigna

        We are in-network with Cigna Open Access Plus (OAP) and Cigna PPO plans. Cigna
        LocalPlus plans are in-network only for members who live in our county; the
        network area is printed on the back of the card. Cigna Connect plans bought
        through the health insurance marketplace are not in-network.

        ## UnitedHealthcare

        We are in-network with UnitedHealthcare Choice Plus and UnitedHealthcare Options
        PPO. UnitedHealthcare Navigate HMO is not in-network. AARP Medicare Advantage
        plans from UnitedHealthcare are in-network when the plan is a PPO, and not
        in-network when it is an HMO.

        ## Medicare and Medicaid

        We accept Original Medicare (Part B) for medical visits. Medicare does not cover
        routine dental care, so dental appointments are self-pay for Medicare patients
        unless they also hold one of the dental plans listed in our dental care guide.
        We do not currently accept Medicaid or Medicaid managed care plans.

        ## Copays and deductibles

        Your plan decides how much you pay at the visit. A copay is a fixed amount your
        plan sets for each visit, and it is collected when you check in. If your plan
        has a deductible you have not met yet, you pay the amount your plan allows for
        the visit at check-in. The front desk can estimate that amount for you if you
        give them your plan details before the visit.

        ## Updating your insurance

        If your insurance has changed since your last visit, tell the front desk at
        check-in and fill in Form IC-2, the Insurance Update Form, so the claim goes to
        the right plan. A new card can take up to two business days to verify. If you
        give us the new details at least two business days before your appointment, we
        can confirm your coverage before you arrive.
        """
    ),
    _document(
        """
        # Dental care guide

        ## What a dental appointment covers

        A routine dental appointment includes an examination of your teeth and gums and
        a professional cleaning. Fillings and extractions are usually done at a separate
        appointment, once the dentist has examined you and explained the treatment.

        ## Dental insurance

        Medical insurance plans do not cover dental care. For dental appointments we
        accept these dental plans: Delta Dental PPO, MetLife PDP Plus and Guardian
        DentalGuard Preferred. We do not accept dental HMO (DHMO) plans, including Cigna
        Dental Care DHMO. If you do not hold one of the accepted dental plans, dental
        appointments are self-pay.

        ## Before your appointment

        Tell the dentist before any treatment if you take a blood-thinning medicine such
        as warfarin, apixaban (Eliquis) or clopidogrel (Plavix), or if you have ever
        taken a bisphosphonate such as alendronate (Fosamax) for bone density. These
        medicines change how the dentist plans an extraction. Do not stop taking them
        before your appointment unless the doctor who prescribed them tells you to.
        Brush and floss as usual on the day.

        ## Feeling anxious about the dentist

        Let us know when you book or when you check in. The dentist will agree a stop
        signal with you - raising your left hand - and will pause whenever you use it.
        Noise-cancelling headphones are available in the treatment room. We do not offer
        sedation of any kind, including nitrous oxide ("laughing gas") and IV sedation;
        a patient who needs sedation for dental treatment has to be treated at a dental
        practice that provides it.

        ## After a filling

        Your mouth may stay numb for two to four hours. Do not eat or drink anything hot
        until the numbness wears off, so you do not burn or bite yourself without
        noticing. A tooth with a new filling can be sensitive to cold for a week or two.
        If the sensitivity lasts longer, or your bite feels high when you close your
        teeth, contact the front desk so the dentist can adjust the filling.

        ## After a tooth extraction

        Bite firmly on the gauze pad for 30 to 45 minutes to let a blood clot form. Do
        not rinse your mouth for the first 24 hours; after that, rinse gently with warm
        salt water (half a teaspoon of salt in a glass of water) after meals. Do not
        drink through a straw, smoke or spit forcefully for at least 72 hours, because
        suction can dislodge the clot and cause a painful condition called dry socket.
        Eat soft food on the other side of your mouth for the first few days. For pain,
        use the over-the-counter painkiller the dentist recommended at your appointment.

        Contact us if the bleeding has not stopped after four hours, or if the pain gets
        worse rather than better after the third day. If bleeding is heavy and will not
        stop, go to the nearest emergency department.

        ## Services we do not offer

        We do not offer teeth whitening, orthodontic treatment such as braces or clear
        aligners, or dental implants.
        """
    ),
    _document(
        """
        # Preparing for tests at the clinic

        Some tests are done at the clinic during a regular appointment. This guide
        explains how to prepare for each of them. If your doctor has given you different
        instructions, follow those instead.

        ## Electrocardiogram (ECG)

        A resting 12-lead ECG records the electrical activity of your heart through ten
        small sticky electrodes placed on your chest, arms and legs. It takes about ten
        minutes and does not hurt. On the day, do not put body lotion, oil or powder on
        your chest, because the electrodes do not stick well to it. Wear a two-piece
        outfit so you only need to remove your top. You can eat, drink and take your
        usual medicines before an ECG.

        ## Spirometry (breathing test)

        Spirometry measures how much air you can breathe out, and how fast. You will
        blow hard into a mouthpiece several times while sitting down. Before the test:

        - do not use a short-acting reliever inhaler, such as salbutamol (Ventolin) or
          terbutaline (Bricanyl), for 4 hours;
        - do not use a long-acting inhaler, such as tiotropium (Spiriva) or salmeterol
          (Serevent), for 24 hours;
        - do not smoke for at least 1 hour;
        - avoid a large meal in the 2 hours before the test, because a full stomach
          makes it harder to breathe out fully;
        - avoid vigorous exercise for 30 minutes.

        If you become short of breath while holding off your inhaler, use it, and tell
        the staff when you arrive so the result can be read correctly. Wear loose
        clothing that does not restrict your chest.

        ## Urine sample

        If you have been asked to bring a urine sample, collect a sterile container from
        the front desk beforehand; household containers can contaminate the sample.
        Unless you were told otherwise, collect the first urine of the morning, catching
        the middle part of the stream. Write your full name and date of birth on the
        label. Bring the sample to the clinic within 2 hours. If that is not possible,
        keep it in the fridge - not the freezer - for up to 24 hours.

        ## 24-hour blood pressure monitoring

        For ambulatory blood pressure monitoring you wear a cuff on your upper arm,
        connected to a small recorder on a belt, for 24 hours. It inflates automatically
        every 30 minutes during the day and every hour at night. Wear a loose
        short-sleeved or sleeveless top to the fitting appointment. You cannot shower or
        bathe while wearing the monitor, so do it before you come in. Keep your arm
        still and relaxed by your side while the cuff inflates. Return the monitor to
        the front desk the next day by 10:00 AM.
        """
    ),
    _document(
        """
        # Vaccinations

        ## Seasonal flu vaccine

        We give the flu vaccine every year from the start of September until the end of
        March. Patients aged 65 and over are offered a high-dose vaccine (Fluzone
        High-Dose), which gives a stronger immune response at that age. The flu vaccine
        can be given at the same appointment as a COVID-19 vaccine.

        ## COVID-19 vaccine

        We offer the updated COVID-19 vaccine for the current season made by
        Pfizer-BioNTech (Comirnaty). We do not stock the Moderna (Spikevax) or Novavax
        vaccines.

        ## Tetanus, diphtheria and whooping cough (Tdap)

        Adults need a tetanus booster every 10 years. We use Boostrix, which also
        protects against diphtheria and whooping cough (pertussis).

        ## Travel vaccinations

        Book a travel vaccination appointment at least six weeks before you leave,
        because some vaccines need more than one dose or take time to become effective.
        Bring your travel dates and the list of countries you are visiting. We offer:

        - hepatitis A (Havrix): two doses six to twelve months apart, and one dose
          before travel protects you for the trip;
        - typhoid, either as an injection (Typhim Vi) given at least two weeks before
          travel, or as an oral vaccine (Vivotif) of four capsules taken on alternate
          days and finished at least one week before travel;
        - hepatitis B (Engerix-B): three doses over six months.

        We do not offer the yellow fever vaccine, which can only be given at a
        registered yellow fever vaccination centre. We also do not offer rabies or
        Japanese encephalitis vaccines.

        ## Self-pay vaccine prices

        If your insurance does not cover a vaccine, these are the self-pay prices: flu
        vaccine $35, high-dose flu vaccine $70, Tdap $55, hepatitis A $85 per dose,
        typhoid injection $90, oral typhoid course $95, hepatitis B $70 per dose. They
        are the price of the vaccine itself, and are charged in addition to the rate for
        the visit.

        ## After your vaccination

        Please stay in the waiting area for 15 minutes after any vaccination, so staff
        can help if you feel faint or have an allergic reaction. A sore arm for a day or
        two is common. Bring your vaccination record to the appointment so the new dose
        can be added to it.
        """
    ),
    _document(
        """
        # Running late

        ## If you are running late

        Call the front desk on (212) 555-0142 as soon as you know you will be late, so
        we can tell the practitioner and plan around it. The phone line is answered
        Monday through Saturday from 8:30 AM to 6:00 PM.

        If you arrive up to 10 minutes after your appointment time, you will still be
        seen, but the appointment may be shorter so that the patients booked after you
        are not kept waiting. If you arrive more than 15 minutes late for a GP or
        specialist appointment, or more than 10 minutes late for a dental appointment,
        we may not be able to see you, and you will be asked to choose a new time.
        Dental appointments have the shorter limit because the treatment room has to be
        prepared and cleaned between patients.

        ## If the clinic is running late

        Sometimes an earlier appointment takes longer than planned. If your practitioner
        is running more than 20 minutes behind, the front desk will tell you when you
        check in, and you can choose to wait or to pick a new time. When we know about a
        delay of more than 30 minutes before you leave home, we send you a text message
        instead.

        ## Checking in on time

        Check in at the front desk or at the self-check-in kiosk as soon as you arrive.
        Your arrival is recorded when you check in, not when you enter the building.
        """
    ),
    _document(
        """
        # Appointment reminders and messages from the clinic

        ## Text message reminders

        We send a reminder by text message 48 hours before your appointment and another
        2 hours before it. Our messages always come from the short code 72913 and start
        with "VisitDoc Clinic". The 48-hour reminder includes a check-in QR code you can
        scan at the self-check-in kiosk when you arrive. To stop receiving reminder
        texts, reply STOP to any of them. To start them again, reply START, or tell the
        front desk at your next visit.

        ## Email

        We use email only to confirm a new appointment and to send an itemized receipt
        you have asked for. We do not send test results or medical advice by email.

        ## Phone calls

        If we cannot reach you by phone, we leave a voicemail with only the clinic's
        name, a callback number and your first name - never the reason for the call or
        any health details.

        ## Messages we will never send

        We will never ask for your card number, bank details, Social Security number or
        insurance member ID by text message or email. If you receive a message like that
        which claims to come from us, do not reply to it, and call the front desk on
        (212) 555-0142.

        ## Changing your phone number or email address

        Tell the front desk at check-in, or update the contact section of Form PR-1 at
        the self-check-in kiosk. Reminders are sent to the phone number we hold when the
        reminder goes out.
        """
    ),
    _document(
        """
        # Checking in and registering

        ## Registering as a new patient

        New patients fill in three forms at their first visit:

        - Form PR-1, New Patient Registration: your contact details, emergency contact
          and insurance information;
        - Form PR-4, Medical History Questionnaire: your past illnesses, operations,
          current medicines and allergies;
        - Form HC-9, Consent to Treatment.

        You can fill them in on the tablet at the self-check-in kiosk, or on paper at
        the front desk. Most people take about 10 minutes over them. Returning patients
        update Form PR-1 only when their details have changed, and complete Form PR-4
        again once a year.

        ## The self-check-in kiosk

        Returning patients can check in at the kiosk beside the front desk instead of
        queuing. Scan the QR code from your 48-hour reminder text, or type in your last
        name and date of birth. The kiosk cannot take payments: any copay or self-pay
        amount is paid at the front desk.

        ## Language interpreters

        Free telephone interpreting is available in more than 200 languages for any
        appointment. Ask for it when you book or when you check in; there is no need to
        bring a family member to interpret. An American Sign Language (ASL) interpreter
        can attend in person if you ask at least 72 hours before your appointment.

        ## Bringing someone with you

        You may bring one adult companion into the consulting room. Anyone else who
        comes with you can wait in the waiting area.

        ## While you wait

        Free guest Wi-Fi is available in the waiting area: the network is called
        VisitDoc-Guest, and the password is printed on the card at the front desk.
        Please set your phone to silent and take calls outside. Photography and video
        recording are not allowed in the waiting area, to protect other patients'
        privacy.
        """
    ),
    _document(
        """
        # Your privacy and your health information

        ## Who can see your medical record

        Your record can be seen only by the practitioners and staff involved in your
        care at the clinic, and by the staff who handle billing for your visits. We
        share information from your record with your insurance company only as far as
        is needed to process claims for your care. We never share your health
        information with your employer, and we never sell it.

        ## Conversations with the chat assistant

        Conversations with the clinic's chat assistant are kept with your appointment
        records. Clinic staff can read them, and a staff member may join a conversation
        to help you. Do not use the chat to report a medical emergency: call 911 or go
        to the nearest emergency department.

        ## Recording a consultation

        You may make an audio recording of your own consultation for your personal use,
        as long as you tell the practitioner before you start. Staff and other patients
        must not be recorded or photographed.

        ## How long we keep records

        We keep adult medical records for 10 years after your last visit.

        ## Questions and complaints about privacy

        Our privacy officer answers questions about how your information is used and
        handles privacy complaints. Ask the front desk for the privacy officer's contact
        details.
        """
    ),
    _document(
        """
        # General practice services

        ## Annual physical examination

        An annual physical is a preventive check-up for adults who feel well. It
        includes a review of your medical history and current medicines; your blood
        pressure, heart rate, height, weight and body mass index (BMI); a physical
        examination; and a discussion of the screening tests and vaccinations
        recommended for your age. Most insurance plans cover one preventive physical a
        year at no cost to you, but check with your plan.

        ## Long-term conditions

        If you live with a long-term condition such as type 2 diabetes, high blood
        pressure, asthma or COPD, your GP will agree a regular review schedule with you,
        usually every three or six months. Bring your home blood pressure or blood
        glucose readings to each review, and bring your inhalers if you have asthma or
        COPD, so the GP can check your technique.

        ## Ear wax removal

        We remove ear wax by microsuction, which uses a small medical vacuum instead of
        water. For three to five days before the appointment, put two or three drops of
        olive oil or sodium bicarbonate ear drops into the affected ear twice a day, to
        soften the wax. Do not use cotton buds. Microsuction is not suitable if you have
        a perforated eardrum, so tell us when you book if you have ever had one.

        ## Minor procedures

        We freeze common warts and verrucas with cryotherapy (liquid nitrogen). A wart
        may need two to four treatments, about three weeks apart. We do not remove skin
        tags, moles or other skin lesions for cosmetic reasons.

        ## Medical examinations for work, driving and sport

        We do not carry out commercial driver (DOT) medical examinations, pilot medicals
        or immigration medical examinations. A sports physical before joining a team or
        a competition is done in a standard GP appointment.
        """
    ),
)
