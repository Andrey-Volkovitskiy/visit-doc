# Quickstart: Escalation and the Staff Console (Phase 1d, part 2)

**Feature**: `007-escalation-and-staff-console` | **Date**: 2026-09-01 | **Plan**: [plan.md](./plan.md)

Validates all five user stories end to end, plus the four failure modes that are easy to get wrong
and invisible in a happy path: a save that fails at each of its three steps, a superseded revision
being retrieved, a partial session delete, and an escalation that must **not** silence.

Everything 006's quickstart set up still applies. This feature adds no service and no database, but
unlike 006 it does add **two environment variables**, **two migrations**, and a great deal of
frontend — so read the prerequisites rather than assuming the previous ones cover it.

---

## Prerequisites

### 1. Two new settings

Add to the repo-root `.env` (both services read that one file — see `.env.example`):

```bash
# Guards the admin deletion routes. UNSET OR EMPTY REFUSES EVERY REQUEST (FR-048a) — that is the
# correct default, not a misconfiguration, unless you intend to use them.
ADMIN_SECRET=local-dev-admin-secret
# Where the practitioner console proxy forwards to (FR-036) — the scheduler's practitioner REST
# API. Defaults to this value, so it only needs setting if the scheduler is not on localhost:8001.
SCHEDULING_HTTP_BASE_URL=http://localhost:8001
```

`FAQ_MAX_ENTRIES_PER_SESSION` defaults to 200 and does not need setting. To exercise Scenario 12
without typing 200 entries, set it to `2` temporarily.

### 2. Start everything

```bash
make db-up
uv sync
make run-scheduler-dev    # no migration for this feature; the scheduler's schema is unchanged
make run-chat-dev         # alembic upgrade head runs BOTH new chat migrations
make run-frontend-dev
```

### 3. Confirm the two migrations landed

This is the whole schema change, and most of what follows depends on it:

```bash
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_chat -c "\d chats"
docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_chat -c "\d faq_entries"
```

**Expected on `chats`**: `escalated_at`, `escalation_reason`, `assistant_paused_until`,
`attention_since`, all nullable; a CHECK making `escalated_at` and `escalation_reason` null
together; and `ix_chats_session_attention`.

**Expected on `faq_entries`**: `session_id` (FK to `sessions`, `ON DELETE CASCADE`) and
`live_revision`, both **`NOT NULL`**, and **no** CHECK constraint — there is nothing left to
constrain once neither can be null. Also expect the table to be **empty**.

If `escalated_at`'s CHECK is missing, Scenario 3 will appear to pass while leaving the state it
exists to prevent representable. If either `faq_entries` column came out nullable, the migration
ran without its deletion step and an ownerless entry is representable again.

### 4. Everything you had is gone — deliberately

**This deployment resets the system** (FR-039e). Migration 2 deleted every session, chat, message and
FAQ entry in `visitdoc_chat`; the scheduler's data-only revision deleted every patient, practitioner
and appointment. One step is **manual** and is not done for you — drop the Qdrant collection, which
the next chat-service start recreates with its new payload indexes:

```bash
curl -X DELETE localhost:6333/collections/faq_chunks
```

Do this **before** starting the chat service. If you skip it, the collection keeps points that carry
no `revision`, and while no live-revision filter can match them, you will be testing against a store
whose contents the schema no longer describes.

Your browser still holds a ~400-day cookie naming a session that no longer exists. That needs no
action: an unrecognized cookie is already treated as a first arrival, so you simply get a new
session on the next load — which is also Scenario 13 step 5, arrived at early.

**Your first FAQ question will abstain, escalate, and silence that conversation** (FR-003c) — that is
the designed behaviour on an empty corpus, and Scenario 5 is where you fix it. If you want a corpus
before you start, note that `/faq` is session-scoped now and needs the cookie:

