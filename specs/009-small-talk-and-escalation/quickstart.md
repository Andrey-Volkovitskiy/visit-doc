# Quickstart: Small Talk and What Escalation Is For (Phase 1f)

A manual walk-through proving the phase end to end in the real UI. Read before a demo; it is not
automated, and automating it would remove the thing it is for.

## Prerequisites

```bash
make services-up          # chat, scheduler, frontend in the background
make services-status      # all three up; logs and pids under .run/
make migrate              # only if the dev databases are behind — this phase adds no migration
```

Open the app: patient chats on the left, staff console on the right. Create a chat. `scripts/dev-chat.sh`
drives the same flows from a terminal if you would rather not click.

## 1 — A pleasantry is answered, and nobody is called (US1)

1. Send `Hi`.
2. Expect: a greeting, possibly offering help in general terms. **No citations.** No abstention
   message.
3. Check the staff console: the conversation is **not** in the queue and the message carries no mark.
4. Check the log: `intent.classified` shows `["small_talk"]`; there is no retrieval line and no
   `escalation.raised` for this turn.
5. Send `Thanks!`, then `asdfgh`. The first is acknowledged; the second asks you to rephrase. Neither
   calls anyone.

## 2 — The greeting does not swallow the request (US2)

1. Send `Hi, do I need a referral?`.
2. Expect the corpus-backed answer with citations in the console — the ordinary FAQ turn.
3. Send `Thanks! Any slots on Monday?` and expect the booking path, not an acknowledgement.

## 3 — "OK" means two things (US3)

1. Ask a question whose answer ends in arrival instructions, then reply `OK`. Expect small talk;
   no appointment.
2. Start a booking, let the assistant offer a specific slot, then reply `OK`. Expect the booking to
   complete. Verify it exists:
   ```bash
   docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_scheduler \
     -c "select id, starts_at, status from appointments order by created_at desc limit 3;"
   ```

## 4 — Not authorized: a person, without silence (US4)

1. Send `I would like you to prepare a sick leave paper for my employer`.
2. Expect the fixed notice — not authorized, forwarded to staff, ask anything else meanwhile.
3. Console: the conversation is in the queue, and the message shows **"Not something the assistant
   may do"**, not "No answer in the clinic's documents".
4. Send `What are your opening hours?` next. It is **answered** — this cause does not silence.
5. Now send `What are your opening hours, and can you write me a sick note?` in a fresh chat: one
   reply that both answers the hours and says the note went to staff.

## 5 — The three that stop the conversation (US5)

Run each in its own chat.

1. `My chest hurts and I feel faint` → reply points at emergency services and says staff are
   notified. Console mark: **"Urgent condition"**.
2. `I'm terrified about my results` → a person is called. Console mark: **"Patient in distress"**.
3. `Can I book Monday for my mother?` → nothing is booked. Console mark: **"Booking for someone
   else"**. Confirm no appointment was created with the query in step 3 above.
4. In any of the three, send another message. It gets **no reply**, and shows "Arrived while the
   assistant was silent". The assistant switch in the console shows the conversation escalated.
5. Post a staff reply in it. The mark clears and the conversation stops being escalated — but the
   assistant does **not** answer the very next message: a staff post starts the existing 120-second
   pause, so `assistant_may_reply` stays false until it expires. That is spec 007's behavior, not
   this phase's; wait it out (the console shows the countdown) and the next message is answered
   normally. *(Corrected 2026-09-08: this step used to claim the reply resumed immediately.)*

## 6 — Near misses do not stop anything

1. `My wife recommended you, can I book Tuesday?` → an ordinary booking.
2. `Last year I had a heart attack, do I need a referral?` → an ordinary FAQ answer.
3. `Can I book Monday morning for <the chat's own patient name>?` → the booking path asks who the
   appointment is for, rather than stopping.

## 7 — Nothing else regressed

1. Ask a **clinic** question the corpus cannot answer — "Do you have an MRI scanner on site?", not
   "what is the weather in Paris?" → abstention, staff called as a corpus gap, assistant still free
   to answer the next question (mark: "No answer in the clinic's documents", permanent). *(Corrected
   2026-09-08: an off-topic question is classified `small_talk` and deflected politely, so it never
   reaches the abstention path and does not exercise this step.)*
2. Ask for a human explicitly → today's hand-off sentence, conversation silent.
3. A mixed FAQ + booking message → both halves answered in one merged reply, as before.

## Teardown

```bash
make services-down
```

Sessions are ephemeral; deleting the session from the admin surface removes its chats, patients and
appointments together.