```bash
curl -c /tmp/vd.jar -X POST localhost:8000/chats           # mint a session, keep its cookie
curl -b /tmp/vd.jar -X POST localhost:8000/faq \
  -H 'content-type: application/json' \
  -d '{"content":"Bring your ID and any current medication list to your first appointment."}'
```

A `POST /faq` with no cookie now creates nothing. That is FR-039, not a regression.

---

## Scenario 1 — Reach a human, and be answered by one (US1, P1)

1. In the patient pane, in any chat: **"Can I speak to someone about my bill?"**
2. **Expected**: the assistant says a staff member has been notified and will reply in this
   conversation, and **names no timeframe** (FR-005). The turn completes normally.
3. Look at the staff pane, without reloading. **Expected**: within ~2 seconds the conversation is
   emphasized and sorted to the top, the attention total reads 1, and the assistant switch on that
   conversation is already **off** (FR-017, US2 scenario 9) — you do not have to infer the silence.
4. Send another message in the same chat: **"It's the second charge on the March invoice."**
5. **Expected**: the message appears in the thread and **no reply is generated** — no typing, no
   tokens, nothing. On the staff pane the message carries an **unanswered** mark and the
   conversation stays emphasized (FR-019).
6. Confirm nothing ran, rather than trusting the absence of a reply:

   ```bash
   # In the chat service's log stream, for that turn:
   #   message.unanswered  is present
   #   intent.classified   is ABSENT
   #   turn.retrieval_completed is ABSENT
   ```

   This is SC-002, and it is the assertion the whole silencing design exists to make true.
7. Open the conversation in the staff pane and read it. **Expected**: the whole thread — patient,
   assistant, and any staff messages — in one ordered list (FR-025); and it is **still** emphasized,
   because reading is not answering (FR-029a).
8. Post as staff: **"I've credited that charge — it was a duplicate."**
9. **Expected**, all at once: the reply appears in the **patient's own thread**, in order, labelled
   **Staff** — and the assistant's earlier replies in that same thread now read **AI assistant**, so
   the two are distinguishable at a glance. No person's name appears anywhere, and no staff
   identifier is present in the response body (FR-021, FR-023, SC-011c). The escalated mark is gone;
   the emphasis is gone;
   the unanswered mark on step 4's message is gone; and the attention total is back to 0.
10. **Expected** in the patient pane, without reloading or sending anything: the staff reply is
    there within ~2 seconds (SC-004).

---

## Scenario 2 — The assistant does not talk over you (US3, P3)

In a **different, never-escalated** conversation — the point is that a pause needs no escalation.

1. Post as staff: **"Hi, I'm looking at your file now."**
2. **Expected**: the switch for that conversation goes off and shows a countdown from 2:00
   (FR-013, FR-017b).
3. **Reload the page.** **Expected**: the countdown is still running, with the correct time
   remaining — not restarted and not vanished (FR-018, SC-006). Open a second tab: both count down
   together.
4. As the patient, send a message. **Expected**: it lands, is marked unanswered, emphasizes the
   conversation, and gets no reply (FR-015).
5. Post as staff again before the timer runs out. **Expected**: the countdown restarts at 2:00
   (FR-014).
5a. **Take a conversation without saying anything.** In a third conversation nobody has touched,
    turn the assistant switch **off**. **Expected**: the same two-minute countdown starts, with no
    message added to the thread. A patient message sent now is kept, marked unanswered, and not
    answered — indistinguishable from a pause a staff reply started, which is the point (FR-017b).
5b. Turn it off again with 20 seconds left. **Expected**: the countdown restarts at 2:00.
5c. With a reply mid-stream, turn the switch off. **Expected**: the generation stops and no part of
    it is kept or shown — the same outcome as a staff message landing mid-stream (FR-017c).
5d. In an emphasized conversation holding an unanswered message, turn the switch off, then on again.
    **Expected**: the emphasis and the mark are untouched throughout. **Neither** direction answers
    a patient (FR-029a) — if either cleared them, the two axes were collapsed somewhere.
6. Let it expire without acting. As the patient, ask an ordinary FAQ question.
   **Expected**: it is answered normally (FR-016).
7. **Expected, and the easiest thing to get wrong**: the assistant answers **only** the question you
   just asked. It does **not** go back and answer the message from step 4, even though that message
   is an unanswered patient message immediately preceding this one and 003's burst-merging rule
   would otherwise pull it in (FR-019a, FR-019b). Check the reply addresses one question, and check
   `turn.message_received`'s `message_ids_unified` holds **one** id.

### 2a — Cancelling a reply mid-sentence

1. As the patient, ask a question that produces a long answer.
2. While the tokens are still streaming, post as staff into that conversation.
3. **Expected**: the generation stops immediately, and **no part of that reply is kept or shown**.
   Reload the thread: it holds the patient's message and the staff reply, with nothing between them
   (FR-013a, SC-002c).

---

## Scenario 3 — Hand the conversation back without replying (US3, P3)

1. Escalate a conversation as in Scenario 1, and do **not** reply to it.
2. Wait longer than two minutes. **Expected**: it is **still** silent and still escalated. An
   escalation has no deadline (FR-009, SC-002b) — this is the one behaviour that most looks like a
   bug and is most deliberately not one.
3. Turn the assistant switch **on**.
4. **Expected**: the assistant may speak again, **and** the conversation is still emphasized with its
   unanswered marks intact — nobody answered the patient (FR-017b, FR-029a). This is the assertion
   that the two axes really are separate; if turning the switch on cleared the emphasis, research #1
   was implemented as one column.
5. As the patient, send a message. **Expected**: it is answered.

---

## Scenario 4 — A failure calls staff but never silences (US1 5a, P1)

1. Stop the scheduler: `Ctrl-C` its process.
2. As the patient: **"Can I book something for Tuesday?"**
3. **Expected**: the assistant says it could not complete that; the message is marked **assistant
   failed**; the conversation is emphasized and the attention total rises.
4. **Expected, and this is the whole scenario**: the switch is still **on**. Ask an ordinary FAQ
   question in the same conversation — it is answered (FR-003d, SC-009f). A transient outage must
   not cost a conversation its assistant.
5. Restart the scheduler and retry the booking. **Expected**: it succeeds normally.
6. Post one staff message into that conversation. **Expected**: the emphasis clears, and the
   *assistant failed* mark is **still there** — permanently. A staff member answering the patient
   does not mean the failure did not happen (FR-027c, SC-009f).

### 4a — A refusal is not a failure

1. With the scheduler running, book a slot, then try to book the **same** slot again.
2. **Expected**: the assistant explains the refusal and offers alternatives, exactly as it does
   today, and **zero** conversations are marked or emphasized (FR-003a, SC-009e). Refusals and
   failures are the line this feature draws; if a refusal escalates, the mapping in
   `contracts/agent-tools.md` was implemented against `status != "ok"` rather than against the three
   named failure statuses.

---

## Scenario 5 — Give the assistant something to answer from (US5, P5)

1. Open the FAQ screen. **Expected**: an empty list, shown plainly as empty — not an error, and not
   somebody else's entries (FR-039d).
2. Add: *"Parking is free for the first two hours in the visitor car park behind the building."*
3. **Expected**: it is listed with the text you typed, and **no per-entry retrievability indicator is
   shown anywhere** (FR-040). Every entry listed is answerable; a badge that can never say "no" is
   worse than none.
4. As the patient, ask: **"Where do I park?"** **Expected**: a grounded answer citing that entry.
5. Edit the entry to say *four* hours. Ask again. **Expected**: the answer says four, and the
   citation carries the new text (US5 scenario 2).
6. Delete it and ask again. **Expected**: the assistant abstains — **and that abstention escalates
   the conversation and silences it** (US5 scenario 4, FR-003).

---

## Scenario 6 — Cross-session isolation of the corpus (SC-011a)

1. Open a **second browser profile** (not a second tab — you need a second session) and add an entry
   with different text.
2. In each session, ask a question the other session's entry would answer.
3. **Expected**: each answers only from its own, cites only its own, and neither can retrieve,
   cite, or count the other's toward groundedness (FR-039a, SC-011a).
4. Delete the entry in one session. **Expected**: the other session's answers are completely
   unchanged.

---

## Scenario 7 — A failed save costs nothing (US5 3a/3b, SC-015a)

The three failure points, each of which must leave the entry exactly as it was.

**Fail the embedding** — set `VOYAGE_API_KEY` to a wrong value and restart the chat service:

1. Edit an existing entry.
2. **Expected**: the edit is reported as failed, naming the unavailable dependency; the entry still
   shows its **previous** text; and the assistant still answers from that previous text (FR-042a).
   Nothing was written to either store.

**Fail the retrieval store** — restore the key, then `docker stop visitdoc-qdrant`:

3. Edit the entry again.
4. **Expected**: reported as a failed save that can be retried; the entry keeps its previous text and
   the assistant keeps answering from it throughout (FR-042e). **Confirm no revert happened** — the
   old behaviour re-indexed and rewrote the row, and this design performs no compensating write at
   all.

**Retry, and converge**:

5. `docker start visitdoc-qdrant`, wait for it, and submit the same edit again.
6. **Expected**: it succeeds, with no manual repair of the index, and the assistant answers from the
   new text (FR-042g, SC-015c). Submit it once more: nothing changes, no duplicate entry appears,
   and the same single revision stays live.

---

## Scenario 8 — A superseded revision is never answered from (SC-015b)

1. Edit one entry several times, changing a distinctive word each time.
2. Ask a question it answers, repeatedly. **Expected**: every answer and every citation carries the
   **latest** text — never a previous revision's, not once (FR-042d).
3. Inspect the index directly, and expect leftovers rather than cleanliness:

   ```bash
   curl -s localhost:6333/collections/faq_chunks/points/scroll \
     -H 'content-type: application/json' -d '{"limit":100,"with_payload":true}' | jq '.result.points[].payload'
   ```

   **Expected**: possibly several `revision` values for one `faq_entry_id` — superseded chunks that
   the sweep has not reached, or that a failed save left behind. **This is correct** (FR-042i): they
   are unreachable, they are never cited, and the entry's next successful save or the session's
   deletion clears them. Leaked storage is the accepted failure mode; a lost answer is not.

---

## Scenario 9 — Manage practitioners without a command line (US4, P4)

1. On the practitioner screen, add one with **every field left blank**. **Expected**: a name is
   assigned from the seeded pool and shown back to you (FR-035, US4 scenario 1).
2. Edit their weekly schedule. As the patient, ask what times they have. **Expected**: the offered
   times match what the screen shows, on the next question (FR-037, SC-014).
3. Try to add a second practitioner with a name already in use. **Expected**: refused with a reason
   you can read, and **nothing changes**. That refusal came from the scheduler, not from the screen
   (FR-035, SC-013).
4. Book an appointment with a practitioner, then delete them. **Expected**: they and their
   appointments are gone, and the assistant no longer offers them.
5. **Confirm the credential never reached the page.** With the app open, in the browser console:

   ```js
   document.cookie   // must NOT contain visitdoc_session_id
   ```

   **Expected**: empty, or anything but the session id (FR-036, SC-012). Then check the network tab:
   every practitioner request goes to **your own backend**, never to `:8001`.

---

## Scenario 10 — Both panes, one screen, no login (US2, SC-017)

1. Reload the whole app. **Expected**: no authentication prompt anywhere (FR-031).
2. With the staff pane visible, raise an escalation from the patient pane and answer it from the
   staff pane. **Expected**: under 60 seconds of interaction, with no refresh at any point (SC-017).
3. While working in the patient pane, check the attention total is still visible (FR-028).
4. Open the app in **two tabs of the same profile**. Escalate in one. **Expected**: the other shows
   it; a pause started in one counts down in both from the same figure; and no mark is present in one
   and absent in the other (FR-029c, Edge Cases).

---

## Scenario 11 — Three conversations, and the order of the list (US2 scenario 5)

1. Escalate three conversations, a few seconds apart.
2. **Expected**: all three emphasized and above the unescalated ones, with the one escalated
   **longest ago first** (FR-027).
3. Reply to the middle one. **Expected**: it leaves the emphasized group; the other two keep their
   order; the total drops by exactly one.
4. Create a conversation, escalate it, and let it also accumulate two unanswered messages.
   **Expected**: it counts **once** toward the total, not three times (Edge Cases).

---

## Scenario 12 — The corpus cap (US5 scenario 7, SC-015e)

Set `FAQ_MAX_ENTRIES_PER_SESSION=2` and restart the chat service.

1. Add two entries. Add a third. **Expected**: refused, with a message saying the corpus is full and
   an entry has to be removed first (FR-039f). Confirm **nothing** was stored or indexed — the
   refusal happens before chunking and embedding.
2. **Expected**: editing and deleting the two existing entries still work normally; the assistant
   still answers from them.
3. Delete one, then add the third. **Expected**: it succeeds immediately.
4. Add ten practitioners and several chats. **Expected**: none refused for count — only the corpus is
   capped (FR-039g, SC-015e).

Restore `FAQ_MAX_ENTRIES_PER_SESSION` afterwards.

---

## Scenario 13 — The admin surface (FR-046 to FR-052)

1. Refuse by default:

   ```bash
   curl -i -X DELETE localhost:8000/admin/sessions/$SESSION_ID          # no header
   curl -i -X DELETE localhost:8000/admin/sessions/$SESSION_ID -H 'X-Admin-Secret: wrong'
   ```

   **Expected**: both `403`, both with the identical body, neither saying which part was wrong
   (FR-048, SC-019).
2. Confirm they are invisible:

   ```bash
   curl -s localhost:8000/openapi.json | grep -c admin
   ```

   **Expected**: `0`. A generated documentation page listing them would defeat FR-049 without anyone
   noticing (SC-019a).
3. Delete for real, with the header, and verify both stores:

   ```bash
   curl -X DELETE localhost:8000/admin/sessions/$SESSION_ID -H "X-Admin-Secret: $ADMIN_SECRET"
   docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_chat \
     -c "SELECT count(*) FROM chats WHERE session_id = '$SESSION_ID';"
   docker exec visitdoc-postgres psql -U visitdoc -d visitdoc_scheduler \
     -c "SELECT count(*) FROM practitioners WHERE session_id = '$SESSION_ID';"
   ```

   **Expected**: `status: "deleted"` with counts, and zero rows on both sides. The session's FAQ
   entries went by cascade and its chunks were swept (SC-018).
4. **The partial outcome.** Stop the scheduler, then delete another session.
   **Expected**: that session is reported `"incomplete"`, **not** as a success (FR-051, SC-020).
   Restart the scheduler and re-run the same deletion: it completes without error and leaves the same
   end state.
5. Return to the app in that browser profile. **Expected**: it behaves exactly as a first arrival —
   a cookie naming a session that no longer exists is already handled that way, and nothing new was
   invented for it (Edge Cases).

---

## What is deliberately not testable here

- **Out-of-band notification** — no email, no SMS, nothing outside the application (FR-043).
- **Staff booking on a patient's behalf** — staff reply and manage practitioners and FAQ entries;
  they gain no scheduling capability (FR-044).
- **Analytics over the console** — volumes, response times, escalation rates are Phase 3+ (FR-045).
- **Retention** — nothing expires, ages out, or is redacted. Admin deletion is the only removal
  path and may never be exercised (FR-045a).
- **Accessibility and localization** — no keyboard-navigation, screen-reader, or contrast criterion
  applies to any screen above, and the app is English only (FR-045b). Emphasis is visual weight and
  has no non-visual equivalent, which is a stated cost rather than a gap.
