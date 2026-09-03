# Feature Specification: Escalation and the Staff Console (Phase 1d, part 2)

**Feature Branch**: `007-escalation-and-staff-console`

**Created**: 2026-08-31

**Status**: Draft

**Input**: User description: "Create spec for Phase 1d (part 2)" — the second half of Phase 1d as
`docs/ROADMAP.md` defines it: the escalation path (`escalate_to_staff` added to Phase 1c's tool
registry), staff posting directly into the patient's own thread, escalation as a state on the
conversation rather than on a message, and the staff-facing interface that makes all of it visible —
the session's conversations with the escalated ones marked, in-app notification, practitioner
management, and FAQ entry management, on one screen beside the patient chat.

## Clarifications

### Session 2026-08-31

- Q: What makes the assistant escalate on its own, given that FR-003's "something it cannot answer
  from its own capabilities" is not testable as written? → A: **Two triggers and no others — an
  explicit patient request, or the FAQ path failing to produce a grounded answer.** The second is
  the existing abstention: retrieval is judged insufficient and the turn abstains *before* any
  generation call, so the trigger is a deterministic signal already computed on every FAQ turn
  rather than a model judgment. This pulls forward the wiring `docs/ROADMAP.md` describes under
  Phase 1e ("an explicit abstention path that escalates via 1d's `escalate_to_staff` tool"): 1e
  replaces the *gate* that decides sufficiency, not the escalation it triggers, so building the
  caller here leaves 1e strictly less to do rather than pre-empting it. A booking refusal is
  explicitly **not** a trigger — a refusal is an answer, and the assistant already offers
  alternatives for one (FR-003, FR-003a).

- Q: The console puts a delete button on a corpus every session answers from, and there is no login
  (FR-031) — so any anonymous visitor could empty it for everyone. Does the corpus stay clinic-wide?
  → A: **No — the corpus becomes session-scoped.** Each session holds its own, its edits are
  invisible to every other session, and its corpus dies with it (FR-039, FR-039c). This removes the
  exposure outright rather than mitigating it, and it removes the last exception from FR-032, so
  *every* noun in the system is now scoped the same way. The cost is named rather than waved
  through: retrieval itself now carries the session predicate as a filter on the search (FR-039a).
  Where a session's corpus *comes from* is settled separately below.

- Q: Does the console list only escalated conversations, or all of the session's? → A: **All of
  them — and the whole escalation model changes with it.** Staff can read and post into every
  conversation in their session; escalated ones are marked and shown first (FR-024, FR-027). The
  escalated mark stops being a mode that silences the assistant and becomes an attention marker,
  cleared by the first staff reply, so answering *is* taking the conversation (FR-008, FR-009a). What actually stops the assistant talking
  over a person is a **2-minute pause started by any staff message**, escalated or not, restarted by
  each further staff message, ended early by a control staff can see counting down, and otherwise
  lifting by itself (FR-013 to FR-018). A patient message arriving while the assistant is silent
  marks that message **unanswered** and emphasizes the conversation, and only a staff reply clears
  it (FR-029a, FR-027c) — so the mark never means "not looked at": reading a conversation does not
  clear it, because nothing about reading it answered the patient.
- Q: Does the escalated mark itself silence the assistant, or only a staff message? → A: **It
  silences the assistant, indefinitely, until a person deals with it.** So there are two silencing
  states with deliberately different shapes: an escalation has **no deadline**, because time passing
  is not a person answering, and a pause has a **2-minute** one, because a staff member who has
  stopped typing has finished (FR-009, FR-016). The first staff message ends the escalation and
  starts the pause in the same act. The one thing this answer forces that was not asked for: an
  escalation nobody replies to would otherwise silence a conversation permanently, so FR-017's
  return-to-the-assistant control works in both states — showing a countdown where there is one,
  and simply ending the escalation where there is not (FR-009a).

- Q: What does the patient see when they send a message the assistant will not answer? → A:
  **Nothing extra — and the corresponding staff-side fact is made explicit instead.** The message
  lands in the thread and stays there; the turn completes without a reply rather than failing. A
  banner or acknowledgement would be inventing UI to explain something the surrounding messages
  already explain, and a persistent "staff are handling this" indicator would leak the pause to the
  patient. On the staff side the opposite applies: whether the assistant may speak is shown at all
  times as a **switch**, so a staff member opening a conversation a patient just escalated sees it
  already off rather than inferring the silence, and can turn it back on at any moment — even with a
  patient message still unanswered (FR-017 to FR-017b). One rule follows that was not obvious: the
  assistant **never** goes back and answers what arrived while it was silent (FR-019a). That
  deliberately overrides the existing merge-consecutive-unanswered-messages behavior, which would
  otherwise have the assistant answer a question a staff member had already handled (FR-019b).
- Q: What happens to an assistant reply already streaming when a staff message lands in that
  conversation? → A: **It is cancelled immediately and the partial reply is discarded entirely** —
  never persisted, never left standing beside the staff message (FR-013a). This is the same
  cancel-and-discard the service already performs when a new patient message supersedes a running
  turn, so it reuses that mechanism rather than adding one. It is deliberately the opposite of
  FR-006, which lets an *escalating* turn finish: there, nothing else is competing to speak; here a
  person is actively talking, and two simultaneous answers to one question are worse for the patient
  than one answer cut short.

- Q: Does an escalation carry a reason, and is it shown to staff? → A: **Yes — exactly two, the
  same two FR-003 already defines**: the patient asked for a person, or retrieval abstained. The
  reason travels with the escalation, is shown on the conversation in the console, and is carried in
  the escalation record (FR-007a, FR-027a, FR-033). It adds no new taxonomy — the triggers are the
  reasons — and the two are genuinely different work: one is a person wanting a person, the other is
  a hole in the corpus, which the staff member can fix on the FAQ screen. Counting the second is
  also the signal Phase 1e wants when tuning thresholds. No generated summary accompanies it: the
  thread is what says what the patient wanted, and a summary that can be wrong would be a second,
  less reliable account of it.

- Q: A new session's corpus is empty, so its first FAQ question abstains — does that escalate and
  silence the conversation, as FR-003 and FR-009 say? → A: **Yes, with no special case.** An
  abstention is an abstention however few entries the corpus holds, and carving out "the corpus is
  empty" would give one value two meanings — an abstention that escalates and an abstention that
  does not — with nothing but a row count separating them. The consequence is accepted and stated
  rather than hidden (FR-003c): on a brand-new session, the first FAQ question hands its
  conversation to staff and the assistant goes quiet in it. That is the honest behavior of an
  assistant with no clinic documents, and the intended first move is to add entries on the FAQ
  screen — or to answer as staff, which demonstrates the feature this phase is about.

- Q: Escalation is a tool the agent calls, but the abstention trigger is a deterministic gate with
  no model turn in it — is escalation one capability or two paths? → A: **One capability, several
  callers** (FR-001a). The model invokes it as a registry tool for an explicit request; the
  abstention gate and the failure path invoke the same handler directly. One implementation means
  the callers cannot drift apart in what they record or what state they leave, and no model call is
  spent re-deciding something a gate already decided — which would also let the model decline to
  escalate after the system had concluded it could not answer.
- Q: What does a staff member actually see about *why* a conversation needs them, and how long does
  it stay visible? → A: **A mark on the message it concerns, with a lifetime that depends on what
  kind of mark it is** (FR-027a to FR-027e). Four kinds: the patient asked for a person, the corpus
  could not answer, the assistant failed, and the message went unanswered while the assistant was
  silent. Hovering one says which. Two of them are **requests for a person** and are cleared by a
  person speaking — any staff message in that conversation clears every one of them. The other
  two — corpus could not answer, assistant failed — are **records of a system gap** and stay
  forever, because a staff member answering the patient does not mean the corpus gained the entry it
  was missing. At conversation level all four look the same for now: the chat is emphasized in the
  list, and a staff message removes the emphasis (FR-029).
- Q: Does a failed booking escalate, given FR-003a said it did not? → A: **A genuine failure does; a
  refusal still does not.** Scheduling unreachable, a write whose outcome is unknown, and a tool
  error are failures — the patient asked for something and the system cannot say what happened, so a
  person should see it. A refusal is an *answer*: the slot is taken, the time is outside the
  practitioner's hours, and 006 already requires an alternative to be offered with it. FR-003a
  narrows rather than disappears (FR-003, FR-003a).

### Session 2026-09-01

- Q: Trigger 3 (the assistant failed) escalates, and FR-009 silences the assistant on every
  escalation — so a scheduling blip silences the conversation until a human replies. Is that
  intended? → A: **No. A failure marks and emphasizes, but never silences** (FR-003d). The assistant
  stays available, and the patient can simply retry — which for a transient outage is the fastest
  route to what they wanted. This also resolves a live contradiction between SC-001a and SC-009e,
  which had said the opposite things about unreachable-service runs. The structural consequence is
  that **two axes that looked like one are now plainly separate**: which reason *silences* the
  assistant, and which mark is *permanent*, are different questions with different answers, and they
  do not line up (FR-027c's grid). Calling staff stays one capability with one implementation; the
  reason it carries is what decides whether silence follows.

- Q: Sessions already exist, minted before this feature and holding ~400-day cookies — what happens
  to a returning visitor whose session has no staff member and owns no FAQ entries? → A: *(Answered
  here by tolerating them, and **superseded** by the reset entry below, which removes them instead.
  Both halves of the original answer are now moot: there is no staff member to derive, and there is
  no pre-existing session to return to.)*
- Q: Is a staff *entity* really needed, given a session has one and only one staff member? → A:
  **No — it is identified by the session** (FR-022). Everything it carried was a display name;
  nothing referenced it by identifier, since a staff message is identified by its sender and its
  chat's session. Storing it would have meant a one-to-one table with a single meaningful column, a
  uniqueness constraint for an invariant that construction already guarantees, and a migration for
  every session minted before this feature. *(Superseded in part by the next entry, which removes
  the display name too — so the record is not merely unnecessary, there is nothing left for it to
  hold.)*

- Q: Does the staff member need a **name** at all? → A: **No — a message is labelled by its role,
  not by a person** (FR-021, FR-023). A staff message reads **"Staff"** in the patient's own thread
  and an assistant message reads **"AI assistant"**; the patient's own messages stay unlabelled, as
  they are today. This removes the last thing the staff member carried, and with it a chain of
  machinery the previous answer had accepted: no name pool, no derivation from the session
  identifier, no requirement that the derivation be stable across restarts, and no `staff_names`
  module. What is left is not a lightweight staff *entity* but **none at all** — "the session's
  staff member" collapses entirely into the value `staff` on a message's sender. It also removes an
  ambiguity the name introduced rather than solved: a fictional person's name on a clinic reply
  invites a patient to believe some specific human is answering them, where a role label states
  exactly what is true and no more. The cost the previous answer named — "a staff member cannot be
  renamed as a patient can" — becomes moot: there is nothing to rename. FR-022a and SC-011c are
  **withdrawn**, and FR-023 is repurposed from the name pool to the two labels.

- Q: FR-039c requires a session's corpus to go when the session does, but nothing deletes sessions —
  the requirement can never fire and corpora accumulate forever. → A: **Add admin deletion paths**
  (FR-046 to FR-052): delete one named session, or delete all of them, guarded by a single admin
  secret held in environment configuration. Three things follow that are worth stating rather than
  discovering. It is **not a user role** — FR-031 stands, patients and staff still never log in, and
  this surface is maintenance that the console never links to (FR-049). It **crosses the service
  boundary**: the scheduling service today can delete one patient for a chat and one practitioner,
  but has no session-level delete, so a new capability is needed there (FR-047, Dependencies). And
  because two independent stores are involved with no transaction spanning them, a partial delete is
  reachable and MUST be reported as one rather than as success (FR-051) — the same rule 006 applies
  to a write whose outcome is unknown.

- Q: How is "currently retrievable" determined, and how can an entry diverge from its chunks at all?
  → A: **It is read from the entry's live revision, and the divergence is made unreachable rather
  than detected (FR-040, FR-042b).** The bad state is reachable today because a save is destructive:
  the row is written first, the re-index deletes the old chunks before writing the new ones, and
  when it fails the compensating revert performs two more writes and swallows its own failures — so
  it can leave the row holding new text while the index holds old text, both present, with nobody
  told. Rather than detect that state, this feature removes the ability to reach it. Indexed chunks
  become **immutable and additive**: a save writes its chunks under a **new revision** and deletes
  nothing, and the entry's stored row names the one revision that is **live**. Retrieval searches
  live revisions only. An entry is retrievable exactly when its row names a live revision, so
  "saved" and "searchable" stop being two facts that can disagree, and the screen has nothing to
  reconcile.

- Q: Then where does a failure land? → A: **On the previous revision, which is still live and still
  correct (FR-042a, FR-042c, FR-042e).** Chunking and embedding happen before any store is written,
  so the likeliest failure in the path changes nothing anywhere. The chunk write adds a new revision
  without touching the old one, so a failure there leaves the entry answering exactly as it did
  before. One local commit then stores the new content and names the new revision live — the single
  moment the change becomes visible — and if that fails the save simply did not happen, and is
  reported as a failed save with the entry still retrievable on its previous text. No compensating
  write exists because none is needed: content is written once, in the commit that publishes it, and
  a failure before that commit changed nothing anyone can see. What is left behind is chunks nobody
  points at.

- Q: So it leaks orphaned points into the retrieval store? → A: **Yes, and that is the trade, taken
  deliberately in the safe direction (FR-042h, FR-042i).** A superseded revision is unreachable —
  retrieval filters to live revisions, so its chunks cannot be searched, cited, or counted toward
  groundedness — it only occupies storage. Sweeping it is never load-bearing: correctness was
  settled by the commit, so a failed sweep is a leak and not a bug, the sweep is idempotent, and it
  converges, since the entry's next save and the session's deletion each sweep what earlier ones
  left. Weighed against the alternative, leaked storage is invisible and cheap while a lost answer
  is not: under a destructive save, one failed edit takes a working entry out of service until a
  person repairs it.

- Q: Would a readiness flag — *pending* while a change is in flight, *ready* once the index is
  written — be simpler? → A: **It was this spec's answer, and it is withdrawn.** It is the right
  answer while a save destroys the old chunks first, because something then has to record *an
  operation is in flight*: the two stores cannot express the difference between an index mid-update
  and an index settled. Additive revisions dissolve the question — nothing that is live is ever
  mid-update, since a revision is either published by the commit or it is not — and with it the
  four things the flag dragged along: a state machine on the entry, a rule excluding pending entries
  from retrieval, a content rollback that is itself a fallible write, and a human retry that a
  working entry's availability depended on. The earlier objection to a stored flag, that it would
  duplicate a fact the stores already determine, therefore stands after all. The live revision is
  not such a flag: the index holds several revisions of an entry and cannot say which is current, so
  the entry says.

- Q: Or solve it with a transactional outbox? → A: **Considered and deliberately not used, and no
  longer needed for correctness.** It is the standard answer to a dual-write problem, and would turn
  "the staff member retries" into "the system converges by itself". Three reasons against it here,
  in order of weight. `docs/ROADMAP.md` puts the outbox — with a broker and idempotent consumers —
  in **Phase 3+**, and Principle I of the constitution forbids pulling a platform layer forward;
  that is binding on its own. It needs a background worker this phase has and needs nothing else
  for. And there is no correctness hole left for it to close: publishing a revision is a
  single-store commit, not a dual write, so the only thing an outbox would automate is the sweep of
  superseded chunks — housekeeping that is already idempotent and already converges (FR-042h). The
  seam is left in the right shape for it should that sweep ever want a worker.

- Q: What identifies the chunks a sweep removes — and what removes a revision that was written but
  never published, which no commit ever superseded? → A: **The sweep is scoped to one entry:
  delete that entry's chunks whose revision is not the live one.** The chunk payload therefore
  carries its entry's identity alongside its revision, and the stored row keeps only the live
  revision — no history of superseded ones is kept, because the predicate needs none. One predicate
  covers both cases: a revision superseded by a later commit and a revision that was written and
  never published are both simply "not live", so the retry that follows a failed save sweeps the
  failed attempt as a matter of course. A **session-wide** sweep was considered — it would also heal
  leftovers on entries that are never saved again — and rejected: its predicate would delete a
  concurrent save's chunks during the window between their write and the commit that publishes them
  (FR-042c), publishing a revision whose chunks no longer exist. The per-entry predicate cannot
  reach that state, because the staleness guard already fails any competing commit on the same
  entry. Making the session-wide form safe would need a grace period on a chunk's write time, and
  the only thing it buys is reclaiming storage nobody can reach, on entries the session's deletion
  already clears (FR-039c).

- Q: With revisions, can an entry the console lists ever *not* be retrievable — and if not, what is
  the per-entry retrievability indicator still for? → A: **It cannot, and the indicator is dropped.**
  A row names a live revision only if a save published one, and a published revision always has at
  least one chunk: the content validator already rejects meaningless text, and the chunk filter uses
  the same check on slices of it, so a character that makes the whole content meaningful survives in
  whichever chunk holds it. Every listed entry is therefore retrievable, and an indicator that can
  never say "no" is worse than none — it trains the staff member to watch a signal that cannot fire.
  The guarantee is stated once as an invariant the write path upholds (FR-040, FR-041) rather than
  repeated per row, and User Story 5 is re-premised on it. One boundary is accepted rather than
  covered: an index that loses data *outside* this write path — a restore, a recreated collection —
  would leave rows vouching for chunks that are gone, and nothing here detects that. Verifying each
  listing against the index would catch it, at a payload read per entry per listing, and is not
  worth that for an operational event this project does not otherwise handle.

- Q: How large can one session's corpus get? Retrieval now carries the session's live revisions as a
  filter term on every FAQ turn, and nothing bounds how many there are. → A: **A hard cap of 200
  entries per session, enforced when an entry is created (FR-039f) — and scoped to the corpus
  alone.** The cap exists because a specific mechanism needs bounding: FR-042d makes every FAQ turn
  carry the session's live revisions as a filter term, so corpus size sits on the hot path of the
  feature's core behavior. It is deliberately **not** a general anti-abuse rule, and the same
  reasoning is not extended to the other things a session accumulates. Practitioners, chats, and
  their messages stay uncapped, with admin deletion (FR-046) as their only bound, even though
  nobody logs in (FR-031) and the abuse argument would read identically — because no design here
  depends on their size, and a feature about escalation is not where a rate-limiting story belongs.
  What the cap buys as a side-effect is a bounded console listing and bounded per-session corpus
  storage; what it does not claim to be is protection.

- Q: Retrieval now reads the session's live revisions from the stored rows before searching. An
  empty result and a failed read look identical downstream — both yield no revisions. How are they
  told apart? → A: **They MUST produce different outcomes (FR-042j).** An empty set is the ordinary
  starting state of every session and abstains and escalates exactly as an empty corpus does
  (FR-039b, FR-003c). A failed read is an unreachable dependency, and MUST be reported as one —
  never as an abstention, because an abstention tells the patient the corpus does not answer their
  question, which is a claim nothing verified. Collapsing the two is the "one value, one meaning"
  failure the project's own principles name, and it is the likely accident here rather than a
  theoretical one. A plan-level simplification is available and preferred: the turn already writes
  the patient's message to the same store before retrieval runs, so reading the live revisions
  there means an outage fails the turn before the FAQ path is entered and the ambiguity never
  arises. That is an implementation route to the requirement, not a substitute for it.

- Q: Where does the staleness guard's expected revision come from — the request, or the client that
  loaded the entry? → A: **The request (FR-042c).** The server reads the live revision when the
  operation begins and guards its commit on that, with no client involvement and no change to the
  API's shape. The guard exists to protect the **index** during the window between a chunk write and
  the commit that publishes it, and a revision read inside the request covers that window exactly.
  Carrying the revision the *client* loaded — an `If-Match` shape — would additionally prevent one
  person's two browser tabs from overwriting each other, which is a different problem: a lost update
  at human scale, not an index that disagrees with its row. FR-031 gives each session exactly one
  staff member, so there is no second person to conflict with, and solving tab-versus-tab would put
  revisions into the API contract and revision-tracking into the frontend for a P5 story. It stays
  available as a later upgrade, since the revision already exists and would only need exposing.

- Q: A sweep that keeps failing leaks storage with no signal at all. Should its failure be logged?
  → A: **No — it is swallowed entirely (FR-042h).** Nothing is logged, and in particular the
  critical dependency event that the rest of the FAQ path raises on a retrieval-store failure MUST
  NOT fire for it, since that event means an operation could not be completed and a sweep is not one.
  The leak this leaves is bounded on three sides: a session holds at most 200 entries (FR-039f), the
  entry's next successful save sweeps it, and deleting the session removes everything regardless
  (FR-039c). Silence is the deliberate choice, not an oversight — the sweep is housekeeping, and
  housekeeping that reports nothing cannot be mistaken for an operation that failed.

- Q: Entries that predate session ownership belong to no session, so neither the per-entry sweep nor
  the session delete ever removes them or their chunks. What clears them? → A: *(Answered here by
  accepting them as inert leftovers, and **superseded** by the reset entry below: the deployment
  removes them, so the question does not arise.)*

- Q: Two of the answers above spend their effort *tolerating* data that predates this feature — an
  ownerless corpus that nothing removes, a returning visitor whose session owns nothing. Is
  tolerating it the right call? → A: **No — the deployment removes it instead** (FR-039e). Every
  session that exists before this feature ships is deleted, along with everything it owns in every
  store: its chats and messages, its FAQ entries and their indexed chunks, and its patients,
  practitioners and appointments on the scheduling side. Nothing is migrated and nothing is given an
  owner.

  What this buys is not tidiness, it is a **smaller schema and one fewer representable state**. With
  no ownerless rows possible, an entry's owning session and its live revision become **required**
  rather than optional, and the two-armed CHECK that was holding them consistent collapses into two
  `NOT NULL`s. "An entry that belongs to nobody" stops being a state the retrieval filter has to
  exclude and becomes a state that cannot be written — which is the same move this feature already
  makes for the disagreement between a row and its chunks (FR-040, FR-042b), applied one level up.
  FR-039e is rewritten from "tolerate them" to "there are none", and the two clarifications above
  are superseded rather than edited, so the reasoning that was replaced stays visible.

  The cost is stated plainly rather than assumed: this is **destructive and not reversible**, and it
  is defensible only on the precondition FR-045a already states — synthetic data, fictional
  patients, no real clinical content, and nothing anyone needs back. Against real data it would be
  unacceptable, and the nullable-column design that was just discarded is what it would have to
  return to. A returning visitor is not a new case: their cookie names a session that no longer
  exists, which the system already treats as a first arrival.

- Q: What should this capability be called? → A: **Admin**, replacing *operator*. It is the ordinary
  word for destructive maintenance held behind a shared secret, and it reads correctly to anyone
  arriving at `ADMIN_SECRET` or `DELETE /admin/sessions` without context. It carries one risk the
  previous name did not, and FR-049 is strengthened to close it: *admin* strongly implies an account,
  and there is none — no login, no session, no view, and no third kind of user. The term also
  collides with an established use in this project, where "the admin surface" has meant the
  scheduling service's practitioner CRUD since Phase 1c; this feature's documents therefore call
  that one **the practitioner REST API** throughout, so "admin" names exactly one thing.

- Q: Where do the admin deletion capabilities live? FR-048 says the request carries a secret and
  FR-049 says the console never links to them, but not what surface they sit on. → A: **HTTP routes
  on the core backend, guarded by the secret on the request (FR-048a).** They are ordinary routes on
  the same public surface as the chat, which makes four things requirements rather than choices, all
  of them cheap and all of them easy to get wrong by default. The secret travels in a **header**, not
  a query string, because query strings reach access logs and browser history where FR-050's
  redaction does not follow them. The comparison is **constant-time**, so a refusal leaks nothing
  about how much of the secret was right. The routes are **absent from any API schema the
  application publishes**, since a generated documentation page would otherwise make them
  discoverable and defeat FR-049 without anyone noticing. And an **unset or empty secret refuses
  everything** rather than admitting everything — a deployment that forgot to configure it must fail
  closed. A management command was considered, which would have avoided the public route entirely,
  and the route was chosen for being callable against a running deployment without shell access
  to it.

- Q: This stores what patients say about their own care, and nothing in the spec says how long any
  of it is kept. What is the retention position? → A: **There is none, and that is declared out of
  scope rather than left to be inferred (FR-045a).** Nothing expires, ages out, or is redacted:
  sessions carry ~400-day cookies, conversations and their messages persist for as long as their
  session does, and admin deletion (FR-046) is the only removal path — which may never be
  exercised. The reasoning is that this is a portfolio project on synthetic data, with fictional
  patients drawn from a name pool and no real clinical content, so a retention policy would be
  ceremony over data that represents nobody. The point of stating it is that the *absence* of a
  policy is now a recorded decision with its precondition attached — synthetic data only — rather
  than a gap a reader has to assume was considered. Anything closer to real patient content would
  make retention, redaction, and an expiry path prerequisites, not enhancements.

- Q: This feature adds the project's largest UI surface — a second pane, a conversation list with
  emphasis, a switch with a countdown, two admin screens — and the spec says nothing about
  accessibility or localization. What is the position? → A: **Both out of scope, and the app is
  English only (FR-045b).** No accessibility target, no keyboard-navigation or screen-reader
  requirement, no contrast criterion, and no carve-out — including for emphasis, which FR-029
  defines as visual prominence and which therefore has no non-visual equivalent. That consequence is
  stated rather than glossed: a screen-reader user cannot perceive the staff side's primary signal.
  All copy, and the assistant's own answers, are English, with no translation layer and no
  provision for one. The reasoning is the project's own scope discipline — this is a portfolio
  project judged on its applied-AI core, and the constitution puts effort there when it must be
  traded — so both are declared boundaries with a named cost rather than gaps someone later
  mistakes for oversights.


- Q: The switch only turns the assistant back **on**. Should staff be able to turn it off too? → A:
  **Yes, and it starts the ordinary 2-minute pause** (FR-017b, FR-017c). An earlier answer made the
  control one-directional, reasoning that FR-017b asks for only one direction and that the way to
  silence the assistant is to say something. That is true of the *usual* case and wrong about the
  case that matters: a staff member who opens a conversation to read it properly, or to work out
  what to say, has taken it just as surely as one who has started typing — and under the
  one-directional rule they had no way to say so, so the assistant could answer the patient out from
  under them mid-thought. The switch was also, honestly, a switch that could not be switched.

  What makes this cheap is that **off introduces no new state**: it writes the same
  `assistant_paused_until` deadline a staff message writes, so a pause a person asked for and a
  pause a message caused are the same pause — same duration, same storage, same expiry, same
  countdown, and nothing downstream has to tell them apart. FR-013's rationale generalizes rather
  than bending: the trigger was never "a person speaking", it was **a person taking the
  conversation**, and speaking is simply the usual way of doing that.

  Two consequences are stated rather than left to be found. Turning the assistant off MUST cancel a
  reply already streaming, on FR-013a's exact terms — a staff member who has just said "not you"
  should not then watch it finish a sentence (FR-017c). And **neither** direction touches emphasis
  or marks (FR-029a): taking a conversation is not answering it, and handing it back is not
  answering it either.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reach a human when the assistant cannot help (Priority: P1)

A patient raises something the assistant is not the right answer for — a billing dispute, a
complaint, a question about their own care. Instead of guessing, the assistant hands the
conversation to the clinic's staff, tells the patient it has done so, and stops replying in that
conversation. The staff member finds the conversation waiting for them, reads the whole exchange the
patient has already had, and writes back **into that same thread** — the patient sees the reply
arrive in the conversation they were already in, labelled plainly as coming from **Staff** rather
than from the assistant, with no new window, no email, and nothing restated.

**Why this priority**: This is the entire point of the escalation path, and everything else in the
feature exists to serve it. Until staff can answer, a handoff is a dead end — the assistant says
"someone will get back to you" and nobody can. It stands alone: a clinic whose assistant can hand
off to a person who actually replies is already more useful than one that cannot, even with no
marking, no notification, and no admin screens.

**Independent Test**: In one chat, ask to speak to a person. Verify the assistant says it has handed
the conversation to staff and that the conversation is marked as escalated; send another message and
verify no assistant reply is generated. Then, from the staff side, open that conversation, see the
patient's full history, post a reply, and verify it appears in the patient's own thread labelled
**Staff**, distinguishable at a glance from the assistant's own replies, which read
**AI assistant**.

**Acceptance Scenarios**:

1. **Given** an ordinary open conversation, **When** the patient asks to talk to a human, **Then**
   the assistant hands the conversation to staff, tells the patient in that same turn that a staff
   member has been notified and will reply in this conversation, and names no timeframe.
2. **Given** a conversation that has just been escalated, **When** the patient sends another
   message, **Then** the message is kept, shown in the thread, marked unanswered, and its
   conversation emphasized, and no assistant reply is generated for it — no intent classification, no retrieval, no tool
   call, and no answer — however long it is left.
3. **Given** an escalated conversation, **When** the staff member posts a message into it, **Then**
   that message appears in the patient's own thread, in order, labelled **Staff** — not by a
   person's name, and not indistinguishable from an assistant reply.
4. **Given** an escalated conversation whose patient sent three further messages, **When** the staff
   member opens it, **Then** they see the whole conversation — the patient's messages, the
   assistant's earlier replies, and their own if any — in one ordered thread.
5. **Given** a question the clinic's documents do not answer, **When** the turn runs, **Then** the
   FAQ path abstains and that abstention escalates the conversation — the patient is told the
   assistant cannot answer it from clinic documents and that staff now have it, and no speculative
   answer is generated alongside.
5a. **Given** the scheduling service is unreachable, **When** the patient asks to book, **Then**
    staff are called, the message is marked permanently as *assistant failed*, the conversation is
    emphasized — and the assistant is **not** silenced: the patient's next message is answered, and
    a retry once the service recovers succeeds normally.
6. **Given** a booking the scheduling service refuses, **When** the turn runs, **Then** the
   conversation is **not** escalated — the assistant explains the refusal and offers alternatives,
   exactly as it does today.
7. **Given** the scheduling capability is unreachable, **When** a patient asks for a human, **Then**
   the escalation still happens and staff can still reply.
8. **Given** an escalated conversation, **When** anyone asks the assistant a question in a
   *different* chat of the same session, **Then** that chat answers normally — escalation binds one
   conversation, not the session.

---

### User Story 2 - Know which conversations need a person (Priority: P2)

The staff side of the screen lists every conversation in the session and tells its user which ones
need them: the ones the assistant escalated, and the ones where a patient has spoken into a silent
conversation and nobody has answered. Those are emphasized and sorted to the top, a total stays
visible even while the user is working on the patient side, and the emphasis arrives on its own — a
conversation escalated from the patient pane a second ago is already emphasized on the staff pane.
Opening one, the staff member can see *which* message needs them and why: a mark sits on the message
itself, saying whether the patient asked for a person, the corpus could not answer, the assistant
failed, or the message simply went unanswered while the assistant was silent.

**Why this priority**: An escalation nobody notices is the same as no escalation, and under this
design an escalated conversation is *silent* — so a missed mark is a patient talking to nothing.
This is also what makes the side-by-side screen demonstrate anything at all, since the user raises
the escalation in one pane and must see it land in the other.

**Independent Test**: With the staff pane open, escalate from the patient pane and verify the
conversation is marked and moves to the top without a refresh. Send another patient message and
verify the message is marked unanswered and the conversation emphasized. Open it, read it, and
verify both are still there — then reply and verify both clear.

**Acceptance Scenarios**:

1. **Given** the staff pane is open, **When** a conversation is escalated from the patient pane,
   **Then** it is marked as escalated and sorted above the unescalated ones, without the page being
   reloaded or any control being pressed.
2. **Given** a silenced conversation the staff member has not answered, **When** the patient sends a
   message in it, **Then** that message is marked unanswered, the conversation is emphasized, and
   the total needing attention rises.
3. **Given** an emphasized conversation, **When** the staff member opens and reads it, **Then** it
   is **still** emphasized — only a staff reply clears it.
4. **Given** an emphasized conversation holding three unanswered messages, **When** the staff member
   replies once, **Then** the emphasis and all three unanswered marks clear together.
4a. **Given** a conversation whose corpus-could-not-answer mark is the only one left, **When** the
    staff member has replied, **Then** the conversation is no longer emphasized but that mark is
    still shown on its message — permanently.
5. **Given** three conversations escalated at different times, **When** the staff member looks at
   the list, **Then** the one escalated longest ago is first.
5a. **Given** conversations escalated for each of the three reasons, **When** the staff member opens
    one, **Then** the message that caused it carries a mark, and asking what the mark means says
    which reason it was.
6. **Given** the user is working in the patient pane, **When** a conversation needs attention,
   **Then** the total is visible to them without switching panes.
7. **Given** an escalated conversation, **When** the staff member posts a reply into it, **Then**
   the patient's pane shows that reply without the patient reloading or sending anything, and the
   conversation is no longer marked escalated.

---

### User Story 3 - Lead a conversation without the assistant talking over you (Priority: P3)

The staff member replies in a conversation — escalated or perfectly ordinary — and the assistant
goes quiet in it for two minutes so their next sentence is not interrupted by a generated one. Each
further message they send restarts those two minutes. They do not have to speak first to get that
silence: one switch takes the conversation before they have typed anything — to read it properly, or
to think — and the same switch hands it back the moment they are done. They can watch the time
counting down throughout. If they simply stop, the pause lifts by itself and the assistant carries
on, knowing what the staff member said.

**Why this priority**: Without it, every staff reply races a generated one into the same thread and
the patient sees two voices answering the same question differently. It ranks below Stories 1 and 2
because a handoff that nobody notices or cannot be answered fails harder than one where the
assistant is merely clumsy.

**Independent Test**: Reply as staff in an ordinary, unescalated conversation. Verify the countdown
appears, that a patient message sent within the two minutes gets no assistant reply and marks the
message unanswered, and that after the two minutes elapse the assistant answers again. Repeat,
turning the switch back on instead of waiting, and verify the assistant answers immediately. Then,
in a conversation nobody has touched, turn the switch **off** without writing anything and verify
the same two-minute silence starts.

**Acceptance Scenarios**:

1. **Given** an ordinary conversation nobody escalated, **When** the staff member posts into it,
   **Then** the assistant is paused there for two minutes and a countdown is shown.
2. **Given** a conversation paused with 30 seconds left, **When** the staff member posts again,
   **Then** the countdown restarts at two minutes.
2a. **Given** the assistant is midway through streaming a reply, **When** the staff member posts
    into that conversation, **Then** the generation stops and no part of that reply is kept or
    shown — the thread holds the patient's message and the staff reply, and nothing between them.
3. **Given** a paused conversation, **When** the patient sends a message, **Then** no assistant
   reply is generated, the message is kept in the thread and marked unanswered, and the
   conversation is emphasized.
4. **Given** a paused conversation, **When** the two minutes elapse with no staff action, **Then**
   the assistant answers the patient's next message normally.
5. **Given** a paused conversation, **When** the staff member turns the switch back on, **Then**
   the pause ends immediately and the assistant answers the next message.
5a. **Given** an ordinary conversation nobody has escalated and nobody has replied in, **When** the
    staff member turns the switch off without writing anything, **Then** the assistant is paused
    there for two minutes and the countdown is shown — identically to a pause a staff message
    started, and with no message added to the thread.
5b. **Given** a conversation paused by the switch with 20 seconds left, **When** the staff member
    turns it off again, **Then** the countdown restarts at two minutes.
5c. **Given** the assistant is midway through streaming a reply, **When** the staff member turns the
    switch off, **Then** the generation stops and no part of that reply is kept or shown — the same
    outcome as a staff message landing mid-stream (FR-017c).
5d. **Given** an emphasized conversation holding an unanswered message, **When** the staff member
    turns the switch off and later on again, **Then** the emphasis and the mark are untouched
    throughout — neither direction of the switch answers a patient.
6. **Given** an escalated conversation nobody has replied to, **When** the staff member turns the
   switch on, **Then** the escalation ends without a staff message and the assistant answers again.
6a. **Given** any conversation, **When** the staff member turns the switch off, **Then** a pause
    starts and **no** escalation is created — the switch can end an escalation but can never raise
    one, because an escalation records that the assistant asked for a person.
7. **Given** a conversation the assistant has resumed, **When** the patient asks about what the
   staff member told them, **Then** the assistant's answer reflects the staff messages, which are
   part of the conversation it reads.
8. **Given** a conversation paused a moment ago, **When** the page is reloaded or opened in a second
   tab, **Then** the pause is still in force with the correct time remaining.
9. **Given** a conversation a patient has just escalated, **When** the staff member opens it,
   **Then** the assistant switch is already shown off, without their having to work that out from
   the absence of replies.
10. **Given** a silent conversation holding two unanswered patient messages, **When** the assistant
    is allowed to speak again — by the switch or by the pause expiring — **Then** it answers neither
    of them, and answers only the next message the patient sends.
11. **Given** a silent conversation holding an unanswered patient message, **When** the staff member
    turns the assistant back on without replying, **Then** the assistant may speak again and the
    conversation stays emphasized and its unanswered mark stays.

---

### User Story 4 - Manage the practitioners the assistant books (Priority: P4)

The staff member adds a practitioner, edits one's name, specialty, appointment length, or weekly
schedule, and deletes one — from a screen, with the defaults and the cascading deletes the
scheduling service already enforces, and without ever touching a command line.

**Why this priority**: The capability already exists and is already tested; what is missing is a way
to *exercise* it. It is what turns "the assistant offers Tuesday at 10" into something a demonstrator
can change and re-ask in front of someone. It ranks below the escalation stories because nothing
about the handoff depends on it.

**Independent Test**: From the console, add a practitioner leaving every field defaulted, and verify
they appear when the assistant is asked which practitioners the clinic has. Edit their working hours
and verify the times the assistant offers change accordingly. Delete them and verify their
appointments go with them.

**Acceptance Scenarios**:

1. **Given** the practitioner screen, **When** the staff member adds a practitioner without typing a
   name, **Then** one is assigned from the seeded pool and shown back to them.
2. **Given** an existing practitioner, **When** their weekly schedule is edited, **Then** the times
   the assistant offers for them change to match on the next availability question.
3. **Given** a practitioner holding appointments, **When** they are deleted, **Then** they and their
   appointments are gone, and the assistant no longer offers them.
4. **Given** a practitioner name already used in this session, **When** the staff member tries to
   reuse it, **Then** the attempt is refused with a reason they can read, and nothing changes.
5. **Given** a practitioner belonging to another session, **When** it is addressed from this
   console, **Then** it resolves to nothing — indistinguishable from one that never existed.

---

### User Story 5 - Manage what the assistant can answer from (Priority: P5)

The staff member adds, edits, and deletes the clinic's FAQ entries from a screen, and can trust
what it shows without qualification: every entry listed is one the assistant can answer from, with
the text shown. "Saved" and "searchable" are the same fact here, because the write path is built so
they cannot come apart — the screen has no retrievability caveat to display and none to interpret.

**Why this priority**: It is the one admin action that changes what the assistant will say, and it
is what makes Phase 1e's threshold tuning possible by hand — editing the corpus and re-asking
questions, repeatedly, in the real screen. It ranks last because the assistant answers correctly
from a corpus loaded any other way; this only makes changing it convenient and its effect visible.

**Independent Test**: Add an entry, then ask the assistant a question it answers and verify the
answer cites it. Edit the entry and verify the cited text changes. Delete it and verify the
assistant abstains on the same question.

**Acceptance Scenarios**:

1. **Given** the FAQ screen, **When** an entry is saved successfully, **Then** it is listed, and the
   assistant can cite it on the next matching question.
2. **Given** any entry the screen lists, **When** the assistant answers from it, **Then** it answers
   from the text the screen shows — the entry the staff member sees and the entry the assistant
   searches are the same revision, and no listing can show otherwise.
3. **Given** a save that does not complete, **When** the staff member looks at the list, **Then** the
   entry is shown with its **previous** text, and reported as a failed save that can be retried —
   the assistant still answers from that same previous text, and the screen never reports as saved
   something that was not.
3a. **Given** the embedding service is unreachable, **When** the staff member edits an entry,
    **Then** the edit does not take effect anywhere, the entry keeps its previous text and the
    assistant keeps answering from it, and they are told which dependency was unavailable.
3b. **Given** the retrieval store fails midway through saving an edit, **When** the staff member
    looks at the entry, **Then** it holds its previous text and says the operation failed and can be
    retried — the assistant keeps answering from the old text throughout, and answers from the new
    text only once a save succeeds.
3c. **Given** an edit that failed at any step, **When** the staff member submits it again once the
    dependency recovers, **Then** it succeeds with no manual repair of the index, and submitting an
    edit that already succeeded changes nothing and creates no duplicate.
4. **Given** an existing entry, **When** it is deleted, **Then** the assistant stops answering from
   it, and where it was that question's only support the assistant abstains — and that abstention
   hands the conversation to staff (FR-003).
5. **Given** two sessions that have each added the same entry text, **When** one of them deletes
   its copy, **Then** the other session still answers from its own and cites it, unchanged.
6. **Given** a session that has just been created, **When** the staff member opens the FAQ screen,
   **Then** it shows an empty corpus plainly — not an error, and not another session's entries.
7. **Given** a session whose corpus is at the cap, **When** the staff member adds another entry,
   **Then** it is refused with a message saying the corpus is full, nothing is stored or indexed,
   and deleting an existing entry makes room for it immediately.

---

### Edge Cases

- **A message carrying two intents, one of which is an escalation** ("what should I bring, and can I
  speak to someone about my bill?"): the **whole turn is the handoff**. The classifier's
  `call_staff` label suppresses every other intent on that message — nothing is retrieved, generated,
  or booked — and the patient is told, in that turn, that a staff member has the conversation and
  will reply in it. A visitor who has asked for a person is going to get one, and the conversation
  falls silent from their *next* message: answering half of what they said and then going quiet
  without explanation is worse than handing over cleanly, and writing an appointment for a patient
  who has just asked to stop talking to a machine is the harder of the two to undo.
- **An escalation raised *during* a turn rather than by the classifier**: the turn finishes. When the
  model calls the capability mid-loop, or the FAQ path's abstention raises it, every specialist the
  turn selected completes and its reply is delivered, and the conversation is escalated at the end of
  that turn (FR-006). Silence starts with the *next* message, never mid-sentence — truncating an
  answer already being generated would leave the patient with half a reply and nothing explaining it.
  The distinction from the case above is that the classifier's label is known *before* any specialist
  starts, so nothing has to be cut off for it to take the turn.
- **The FAQ half abstains in a mixed-intent turn whose booking half succeeded**: both halves are
  delivered — the booking is confirmed and the unanswerable question is reported as handed to staff
  — and the conversation is escalated at the end of that turn. A successful booking does not
  suppress the handoff, and the handoff does not discard the booking.
- **An escalation raised in a conversation that is already escalated**: nothing transitions and the
  conversation is not marked twice. It is recorded as a request that changed nothing, exactly as 006
  records a change asking for a state the appointment already holds.
- **The patient keeps typing while escalated and nobody is looking**: every message is kept, shown
  in order, each marked unanswered, and the conversation stays emphasized. Nothing is dropped, nothing auto-replies, and
  nothing expires — an escalation has no deadline (FR-009).
- **A pause expiring while the patient is mid-message**: the message is sent into a conversation the
  assistant is free to answer by the time it arrives, so it is answered. There is no window in which
  a message is neither answered nor visible to staff.
- **Staff turn the assistant off and then say nothing at all**: the pause expires by itself after
  two minutes and the assistant resumes, exactly as it would after a staff message. There is no
  state left behind by a switch-off that nobody followed up, which is the point of it writing the
  same deadline rather than a mode of its own (FR-017b).
- **Staff turn the assistant off in a conversation that is already escalated**: nothing observable
  changes — it was already silent, and it stays silent with no deadline, because an escalation does
  not expire (FR-009). The pause is written all the same, so a later switch-**on** clears both in one
  act and no ordering rule is needed for the pair.
- **A staff reply landing in the same instant the pause expires**: the reply starts a fresh
  two-minute pause regardless. The deadline is recomputed from the newest staff message, so a
  message that arrives at the boundary extends the silence rather than falling through it.
- **A patient message arriving between the staff member's two sentences**: it lands in the thread
  unanswered and emphasizes the conversation, which is exactly what the pause is for — the staff
  member sees it and answers it themselves.
- **Staff posts while the assistant is mid-sentence**: the generation is cancelled and the partial
  reply is thrown away, so the patient is never left with half an assistant answer sitting next to a
  staff message that contradicts it (FR-013a).
- **Staff posts in the instant between a turn finishing and its reply being written**: there is no
  generation left to cancel, and the reply is discarded all the same. FR-013a is about the reply,
  not about how far it got — a complete one written behind a staff member's own is the reply it says
  must not be left standing beside theirs, and the patient sees the turn end without an answer.
- **The staff member posts at the same moment the patient does**: both messages land, ordered by
  arrival, and neither replaces the other. A thread is an append-only log.
- **The conversation is deleted while escalated or paused**: it goes with its marks, its messages,
  its patient, and that patient's appointments — the existing delete, unchanged.
- **Scheduling is down and the patient asks three booking questions in a row**: each calls staff and
  each marks its own message permanently, but the conversation is never silenced, so an FAQ question
  in between is answered as usual. One staff reply removes the emphasis; the three permanent marks
  stay (FR-003d, FR-027c).
- **Escalation with the scheduling service down**: unaffected. Escalation is entirely a
  core-backend concern; a chat with no patient record can still be escalated and answered by staff.
- **A staff reply to a conversation the assistant has just resumed**: accepted, like any other staff
  message, and it pauses the assistant again. There is no conversation in the session a staff member
  is forbidden to speak in (FR-024), so this needs no refusal at all.
- **Three patient messages arrive during a pause, then the pause expires and the patient sends a
  fourth**: the assistant answers the fourth alone. The first three stay in the thread as context it
  can read, but they are not the question it is answering, and they are not merged into that turn
  (FR-019a, FR-019b).
- **Two browser tabs on the same session**: both are the same session and the same staff member. An
  escalation raised in one appears in the other; a pause started in one counts down in both from the
  same stored deadline; and a mark is a property of the conversation, so it cannot be set in one tab
  and absent in the other (FR-029c).
- **A visitor is mid-conversation when their session is deleted**: their next request finds no
  session. They are treated exactly as a first arrival, since a cookie naming a session that no
  longer exists is already handled that way today — nothing new is invented for it.
- **A deletion that removes one store's records but not the other's**: reported as incomplete, never
  as success, and safe to re-run until it converges (FR-051).
- **A visitor returning on a session minted before this feature**: there is no such session — the
  deployment removed it (FR-039e). Their cookie names a session that no longer exists, which the
  system already treats as a first arrival, so they get a new session, a new chat, and an empty
  corpus. Their old chats, patients, practitioners and appointments are gone with it, deliberately.
  Nothing new is invented for this case, and no code tolerates a half-migrated one.
- **A brand-new session, before any FAQ entry has been added**: its corpus is empty, so the first
  FAQ question abstains, escalates, and silences the assistant in that conversation until staff act
  — deliberately, with no empty-corpus exemption (FR-003c). Other conversations in the session are
  unaffected (FR-011), and booking still works, so the session is not stuck. Nothing about session
  creation touches the retrieval store, so a store unreachable at that moment changes nothing here
  either (FR-039b, FR-039d).
- **An update whose chunk write succeeds but whose publishing commit then fails**: the new revision
  sits in the index unpublished and unreachable, the entry keeps its previous content and stays
  retrievable on it, and the staff member is told the save failed. A retry publishes a further
  revision, and its sweep clears the failed attempt along with the superseded one — both are simply
  not the live revision of that entry (FR-042e, FR-042g, FR-042h).
- **A delete whose row removal succeeds but whose chunk sweep fails**: the entry is unanswerable the
  moment its row is gone, because nothing names its revisions live any more. The leftover chunks are
  unreachable, and the session's deletion removes them. At no point is a deleted entry still citable
  (FR-042f).
- **The stored rows are unreachable when a patient asks an FAQ question**: the turn fails as a
  dependency failure and says so. It does not abstain, because abstaining would tell the patient the
  corpus has no answer for them — something nothing checked. A session with a genuinely empty corpus
  is the one that abstains, and the two are never reported the same way (FR-042j).
- **A deployment with no admin secret configured**: every deletion request is refused and
  nothing is removed. The absence of a secret means there is no admin, not that anyone may act as
  one (FR-048a).
- **A session whose corpus is full**: the next create is refused and says so, and nothing about the
  session is otherwise degraded — the assistant answers from the 200 entries as usual, and editing
  or deleting any of them works normally, including deleting one to make room (FR-039f).
- **A session that accumulates everything else without limit**: nothing refuses it. Practitioners,
  chats, and their messages grow for as long as the visitor keeps adding, and only an admin
  deleting the session (FR-046) reclaims any of it. This is deliberate — only the corpus is capped,
  and only because retrieval carries its size on every turn — and the growth degrades that session
  alone, never another (FR-032).
- **Content that would index to nothing**: unreachable. The content validator already rejects text
  with nothing meaningful in it, and the chunk filter applies that same check to slices of what
  survived, so a character that makes the whole content meaningful survives in whichever chunk holds
  it. No save can publish a revision with no chunks, which is why a listed entry is always
  answerable (FR-040).
- **One entry saved twice at once** — two browser tabs, or a resubmitted request, since a session
  has only one staff member (FR-031): each save writes its own revision, so their chunks never
  interleave in the index. Whichever commits second finds the revision it expected to replace is no
  longer live, and that save is reported as failed rather than publishing over the other. What the
  entry is *not* protected from is the ordinary lost update — the staff member saving stale text
  from a tab opened earlier, and it simply taking effect — which is out of scope by choice
  (FR-042c).
- **An FAQ entry edited while a patient's turn is retrieving against it**: the turn completes against
  the revision that was live when it searched. No turn is failed or restarted because the corpus
  changed underneath it, and no turn ever sees half of one revision and half of another.
- **The staff member has never opened the console when the first escalation arrives**: the marks are
  waiting for them when they do. They are properties of the conversation, not of whether a pane
  happened to be rendered.
- **A conversation that is escalated *and* holds unanswered messages**: it counts once toward the
  total needing attention, not once per mark. Emphasis is a property of the conversation and the
  marks are properties of its messages; a conversation is one item in the list however many marks
  sit inside it, and one staff reply clears the emphasis and every clearable mark at once.

## Requirements *(mandatory)*

### Functional Requirements

#### Escalating a conversation

- **FR-001**: The assistant MUST have an `escalate_to_staff` capability, exposed through Phase 1c's
  existing tool registry alongside the scheduling tools, so that handing off is a tool call the
  agent makes rather than a branch hard-coded into orchestration.
- **FR-001a**: Escalation MUST be **one capability with one implementation**, reachable by more than
  one caller: the model invokes it as a registry tool when the patient asks for a person, and the
  abstention gate and the failure path invoke the same handler directly, since neither runs inside a
  model turn in which a tool could be chosen. Every caller MUST produce the same state, the same
  record, and the same reason handling — the seam exists so agent reasoning stays decoupled from
  what a handoff does, not so that each trigger can implement its own.
- **FR-002**: The capability MUST be available in every conversation regardless of whether the
  scheduling service is reachable, and regardless of whether the chat has a patient record yet.
  Escalation is a core-backend concern only.
- **FR-003**: The assistant MUST call staff on exactly three triggers, and no others. Two of them
  **silence** the assistant in that conversation (FR-009); the third does not (FR-003d):
  1. **The patient explicitly asks for a person** — to speak to staff, a human, or the clinic
     itself.
  2. **The FAQ path abstains** — retrieval is judged insufficient to answer from, so the turn
     abstains rather than generating. The escalation is raised on the same signal that produces the
     abstention today, before any generation call is made.
  3. **The assistant fails** — it cannot complete what the patient asked because something broke:
     the scheduling service is unreachable, a write's outcome is unknown, or a tool call errored.
     The patient asked for something and the system cannot say what happened to it, which is a
     person's problem, not a sentence the assistant should compose its way out of. This trigger
     raises attention without silencing (FR-003d).

  Triggers 1 and 2 silence because the patient is owed a *person* — fobbing them off with more
  assistant is the failure being avoided. Trigger 3 is owed a *retry*: the thing that broke may
  already be working again, and silencing the conversation would take away the fastest route to
  what the patient actually wanted.
- **FR-003a**: A **refusal MUST NOT escalate**, and this is the line between trigger 3 and ordinary
  operation. A refusal is an *answer*: the slot is taken, the time is outside the practitioner's
  hours, the appointment has already started — each names one reason and 006 already requires an
  alternative to be offered with it. A failure is the absence of an answer. The test is whether the
  system can tell the patient what is so: if it can, that is a refusal and the assistant says it; if
  it cannot, that is a failure and a person is fetched.
- **FR-003c**: An abstention MUST escalate regardless of how much the corpus holds, **including
  when it holds nothing**. There is no empty-corpus exemption: an abstention against an empty corpus
  and an abstention against a corpus that simply does not cover the question are the same outcome to
  the patient, and separating them by a row count would give one value two meanings. The accepted
  consequence is that a session's first FAQ question, asked before any entry has been added, hands
  that conversation to staff and silences the assistant in it (FR-009).
- **FR-003b**: When the FAQ path abstains, the assistant MUST NOT both abstain and separately
  attempt a speculative answer to the same question. The abstention and the handoff are one
  outcome: the assistant says it cannot answer that from the clinic's documents and that it has
  handed the question to staff.
- **FR-003d**: A call to staff raised because the assistant **failed** MUST NOT silence the
  assistant. It marks the message permanently (FR-027a, FR-027c) and emphasizes the conversation
  (FR-029), and the assistant goes on answering — the patient may retry immediately, and an
  unrelated question in the same conversation is still answered. A transient outage MUST NOT cost a
  conversation its assistant until a human intervenes.
- **FR-004**: Escalation MUST NOT require the patient's confirmation. Unlike a change to an
  appointment it alters no record the patient holds, and it is reversible — a staff reply or the
  switch of FR-017 ends it (FR-009a).
- **FR-005**: In the same turn it escalates, the assistant MUST tell the patient that a staff member
  has been notified and will reply in this conversation. It MUST NOT promise a response time,
  because nothing in the system commits to one.
- **FR-006**: A turn that escalates MUST run to completion first: every specialist the turn selected
  finishes and its reply is delivered, and the conversation's escalated state takes effect at the
  end of that turn (see Edge Cases).
- **FR-007**: Escalating a conversation that is already escalated MUST transition nothing, add no
  second mark, and be recorded as a request that changed nothing — distinct from an
  escalation that actually transitioned a conversation (FR-033). The reason carried by the original
  escalation MUST NOT be overwritten by the second request: the conversation is still marked for the
  reason that first silenced it.
- **FR-007a**: Every escalation MUST carry exactly one **reason**, drawn from the same closed set as
  FR-003's triggers because they are the same things: **patient asked for a person**, **corpus could
  not answer**, or **assistant failed**. No fourth value exists, and no escalation is raised without
  one.

#### What the escalated mark means

- **FR-008**: Escalation MUST be a state of the **conversation as a whole**, never a property of a
  message. It means the assistant asked for a person here and no person has dealt with it yet, and
  it does two things at once: it marks the conversation for attention, and it stops the assistant
  replying in it.
- **FR-009**: A conversation is **escalated** — silenced — when staff were called for one of the two
  reasons that silence: the patient asked for a person, or the corpus could not answer (FR-003).
  While a conversation is escalated the system MUST generate no reply in it — no intent
  classification, no retrieval, no tool call, and no generation call of any kind — and MUST stay
  that way **indefinitely**, until a person deals with it. Unlike the pause (FR-013), an escalation
  has no deadline: nothing about time passing means a patient who asked for a human got one.
- **FR-009a**: The escalated state MUST be ended by either of exactly two things, and nothing else:
  the **first staff message** posted into that conversation, or the staff member explicitly
  **returning it to the assistant** with the control of FR-017. Replying *is* taking the
  conversation, so there is no separate resolve or close action; the explicit return exists only so
  that an escalation raised by mistake — or one the staff member judges the assistant can handle
  after all — is not a conversation silenced forever.
- **FR-010**: A conversation whose escalation ended MUST be escalatable again later, as a fresh
  escalation with its own waiting time.
- **FR-011**: The escalated mark MUST bind exactly one conversation. Every other chat in the session
  is unaffected by it.
- **FR-012**: The escalated mark MUST survive a reload, a second browser tab, and a restart of the
  backend — it is a property of the stored conversation, not of an open connection.

#### Pausing the assistant

- **FR-013**: A staff message MUST pause the assistant in that conversation for **2 minutes**,
  whether or not the conversation was escalated. The trigger is a person **taking** the
  conversation, not a mode being entered: the assistant must not talk over a conversation a human
  has started leading. Speaking is the usual way of taking it and needs no separate act; saying so
  explicitly with the switch is the other, and starts the identical pause (FR-017b).
- **FR-013a**: A staff message MUST cancel any assistant reply already being generated in that
  conversation, and the partial reply MUST be discarded entirely — not persisted, not shown, and not
  left standing beside the staff message. The patient sees the turn end without an answer, exactly
  as they already do when a new message supersedes a running one.
- **FR-014**: Each further staff message MUST restart the 2 minutes, so a staff member typing a
  sequence of messages never has the assistant cut in between them. Turning the switch off again
  MUST restart them on the same terms — a staff member who needs longer than two minutes has one
  gesture that gives it to them, repeatable as often as they like.
- **FR-015**: While the assistant is paused in a conversation, the system MUST generate no reply in
  it: no intent classification, no retrieval, no tool call, and no generation call of any kind.
- **FR-016**: The pause MUST end **by itself** when the 2 minutes elapse, with no staff action, and
  the assistant then answers the patient's next message normally. This is the one difference that
  matters between a pause and an escalation: a pause expires because a staff member who has stopped
  typing has finished, whereas an escalation does not expire, because time passing is not a person
  answering (FR-009).
- **FR-017**: The console MUST show, for every conversation, whether the assistant is currently
  allowed to speak in it — as a switch whose position **always** states the answer, not as a control
  that only appears while something is wrong. A staff member opening a conversation a patient just
  escalated sees the switch already off, alongside the escalated mark, and does not have to infer
  the silence from the absence of replies.
- **FR-017a**: The switch MUST read a single derived fact — *may the assistant reply here* — false
  while the conversation is escalated or paused and true otherwise. It is computed from those two
  states rather than stored beside them, so it can never disagree with the state that actually
  decides whether a reply is generated.
- **FR-017b**: The switch MUST work in **both** directions, at any moment, in any state.
  **Turning it on** ends whichever silence was in force (FR-009a) — an escalation, a pause, or
  both — including while a patient message sits unanswered.
  **Turning it off** starts a 2-minute pause, exactly the pause a staff message starts (FR-013), and
  restarts it if one was already running (FR-014). It MUST introduce no state of its own: "off" is
  the existing deadline, so there is nothing a reader has to reconcile between a pause a person
  asked for and a pause a message caused.
  Where a pause is running the switch MUST show how much of the 2 minutes remains; where the
  conversation is escalated there is no deadline to show.
  **Neither direction MUST remove the conversation's emphasis or clear any mark** — in neither case
  has anyone answered the patient (FR-029a). This is the property that makes the switch safe to use
  freely: it decides only who may speak, never whether anyone still needs a person.
- **FR-017c**: Turning the assistant **off** MUST cancel any reply already being generated in that
  conversation and discard it entirely, on exactly the terms FR-013a sets for a staff message. A
  staff member who has just said the assistant must not speak here MUST NOT then watch it finish a
  sentence. The mechanism is the same one, not a second one.
- **FR-018**: The pause MUST be a deadline stored on the conversation, not a timer living in an open
  page: it MUST survive a reload, a second tab, and a backend restart, and MUST bind exactly one
  conversation.
- **FR-019**: A patient message sent while the assistant is silent — paused or escalated — MUST
  still be accepted, kept, and shown in the thread in arrival order, and MUST be marked *unanswered* and
  emphasize the conversation (FR-029, FR-027a). The turn MUST complete without a reply rather than failing, and the patient MUST
  be shown nothing further: their message lands in the thread and stays there. No banner,
  acknowledgement line, or status indicator is added, because the messages already around it — the
  assistant saying staff have this, or the staff member's own reply — are what explain the silence.
- **FR-019a**: The assistant MUST NOT answer, retroactively, any message that arrived while it was
  silent. When it is allowed to speak again — by the switch or by a pause expiring — it answers only
  what the patient sends **after** that, and the messages from the silent window remain part of the
  conversation it reads for context but are never treated as the question it is answering.
- **FR-019b**: FR-019a MUST override the existing rule that merges consecutive unanswered patient
  messages into one turn. That rule exists because a patient typing three quick lines means one
  question; a message left unanswered because a person was handling the conversation does not, and
  merging it into a later turn would have the assistant speak over the person it is still waiting
  for. Messages from a silent window MUST be excluded from the burst a later turn answers. They are
  not thereby closed: their marks stay and the conversation stays emphasized until a staff member
  replies (FR-027c), so what the assistant is told about them MUST NOT assert that anyone has
  answered them - a silence that merely expired was answered by nobody.

#### Staff in the conversation

- **FR-020**: A conversation MUST be able to hold messages from three senders — the patient, the
  assistant, and staff — in one flat, ordered log. The existing two-sender shape is widened, not
  supplemented by a second channel.
- **FR-021**: A staff message MUST appear in the patient's own thread, in order, labelled as coming
  from **staff** and distinguishable at a glance from an assistant reply. It MUST carry **no
  person's name** and no identifier of any kind — the label states a role, which is the whole of
  what is true: one anonymous session is on both ends of this conversation, and a human-sounding
  name would invite the patient to believe a specific person is answering them.
- **FR-022**: There MUST be no staff record, no staff identifier, and no staff attribute of any
  kind. A session has exactly one staff member in the sense that matters — one person acts as staff
  in it — and that fact needs nothing stored to be true: a staff message is identified by its
  sender, and its chat's session already says which session it belongs to. "Exactly one" is
  therefore true by construction rather than by a constraint something could violate.
- **FR-022a**: *(Withdrawn.)* This required the staff member's display name to be derived from the
  session identifier. There is no display name; see FR-023. The property it existed to protect
  holds more simply than it did: because nothing about staff is stored or derived, there is nothing
  a session could need migrated or back-filled, and nothing that can resolve to absent.
- **FR-023**: Messages MUST be labelled by **role**, using exactly two labels: a staff message
  reads **"Staff"** and an assistant message reads **"AI assistant"**. The patient's own messages
  stay unlabelled, as they are today — they are the reader's own, and a label would say nothing
  they do not already know. This is the first time the assistant's replies are labelled at all;
  until now the two senders were distinguished only by position and styling, which was sufficient
  while there were two of them and is not sufficient with three.
- **FR-024**: Staff MUST be able to read and post into **every** conversation belonging to their own
  session, escalated or not. Escalation decides what is *marked*, never what is reachable — a staff
  member who wants to say something in an ordinary conversation should not have to escalate it
  first to be allowed to.
- **FR-025**: The staff view of a conversation MUST show the entire thread — patient, assistant, and
  staff messages alike — not only the messages since it was escalated.
- **FR-026**: Staff messages MUST be part of the conversation the assistant reads on subsequent
  turns, attributed to staff, so that when the pause ends the assistant does not contradict what a
  person just said in the same thread.

#### What the console shows, and what it marks

- **FR-027**: The console MUST list **all** of the session's conversations, with emphasized ones
  (FR-029) shown first. Among those, the one waiting longest comes first.
- **FR-028**: The staff side MUST show a total of the conversations needing attention, and that
  total MUST stay visible while the user is working in the patient pane.
- **FR-029**: A conversation MUST be **emphasized** in the list while it needs a person — that is,
  while it is escalated, or while it holds a patient message that went unanswered because the
  assistant was silent. Every reason MUST be emphasized **identically** in this phase: the list says
  *this needs you*, and what it needs is read from the marks inside it (FR-027a).
- **FR-029a**: A staff message in a conversation MUST remove its emphasis. Nothing else does — not
  opening it, not reading it, not a pause expiring, and **neither direction of the switch**
  (FR-017b). Emphasis means "a person is needed here", and only a person speaking answers that:
  taking a conversation is not answering it, and handing it back is not answering it either.
- **FR-029b**: Emphasis and marks MUST be properties of the stored conversation and its messages
  rather than of a rendered pane, so a staff member arriving for the first time sees everything that
  accumulated before they looked, and two open tabs agree rather than disagreeing or double-counting.
- **FR-029c**: A conversation newly emphasized while the staff side is open MUST show that without a
  manual reload or any user action; a staff message posted while the patient side is open MUST
  appear in the patient's thread on the same terms.

#### Marks on messages, and how long they last

- **FR-027a**: The console MUST mark the individual **message** that caused a conversation to need a
  person, so a staff member can see *which* message is outstanding rather than only that the
  conversation is. The mark MUST reveal, on request, which of four kinds it is:
  1. **patient asked for a person** — the patient explicitly requested staff;
  2. **corpus could not answer** — the FAQ path abstained on this question;
  3. **assistant failed** — the assistant could not complete what this message asked;
  4. **unanswered** — this message arrived while the assistant was silent and nothing answered it.
- **FR-027b**: The first three kinds MUST correspond exactly to the three reasons staff can be
  called for (FR-007a), and MUST be set by the same act that calls them — so there is no call
  without a mark on the message that caused it, and no mark of these three kinds without a call
  behind it. The fourth kind is not a call at all: it is set when a patient message arrives while
  the assistant is already silent, and it records a consequence of silence rather than a request for
  a person.
- **FR-027c**: Two independent properties MUST be decided by a mark's kind, and by nothing else —
  whether it silenced the assistant, and whether it ever clears. They do **not** line up, and the
  spec states the grid rather than leaving it to be inferred:

  | Mark kind | Silences the assistant? | Lifetime |
  |---|---|---|
  | patient asked for a person | yes | cleared by a staff message |
  | corpus could not answer | yes | permanent |
  | assistant failed | no (FR-003d) | permanent |
  | unanswered | no — it is a *consequence* of silence | cleared by a staff message |

  A staff message in a conversation MUST clear **every** outstanding clearable mark in it at once,
  however many accumulated: a person spoke, and that is what those marks were asking for. A
  permanent mark MUST never be cleared, by a staff message or anything else — a staff member
  answering the patient does not mean the corpus gained the entry it was missing, or that the
  failure did not happen.
- **FR-027d**: Neither axis of FR-027c's grid MUST be collapsed into the other. Silencing answers
  "may the assistant speak here"; lifetime answers "has this been dealt with"; and the two disagree
  on two of the four kinds, which is precisely why one flag cannot carry both. Collapsing them would
  either erase a diagnostic record or leave an answered request outstanding forever.
- **FR-027e**: A conversation whose only remaining marks are permanent MUST NOT be emphasized. The
  permanent marks are a record, not a queue: they stay visible on their messages without asking
  anyone to act again.

#### The one screen

- **FR-030**: The application MUST present both sides at once — the session's patient chats and the
  staff console — so an escalation can be raised on one side and watched arriving on the other.
- **FR-031**: There MUST be no login and no second kind of user. The anonymous session remains the
  only identity and owns both sides.
- **FR-032**: Every capability the console exposes MUST be scoped to the caller's own session, as a
  predicate on the read rather than a check applied afterwards, so a chat, practitioner, staff
  member, or FAQ entry belonging to another session is indistinguishable from one that never
  existed. There is no exception.

#### Recording what happened

- **FR-033**: Every escalation and every return MUST be recorded through the existing structured
  logging conventions, carrying the conversation it applies to, which direction it went, its reason
  where it has one (FR-007a), and the turn correlation identifier that caused it. An escalation that transitioned nothing (FR-007) is
  recorded as its own kind, so one escalation record means one handoff.
- **FR-034**: Recording MUST be best-effort and MUST NOT gate a transition — a log entry that fails
  to be written cannot un-happen a handoff that already occurred. This follows 006's rule for change
  records unchanged.

#### Managing practitioners from the console

- **FR-035**: The console MUST let the staff member list, add, edit, and delete the session's
  practitioners, with the seeded-name defaults, the name-uniqueness rule, the overlapping-range
  rule, and the cascading deletes the scheduling service already enforces. No rule is re-implemented
  on the screen; every refusal comes from the service that owns it and is shown to the user in
  plain language.
- **FR-036**: The console MUST reach these capabilities without the browser holding or presenting
  the session credential. The session identity stays unreadable to code running in the page, exactly
  as it is today — which has a consequence for how this traffic has to be carried, recorded in
  Dependencies.
- **FR-037**: A change made on the practitioner screen MUST be reflected in what the assistant says
  next — the roster it lists, the specialties it names, and the times it offers.

#### Managing FAQ entries from the console

- **FR-038**: The console MUST let the staff member list, add, edit, and delete FAQ entries through
  the existing FAQ capabilities. Their current Postgres↔Qdrant ordering is **not** preserved as it
  stands: FR-042b to FR-042i replace it, because that ordering is destructive — it removes an
  entry's chunks before writing their replacements — and a destructive save is what makes a failed
  save cost a working entry.
- **FR-039**: FAQ entries MUST be **session-scoped**. Each session holds its own corpus, which it
  builds from nothing through the console, and a session's additions, edits, and deletions MUST be
  invisible to every other session. This is a change to what an FAQ entry is, not only a
  screen over it: until now the corpus was shared by every visitor, and a console with a delete
  button on a shared corpus and no login (FR-031) would let any visitor empty what every other
  session answers from.
- **FR-039a**: Retrieval MUST carry the session predicate as a filter on the search itself, not as a
  check applied to the results afterwards. Another session's entry MUST NOT be retrievable, citable,
  or countable toward whether an answer is sufficiently grounded.
- **FR-039b**: A new session's corpus MUST start **empty**. Nothing is seeded, copied, or embedded
  at session creation, and session provisioning MUST NOT gain a corpus step. A starting template is
  deliberately deferred to later work and is not part of this feature.
- **FR-039c**: A session's FAQ entries and their retrievable chunks MUST be removed when the session
  is deleted (FR-046): its rows first, which un-publishes every revision they named, then its chunks
  (FR-042f). No session's chunks may remain reachable once its rows are gone, and none may remain
  stored once the deletion completes — the session delete is also the backstop sweep for anything an
  earlier sweep left behind (FR-042h).
- **FR-039d**: The console MUST show an empty corpus as **empty**, plainly, rather than as a list
  that failed to load or another session's entries. An empty corpus is the ordinary starting state
  of every session, not an error condition, and the screen is where a corpus comes into existence.
- **FR-039e**: This feature MUST be deployed onto an **empty system**. Every session that exists
  before it ships MUST be removed as a one-time reset, together with everything those sessions own
  in every store: the chat store's chats and messages, every FAQ entry and its indexed chunks, and
  the scheduling store's patients, practitioners and appointments. Nothing MUST be migrated, nothing
  MUST be given an owner, and **no code path MUST be written to tolerate a row that predates
  ownership**.
  This is what lets an entry's owning session and its live revision be **required** rather than
  optional: "an entry that belongs to nobody" is then not a state the retrieval filter has to
  exclude but a state that cannot be written at all.
  The reset MUST be treated as destructive and irreversible, and is acceptable only on the
  precondition FR-045a already states — the system carries synthetic data with no real clinical
  content. A returning visitor whose cookie names a session the reset removed MUST be treated as a
  first arrival, which is how an unrecognized cookie is already handled and needs nothing new.
- **FR-039f**: A session's corpus MUST be capped at **200 entries**, enforced when an entry is
  created. A create beyond the cap MUST be refused, MUST change nothing, and MUST say plainly that
  the corpus is full and an entry has to be removed first — never fail silently or partially. The
  cap MUST be a single configured value rather than a number repeated across the code. Editing and
  deleting MUST NOT be affected — a full corpus is still fully manageable. The cap exists because
  retrieval carries the session's live revisions as a filter term on **every** FAQ turn (FR-042d),
  so corpus size sits on the hot path of the feature's core behavior; bounded console listing and
  bounded per-session corpus storage follow as side-effects. It MUST NOT be read as a general
  anti-abuse rule: nothing else a session accumulates is capped (FR-039g).
- **FR-039g**: Nothing else a session owns MUST be capped or rate-limited by this feature —
  practitioners, chats, and their messages included — and admin deletion (FR-046) MUST remain
  their only bound. This is a decision, not an omission. Nobody logs in (FR-031), so the argument
  for bounding them reads exactly as it does for the corpus; it is declined because no design here
  depends on their size, and unbounded growth degrades only the session that caused it, never
  another (FR-032).
- **FR-040**: **Every FAQ entry that exists MUST be retrievable**, with the text it shows. A stored
  row names a live revision, a published revision always holds at least one chunk, and retrieval
  searches live revisions — so "listed" and "searchable" MUST be the same fact. The console MUST NOT
  carry a per-entry retrievability indicator: there is no second state for it to report, and a
  signal that can never fire teaches the staff member to rely on one that would not warn them.
- **FR-041**: It follows that no entry MUST ever be presented with text the assistant would not
  answer from. The entry the screen shows and the entry the assistant searches MUST be the same
  revision, and the write path MUST make any other outcome unrepresentable rather than detect and
  report it. Loss of indexed data occurring **outside** this write path is out of scope: nothing
  here detects it, and the console MUST NOT be built to.
- **FR-042**: A failed save MUST be reported as a failed save. The screen MUST NOT show an entry as
  stored-and-searchable on the strength of the request having been sent.
- **FR-042a**: Chunking and embedding for the new content MUST happen **before any store is
  written**. If either fails, both stores MUST be left exactly as they were and the entry reported
  unchanged, naming the dependency that was unreachable — so the likeliest failure in the path never
  reaches a state that needs recovering from.
- **FR-042b**: An entry's indexed chunks MUST be **immutable and identified by a revision**. A save
  MUST write its chunks as a **new** revision and MUST NOT delete, overwrite, or modify the chunks
  of any existing one. Every indexed chunk MUST carry both the revision it belongs to and the
  **entry it belongs to**, so an entry's revisions can be addressed without consulting the index's
  contents. The entry's stored row MUST name the single revision that is **live**, and MUST NOT
  keep a history of the others; every other revision of that entry is **superseded**, which is to
  say simply *not the live one*.
- **FR-042c**: Every create and update MUST follow one sequence: chunk and embed (FR-042a), write
  the new revision's chunks to the index, then **one** local commit that stores the new content and
  names that revision live. That commit MUST be the only point at which the change becomes visible
  to retrieval or to the screen, and it MUST carry a staleness guard on the revision it replaces
  (006's rule for a change). The guarded revision MUST be the one read when the operation began, not
  one supplied by the caller: the guard's job is to protect the index across the window between the
  chunk write and the commit, and a revision read inside the operation covers that window exactly.
  Two operations racing on one entry therefore write disjoint revisions, one commit wins, and the
  other is reported as failed rather than silently publishing over it. Preventing a **lost update**
  between two views a person has open is a different problem and is deliberately out of scope: a
  session has exactly one staff member (FR-031), so a later save simply supersedes an earlier one.
- **FR-042d**: Retrieval MUST search **only live revisions**, and MUST do so by the same filter that
  scopes it to the session (FR-039a) rather than by a check applied to the results afterwards. The
  chunks of a superseded or never-published revision MUST NOT be retrievable, citable, or countable
  toward whether an answer is sufficiently grounded.
- **FR-042e**: A failed operation MUST report that it failed and can be retried, and MUST leave the
  entry **exactly as it was** — its previous content stored, its previous revision live, and the
  assistant still answering from it. No rollback, revert, or compensating write MUST be performed,
  because none is needed: content is written once, in the commit that publishes it, so a failure
  before that commit has changed nothing anyone can observe, and a failure of that commit is the
  change not happening. A best-effort repair performs writes of its own that can half-succeed and
  swallows its own failures, which is what produced the silent disagreement this design removes.
- **FR-042f**: A delete MUST remove the stored row **first**, which un-publishes every revision that
  row named and makes the entry unanswerable at that instant. Removing its chunks follows as
  housekeeping (FR-042h), and a failure to remove them MUST NOT be reported as a failed delete.
  This reverses the existing deindex-before-delete ordering, and is safe only because
  unretrievability now comes from the row rather than from the index being empty.
- **FR-042g**: Retrying any operation MUST be safe, MUST be repeatable any number of times, and MUST
  require no manual repair of the index — a retry is an ordinary save that publishes a further
  revision. Retrying an operation that already succeeded MUST change nothing and MUST NOT produce a
  duplicate entry.
- **FR-042h**: Chunks that are not live MUST be swept from the index, and the sweep MUST NOT be
  load-bearing. It is scoped to **one entry**, removing that entry's chunks whose revision is not
  the live one — a single predicate that covers a revision superseded by a later commit and a
  revision that was written but never published alike, so a failed save's chunks are cleared by the
  retry that follows it. It MUST NOT be widened to sweep a whole session by the same predicate: a
  concurrent save's chunks are not live between their write and the commit that publishes them
  (FR-042c), and a session-wide predicate would delete them, publishing a revision whose chunks no
  longer exist. The sweep runs after the commit; it MUST be idempotent; and its failure MUST NOT
  fail the operation, MUST NOT be reported as one, and MUST NOT change what is retrievable — chunks
  that are not live are already unreachable. Nor MUST it be **logged**: a failed sweep raises no
  event of any kind, and in particular MUST NOT raise the critical dependency event the rest of this
  path raises when the retrieval store is unreachable, which means an operation could not be
  completed. A sweep is not an operation. The entry's next successful save and its session's
  deletion (FR-039c) MUST each sweep whatever earlier sweeps left behind, so leftovers converge to
  none without anyone intervening.
- **FR-042i**: The trade this makes MUST be taken explicitly: the failure mode of this path is
  **leaked storage, never a lost answer**. Unreachable chunks occupying space until a later sweep is
  an accepted outcome; no failure in this path MUST take a working entry out of service or require a
  person to repair it before the assistant can answer from it again.
- **FR-042j**: Retrieval MUST distinguish a session whose corpus is **empty** from one whose live
  revisions could not be **read**. An empty set MUST abstain and escalate as an empty corpus does
  (FR-039b); a failed read MUST be reported as the unreachable dependency it is, and MUST NOT be
  presented to the patient as an abstention, cited as evidence the corpus lacks an answer, or
  counted as a groundedness decision — nothing verified the corpus, so nothing may be claimed about
  it. This is the same rule the rest of this feature applies to an outcome it does not know.

#### Deleting sessions (admin maintenance)

- **FR-046**: The system MUST provide two admin capabilities: delete **one named session**, and
  delete **all sessions**. Without them nothing in the system ever removes a session, so FR-039c has
  no trigger and every visitor's data — corpus included — accumulates permanently.
- **FR-047**: Deleting a session MUST remove everything that session owns, in both services: its
  chats, their messages and marks, and its FAQ entries with their retrievable chunks; and in the
  scheduling service, its patients, its practitioners, and their appointments. There is no staff
  member to remove: nothing is stored for it and nothing is derived for it (FR-022).
  The scheduling service can today delete one patient for a chat and one practitioner, but has no
  session-level delete, so this requires a capability it does not yet have.
- **FR-048**: Both capabilities MUST be refused unless the request carries a single **admin
  secret**, read from environment configuration. A request without it, or with the wrong one, MUST
  change nothing and MUST report only that it was refused — never which part was wrong.
- **FR-048a**: The capabilities MUST be HTTP routes on the core backend, carrying the secret on the
  request. Four properties follow and MUST hold, because this puts "delete every session" on the
  same public surface as the chat: the secret MUST be carried in a **request header**, never a query
  string or path segment, which would reach access logs and browser history that FR-050's redaction
  does not reach; the comparison MUST be **constant-time**, so a refusal reveals nothing about how
  much of the secret was correct; the routes MUST be **excluded from any API schema or documentation
  the application publishes**, since a generated page listing them would make them discoverable and
  defeat FR-049; and a secret that is **unset or empty MUST refuse every request**, never admit
  every request — a deployment that has not configured one has no admin, not an open door.
- **FR-049**: This MUST NOT introduce a user role, and the word *admin* MUST NOT be read as one.
  There is no admin account, no admin login, no admin session, and no admin view: "admin" names a
  **maintenance capability**, not a person and not a third kind of user. FR-031 is unchanged —
  patients and staff still never log in, and the anonymous session remains the only identity in the
  application. The admin capabilities MUST NOT be reachable or discoverable from the console UI,
  MUST NOT appear in any published schema (FR-048a), and nothing in the product MUST depend on
  them. The distinction matters more under this name than it did under the previous one
  (*operator*), which is why it is stated here rather than left to context: a reader meeting
  "admin routes" in a codebase reasonably expects an account behind them, and there is none.
- **FR-050**: The admin secret MUST never be logged, echoed back, or returned in any response,
  and MUST be carried by the existing secret-redaction path rather than a new one.
- **FR-051**: Deletion spans two independent stores with no transaction between them, so a partial
  outcome is reachable and MUST be reported as one. Where a session could not be fully removed, the
  operation MUST say which sessions were left incomplete rather than reporting success — the same
  rule this project applies to any write whose outcome it does not know. Re-running the deletion for
  those sessions MUST be safe and MUST converge.
- **FR-052**: Deleting **all** sessions MUST offer exactly the guarantees of deleting one, applied
  to each: the same removal, the same partial-outcome reporting, and the same secret.

#### Scope boundary

- **FR-043**: Out-of-band notification — email, SMS, or any delivery outside the application — MUST
  remain out of scope. In-app marking and counting is the whole of FR-027 to FR-029c.
- **FR-044**: Staff MUST NOT gain scheduling capabilities in this feature. They reply in
  conversations and manage practitioners and FAQ entries; booking, rescheduling, and cancelling on a
  patient's behalf are not part of the console.
- **FR-045**: Operational analytics over the console — volumes, response times, escalation rates —
  MUST remain out of scope, as `docs/ROADMAP.md` places them in Phase 3+.
- **FR-045a**: Data retention MUST remain out of scope. Nothing MUST expire, age out, or be
  redacted: conversations, their messages, their marks, and everything else a session owns persist
  for as long as the session does, and admin deletion (FR-046) MUST remain the only removal path.
  This holds on one stated precondition — the system carries **synthetic data only**, with fictional
  patients and no real clinical content. It is a declared boundary rather than an omission, and any
  use approaching real patient content would make retention, redaction, and an expiry path
  prerequisites rather than later work.
- **FR-045b**: Accessibility and localization MUST both remain out of scope. No accessibility
  standard, keyboard-navigation rule, screen-reader semantic, or contrast criterion is required of
  any screen this feature adds, and no carve-out is made — emphasis (FR-029) and message marks
  (FR-027a) may be conveyed visually alone, with the accepted consequence that the staff side's
  primary signal is imperceptible to a screen-reader user. The application MUST be **English only**:
  all interface copy and all assistant output are English, with no translation layer and no
  provision made for one. Both are declared boundaries with their costs named, not omissions.

### Key Entities

- **Conversation (Chat)** *(existing)*: gains three facts, deliberately kept apart because they
  answer different questions. **Escalated** — with the time it was escalated, which orders the list
  — means the assistant asked for a person and nobody has dealt with it; it has no deadline.
  **Assistant paused until** — a stored deadline, not a running timer — means a staff member is
  leading the conversation right now. **Unread** means a patient spoke while the assistant was
  silent and no staff member has answered since. Whether to generate a reply reads the first two;
  what to mark for attention reads the first and the third. An escalation additionally carries the
  **reason** it was raised (FR-007a), which is set once and never overwritten by a later request.
  Whether the conversation is *emphasized* is derived from its outstanding clearable marks rather
  than stored beside them (FR-029). Collapsing any pair of them would make
  one value mean two situations: an expiring escalation would let a patient's request for a human
  lapse on a timer, and a mark cleared by reading would say "answered" when nobody had.
- **Message** *(existing)*: widens from two senders to three. A staff message carries the same
  shape as any other — an author, text, and its place in the order — and none of the fields that
  only ever describe a generated answer. A **patient** message may additionally carry an *attention
  mark*: which of FR-027a's four kinds it is, and — following from the kind alone — whether a staff
  reply clears it or it stays forever. The kind is the whole of the mark; there is no separate
  "cleared by" field to disagree with it.
- **Staff member** *(no entity at all)*: there is nothing to model. Not a table, not a derived
  value, not a field — the concept is fully expressed by a message whose sender is `staff`, in a
  chat that already names its session (FR-022). An earlier draft had it as a derived display name,
  which was already the smallest possible record; dropping the name removed the last thing that
  record would have held, and with it the pool, the derivation, and the stability requirement the
  derivation needed. What the patient sees is a **label**, and a label is a property of how a
  message is rendered, not of an entity behind it (FR-023). The trade is named: giving staff any
  real attribute later — a name, several of them per session, a role — means introducing the record
  then, and nothing here makes that harder.
- **Conversation list** *(derived, not stored)*: every conversation in the session, escalated ones
  marked and first among them, ordered by how long each has waited. It is a view over the
  conversations' own state, not a second record that could disagree with them.
- **FAQ entry** *(existing, now scoped and revisioned)*: gains an owning session, and with it a
  lifetime bounded by that session. It also names its **live revision** — the one set of indexed
  chunks retrieval may search for it — which is what both retrieval and the console consult to
  decide whether it can be answered from. That is not a duplicate of anything the index holds: the
  index holds several revisions of an entry's chunks and cannot say which is current, so the entry
  says. Publishing a new revision is what a save *is*. The scope has to reach the retrievable chunks
  too, not only the stored row, or retrieval would answer from a corpus the reader does not own
  (FR-039a).
- **Chunk revision** *(new)*: one immutable set of an entry's indexed chunks, written whole and
  never edited afterwards. Each chunk carries the entry it belongs to as well as its revision, which
  is what lets a sweep address an entry's revisions without a history record. Exactly one revision
  per entry is live at a time — the one its row names; every other, whether superseded by a later
  commit or written by a save that never published, is unreachable to retrieval and awaits the
  sweep (FR-042b, FR-042h). It exists so that writing new chunks never destroys the ones currently
  being answered from.
- **Practitioner** *(existing)*: unchanged. The console is a new caller of capabilities that already
  exist, not a new set of rules about them.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A patient who asks for a person is told, in that same turn, that staff have the
  conversation — in 100% of attempts across the test suite, including turns where the request is
  mixed with an ordinary question.
- **SC-001a**: Every abstention ends with a person. Across a suite of questions the corpus does not
  answer — including a suite run against an **empty** corpus — 100% of abstentions escalate the
  conversation and say so, and zero produce a generated answer alongside the abstention. Zero
  abstentions are exempted for corpus size. Across a suite of booking refusals, zero conversations
  are marked or emphasized at all; across a suite of unreachable-service runs, zero conversations
  are *silenced* (FR-003d).
- **SC-002**: Zero assistant replies are generated while a conversation is silent. Across a suite in
  which patients send further messages into escalated conversations and into paused ones, no
  generation, classification, retrieval, or tool call is issued for any of them.
- **SC-002c**: Zero partial assistant replies survive a staff message. Across a suite that posts as
  staff at every point in a generation — before the first token, mid-stream, and just before
  completion — 100% of those generations are cancelled and zero partial replies are persisted or
  displayed.
- **SC-002a**: A pause lasts two minutes and no longer, however it started. Across the suite —
  covering pauses started by a staff message and pauses started by the switch alone — 100% of paused
  conversations answer the next patient message once the two minutes have elapsed with no staff
  action, and zero remain silent afterwards; each further staff message and each further switch-off
  restarts the two minutes in 100% of cases; and a pause started by the switch is indistinguishable
  in duration, storage, and expiry from one started by a message.
- **SC-002b**: An escalation never expires. Across a suite that leaves escalated conversations
  untouched well beyond the pause duration, 100% remain silent and marked, and zero are answered by
  the assistant without a staff message or an explicit return.
- **SC-003**: Zero patient messages are lost while the assistant is silent: 100% of messages sent
  into an escalated or paused conversation appear in the thread, in the order sent, and mark the
  message unanswered and emphasize its conversation.
- **SC-004**: A staff reply reaches the patient's thread, and a new escalation or unanswered message
  reaches the staff list, within 3 seconds and with no manual refresh, in 100% of attempts.
- **SC-005**: The list and the conversations never disagree: across the test suite, the set shown as
  escalated equals the set in the escalated state, and the set shown emphasized equals the set
  holding an outstanding clearable mark, with zero conversations in one and not the other. Every
  conversation in the session is listed, emphasized or not.
- **SC-006**: Both silencing states survive every interruption tested — page reload, second tab, and
  backend restart — in 100% of cases. Zero escalated conversations revert to being answered without
  a staff message or an explicit return, and a pause resumes with the correct time remaining rather
  than restarting or vanishing.
- **SC-007**: Zero escalations are ended by anything other than a staff message or the staff
  member's explicit return, across a suite including runs where the patient asks to go back to the
  assistant, where the assistant is asked to end its own silence, and where the conversation is
  simply left alone for longer than the pause duration.
- **SC-008**: Marks are exact and their lifetimes are decided by kind alone. Across the suite, 100%
  of patient messages arriving while the assistant was silent are marked unanswered and emphasize
  their conversation; zero marks or emphasis are cleared by opening, reading, scrolling, a reload, a
  second tab, a pause expiring, or the assistant switch; one staff message clears 100% of the
  clearable marks in that conversation and its emphasis, however many had accumulated; and 100% of
  corpus-could-not-answer and assistant-failed marks survive that same staff message, and every
  later one, unchanged.
- **SC-009**: A conversation the assistant has resumed is answered on the patient's very next
  message, in 100% of cases, and its reply reflects the staff messages in the thread — zero replies
  contradict a staff message that preceded them in the same conversation.
- **SC-009a**: Staff can speak anywhere in their own session: across a suite posting into escalated,
  paused, and ordinary conversations, 100% are accepted and each pauses the assistant for two
  minutes. Zero staff messages are refused for the state the conversation was in.
- **SC-009b**: The assistant never answers into a silence after the fact. Across a suite that leaves
  one, two, and five patient messages unanswered during escalations and pauses, zero of them are
  answered when the assistant is allowed to speak again, and zero are merged into the turn that
  answers the patient's next message — while 100% of those next messages are answered normally.
- **SC-009c**: The staff-side assistant switch always matches reality: across the suite, its
  position equals whether a reply would actually be generated in that conversation in 100% of
  samples, including immediately after an escalation, immediately after a staff message, and at the
  moment a pause expires.
- **SC-009d**: Every escalation carries the right reason and shows it on the right message: across
  a suite of patient requests, corpus-gap questions, and induced failures, 100% are labelled with
  the trigger that actually raised them, 100% mark the message that caused them, and zero
  escalations exist without a reason or with a reason overwritten by a later request against an
  already-escalated conversation.
- **SC-009e**: The refusal/failure line holds. Across a suite of every booking refusal reason 006
  defines, zero call staff; across a suite of unreachable-service, unknown-outcome, and tool-error
  runs, 100% call staff, are labelled *assistant failed*, and mark their message permanently.
- **SC-009f**: A failure never costs a conversation its assistant. Across that same failure suite,
  100% of those conversations answer the patient's very next message — including a retry of the
  thing that failed and an unrelated FAQ question — and zero are silenced (FR-003d). Each remains
  emphasized until a staff member replies, and its permanent mark survives that reply.
- **SC-010**: Every escalation, every resumption, and every pause is recoverable from the logs
  alone: for 100% of transitions, the conversation, which transition it was, and the turn identifier
  are present. Across a suite that escalates conversations already escalated, the number of
  escalation records equals the number of conversations actually silenced, with every no-op request
  present as one. Measured over runs in which the logging path is working, since recording never
  gates a transition (FR-034).
- **SC-011**: No conversation, practitioner, staff member, attention mark, or FAQ entry crosses a
  session boundary. Zero cross-session reads and zero cross-session writes across the test suite,
  including attempts that address a well-formed identifier belonging to another session.
- **SC-011a**: One session's corpus edits never change another session's answers. Across a suite
  where two sessions have each built a corpus and one deletes, edits, and adds entries, 100% of
  the other session's answers and citations are what they were before — and zero chunks belonging to
  either session are retrieved by, cited by, or counted toward the groundedness of the other.
- **SC-011c**: Every message is attributable to its role at a glance, and to nothing more. Across
  the suite, 100% of staff messages are labelled *Staff* and 100% of assistant messages are labelled
  *AI assistant*, in the patient's thread and in the staff view alike; zero messages carry a
  person's name, a staff identifier, or any other staff attribute in any response, rendered screen,
  or log line; and zero sessions require any migration or backfill to work.
- **SC-011b**: Creating a session costs nothing in the retrieval path: across every session created
  in the test suite, zero embedding calls and zero retrieval-store writes are issued at
  provisioning, and a session created while the retrieval store is unreachable still yields a
  working chat in 100% of attempts.
- **SC-012**: The session credential is never readable by frontend code — zero occurrences across
  the suite, including every console capability added by this feature.
- **SC-013**: Every practitioner rule the scheduling service enforces is enforced through the
  console too. Across a suite exercising duplicate names, overlapping working ranges, and deletes of
  a practitioner holding appointments, 100% of refusals are shown to the user with their reason and
  zero leave a partially applied change.
- **SC-014**: A practitioner edit is visible to the assistant on the next question: in 100% of
  tests, the roster, specialty, or offered times the assistant reports after an edit match what the
  console shows.
- **SC-015**: Everything the FAQ screen lists is answerable, with the text it shows. Across a suite
  covering an empty corpus, entries created, edited, and deleted, and every operation failed at each
  step of its sequence, 100% of listed entries are retrievable by the assistant, 100% of them are
  retrieved with the text the screen showed, and zero screens present a per-entry retrievability
  state at all.
- **SC-015a**: A failed operation changes nothing. Across a suite failing every create, update, and
  delete at the embedding call, at the chunk write, and at the publishing commit, 100% leave the
  entry's content, its live revision, and what the assistant answers from exactly as they were, and
  100% report the failure and that it can be retried. Zero leave an entry that was retrievable
  beforehand unretrievable afterwards, and zero require a person to repair the index.
- **SC-015b**: A revision that is not live is never answered from. Across that same suite, including
  runs whose chunk write partially succeeded before failing, zero chunks of a superseded or
  never-published revision are retrieved, cited, or counted toward groundedness.
- **SC-015c**: Retrying always converges. Across that same suite, resubmitting each failed operation
  once the dependency recovers succeeds in 100% of cases with the content the staff member last
  submitted; resubmitting an operation that already succeeded changes nothing, produces no duplicate
  entry, and leaves the same single revision live.
- **SC-015d**: Superseded chunks do not accumulate. Across a suite of repeated edits and deletes,
  including runs whose sweep is forced to fail, zero superseded chunks are ever retrievable, and
  after each entry's next successful save — or its session's deletion — zero of them remain stored.
- **SC-015f**: An unreachable corpus is never reported as an empty one. Across a suite that makes
  the stored rows unreadable mid-turn, zero turns produce an abstention, zero escalate on
  groundedness grounds, and 100% report the unreachable dependency; across the same suite run
  against a genuinely empty corpus, 100% abstain and escalate and zero report a dependency failure.
- **SC-015e**: The corpus cap holds, costs nothing below it, and binds nothing else. Across a suite
  that fills a session's corpus to the cap, 100% of creates beyond it are refused with a message
  naming the reason, zero of them change either store, and 100% of edits and deletes on the full
  corpus still succeed. Below the cap, zero creates are refused. Across a suite adding practitioners,
  chats, and messages well past any comparable figure, zero are refused for count.
- **SC-016**: An FAQ change is visible in what the assistant says: after adding an entry, 100% of
  matching questions cite it; after deleting it, 100% of the questions it alone supported end in
  abstention rather than an unsupported answer.
- **SC-018**: A deleted session leaves nothing behind. Across a suite deleting sessions holding
  chats, messages, marks, a staff member, FAQ entries, patients, practitioners, and appointments,
  100% of those records are gone from both stores afterwards, zero retrievable chunks survive their
  entries, and zero other sessions are affected.
- **SC-019**: The admin secret is required and never disclosed. Across the suite, 100% of
  deletion requests without it or with a wrong one are refused and change nothing; zero responses,
  logs, or error messages contain the secret or say which part of the check failed.
- **SC-019a**: The admin routes are closed by default and invisible by design. With the secret
  unset or empty, 100% of deletion requests are refused and zero sessions are removed; zero
  occurrences of the secret appear in any access log, URL, or published API schema across the suite;
  and zero admin routes appear in whatever schema or documentation the application publishes.
- **SC-020**: A partial delete is never reported as success. Across runs where one store is made
  unreachable mid-deletion, 100% of affected sessions are reported as incomplete, and re-running the
  deletion for them completes without error and leaves the same end state.
- **SC-017**: The screen shows both sides at once without a login step: zero authentication prompts,
  and a visitor can raise an escalation from the patient side and answer it from the staff side in
  under 60 seconds of interaction.

## Assumptions

- **This is the second half of Phase 1d.** Part 1 — rescheduling and cancellation
  (`specs/006-reschedule-and-cancel/`) — is shipped, and everything it established holds unchanged.
  This half changes *who is talking*, not what can be booked.
- **One control, both directions, and no state of its own.** `docs/ROADMAP.md` says the staff
  member "resolves it or hands it back", which reads as two actions; replying already does both, so
  the switch is not the usual route to either. What it adds is the two cases replying cannot cover:
  taking a conversation *before* writing anything — to read it properly, or to think — and handing
  it back without writing anything, after an escalation raised in error. Both directions reuse state
  that already exists: on clears the escalation and the pause, off is the pause. Nothing about the
  switch is stored beside what a staff message writes, so no reader has to tell a pause a person
  asked for from a pause a message caused (FR-017b).
- **Two minutes is chosen, not derived**, in the same spirit as 006's cap of 20 and 005's 90-day
  horizon. It is long enough to type a follow-up sentence and short enough that a staff member who
  wandered off does not strand the patient, and it can be changed without touching any other rule.
- **The patient sees no countdown, no switch, and no pause indicator.** Both are staff-side
  affordances; from the patient's side the assistant is simply not the one talking, and the messages
  around the silence are what explain it (FR-019). Nothing in this phase tells the patient that a
  timer is running or that a switch exists.
- **A staff message pauses the assistant even in a conversation nobody escalated**, because the
  reason for the pause is that a person is leading the conversation, not that a mode was entered
  (FR-013).
- **There is no "claim" or "take over" step.** A session has exactly one staff member, so there is
  nobody to claim a conversation from. Escalated means available to the one person who could answer
  it.
- **The abstention path is wired here, and Phase 1e replaces its gate rather than its wiring.**
  `docs/ROADMAP.md` lists "an explicit abstention path that escalates via 1d's `escalate_to_staff`
  tool" under Phase 1e. What 1e actually adds is a better *decision* about whether retrieval was
  sufficient — two gates, measured thresholds, a typed verdict — not a different consequence of
  deciding it was not. Building the consequence now means 1e re-points an existing caller instead of
  inventing one, which is why this does not violate phase order. **What changes today**: an
  abstention stops being a dead end. The existing abstention message gains a handoff, so the reply a
  patient gets on an unanswerable question is different from the one they get now (FR-003, FR-003b).
- **The abstention trigger is whatever the current sufficiency check decides.** This spec does not
  pin the threshold, the number of chunks, or the scoring scale — those are 1e's to measure. It pins
  only that whatever signal makes the assistant abstain is the same signal that escalates, so the
  two can never disagree.
- **The `call_staff` intent already exists** in the classifier and currently falls back to the FAQ
  path. This feature gives that label somewhere real to go; no new intent label is needed.
- **No timeframe, no SLA, no auto-escalation on delay.** Nothing measures how long a patient has
  waited beyond ordering the list, and nothing escalates or re-escalates on a timer. The one
  deadline in the feature is the pause, which ends a silence rather than starting one.
- **All four mark kinds are surfaced identically at conversation level, for now.** The list says a
  conversation needs a person; it does not rank or colour-code why. The distinction lives on the
  message, where the mark is. Treating them differently in the list is a later refinement, not a
  requirement this phase carries.
- **Calling staff and silencing the assistant are separate consequences of one act.** All three
  reasons call staff; only two of them silence. Treating "staff were called" and "the assistant is
  quiet" as the same fact was the conflation this session removed, and the grid in FR-027c is what
  keeps them apart.
- **The permanent marks are the phase's one piece of retained diagnostics.** They are the only thing
  in this feature that survives being dealt with, and they exist because a corpus gap and a system
  failure are worth counting after the patient has been looked after — which is exactly what Phase
  1e and Phase 2 will want to count.
- **The admin deletion paths are a maintenance surface, not a feature of the product.** Nothing a
  patient or staff member does reaches them, the console does not link to them, and no user story
  above depends on them. They exist so that FR-039c has a trigger and so a demonstration can be
  reset — which is also why one secret in environment configuration is proportionate, where an auth
  system would not be.
- **"Admin" is a capability, not an account.** The name was chosen over *operator* for being the
  ordinary word for this, and it carries an implication the previous name did not: that somebody
  logs in as one. Nobody does (FR-049). The console never links to these routes, no screen exposes
  them, and the only thing that distinguishes a caller who may use them from one who may not is a
  secret in environment configuration.
- **The admin secret is not an authentication system**, and this phase does not pretend
  otherwise: one shared secret, no accounts, no roles, no rotation, no record of who used it. It
  guards a destructive maintenance action from being triggered by accident or by a stranger who
  found the path, which is the whole of what it is for.
- **The reason is a label, not an explanation.** It says which of FR-003's three triggers fired,
  nothing more — not what the patient asked, not what retrieval scored, not what the staff member
  should do about it.
- **No transcript, export, or handoff summary.** The staff member reads the thread. Nothing
  summarizes the conversation for them, and nothing is generated at the moment of handoff beyond the
  assistant's own message to the patient (FR-005).
- **No staff-side typing indicators, read receipts, or presence.** The two marks are the whole of
  what one side knows about the other's attention — and no mark is a read receipt, since every one
  of them tracks whether anyone *answered*, not whether anyone looked (FR-029a, FR-027c).
- **Escalation records are logs, not a queryable history surface**, exactly as 006's change records
  are. The conversation's current state is stored because the list and the silencing decisions are
  read from it; the
  transitions are logged. No audit table, endpoint, or screen for past escalations is part of this
  phase.
- **The FAQ corpus becomes session-scoped, which is a change to existing behavior.**
  `docs/ROADMAP.md`'s list of session-scoped nouns — "chats, patients, practitioners, and staff
  member" — does not include the FAQ, because until now nothing let a visitor change it. Giving it a
  console with a delete button is exactly what changes that, so the boundary follows the capability
  rather than the noun (FR-039). Every session now starts with **no** corpus at all and builds one
  through the console.
- **A new session's first FAQ question is expected to escalate**, and that is not a defect. With no
  corpus there is nothing to answer from, and the assistant admitting so and fetching a person is
  the behavior Principle V asks for. It also means the shortest path to a working demo is to add a
  FAQ entry on the console first.
- **Deployment starts from an empty system, and that is a requirement rather than a convenience**
  (FR-039e). Purging the pre-existing sessions and the shared corpus is not optional housekeeping
  for whoever deploys this: the schema depends on it, because ownership and the live revision are
  required columns and no row may exist that lacks them.
- **A starting template is deferred, deliberately.** Later work may give a new session a corpus to
  begin from; this feature does not, and session provisioning gains no corpus step (FR-039b). The
  consequence is accepted rather than hidden: until a staff member adds an entry, the assistant can
  answer no FAQ question in that session.
- **The FAQ write path is changed, not merely called.** Indexed chunks become immutable revisions,
  every create and update publishes a new one in a single local commit, the compensating revert is
  deleted, the delete ordering is reversed, and retrieval searches only live revisions (FR-042a to
  FR-042i). This is the fourth piece of existing behavior this feature alters, and it is the
  deepest: it removes the destructive step rather than making its consequences visible.
  `specs/001-grounded-faq-chat/` describes the old path.
- **A failing sweep is silent, and the leak it leaves is bounded rather than watched.** Nothing
  logs a sweep failure (FR-042h), so a persistently failing one accumulates unreachable chunks with
  no signal. That is accepted because the leak has three ceilings — 200 entries per session
  (FR-039f), the entry's next save, and the session's deletion — and because an event raised for
  housekeeping that failed would sit alongside events raised for operations that failed, which is
  the confusion this path spent its design avoiding.
- **The shared corpus of `specs/001-grounded-faq-chat/` is deleted, not inherited and not left
  behind.** An earlier draft left those rows in place as inert leftovers that no query reached;
  removing them instead is what makes the ownership columns required, and it is the difference
  between a rule the schema enforces and a rule every future query has to remember (FR-039e).
- **The console is English-only and has no accessibility work, by decision.** Neither is specified
  anywhere in this feature (FR-045b), and the sharpest consequence is named rather than left to be
  discovered: emphasis is visual weight, so the one signal the staff side exists to convey has no
  non-visual form. Both follow the constitution's scope discipline — effort goes to the applied-AI
  core when it must be traded — and both are the kind of boundary that is cheap to state now and
  expensive to discover later, which is why they are stated.
- **Nothing is ever deleted by the passage of time, and the data is synthetic on purpose.** There is
  no retention limit, no expiry, and no redaction anywhere in this feature (FR-045a): a conversation
  a patient had persists until an admin deletes its session, which may be never. That is
  defensible only because the content is fictional — patients come from a name pool, and no real
  clinical text enters the system — so the assumption is recorded here with the decision that rests
  on it. It is the assumption to revisit first if this were ever pointed at real patient data, since
  the isolation rules this feature does enforce (FR-032, SC-011) protect one session from another
  and say nothing about how long either is kept.
- **Corpora are small, and the cap makes that a fact rather than a hope.** A session's corpus is
  built by hand through a console, so tens of entries is the realistic shape and 200 is a ceiling
  nobody reaches by working normally (FR-039f). Two parts of the design lean on that: the
  live-revision filter retrieval carries every turn is proportional to corpus size, and the console
  lists a corpus without paging. Both hold comfortably at 200 and would need rethinking well before
  thousands — the point at which this approach, not just the cap, would deserve revisiting.
- **The cap is a bound on a mechanism, not a defence.** It is scoped to the corpus because retrieval
  carries corpus size on every turn; it is not extended to practitioners, chats, or messages, none
  of which any design here is sized against (FR-039g). Those stay unbounded on a surface with no
  login (FR-031), with admin deletion (FR-046) as their only reclaim, and that is a decision this
  spec takes rather than an oversight to be found later: a feature about escalation is not where a
  rate-limiting story belongs, and the growth costs the session that causes it and nobody else.
- **The trade is leaked storage, and it is taken knowingly.** Additive writes leave superseded
  chunks behind, and a failed sweep leaves them longer. They are unreachable throughout, the sweep
  is idempotent, and the entry's next save or its session's deletion clears them, so the leak is
  bounded by the session's lifetime. The alternative trades the other way — under a destructive
  save, one failed edit costs a working entry until a person repairs it — and an invisible byte is
  worth less than an answer a patient does not get (FR-042h, FR-042i).
- **An earlier draft of this spec used a readiness flag, and it is withdrawn.** *Pending/ready* is
  the right design while a save deletes the old chunks before writing new ones: something then has
  to record *an operation is in flight*, which neither store can express. Immutable revisions remove
  the in-flight state itself — a revision is published by the commit or it is not — and with it the
  state machine, the retrieval rule excluding pending entries, the content rollback that was itself
  a fallible write, and the human retry a working entry's availability depended on. The draft's
  original objection to a stored flag, that it duplicates what the stores already determine, is
  therefore reinstated. The live revision is not that flag: the index holds several revisions of an
  entry and cannot say which is current.
- **A transactional outbox was considered and deliberately not used.** It is the standard answer to
  a dual-write problem and would upgrade "the staff member resubmits" to "the system converges on
  its own". It is rejected here for three reasons, in order of weight. `docs/ROADMAP.md` places the
  outbox — with a broker and idempotent consumers — in **Phase 3+**, and Principle I of the
  constitution forbids pulling a platform layer forward, which is the binding objection on its own.
  It needs a background worker this phase does not have and would not otherwise introduce. And
  there is no correctness hole left for it to close: publishing a revision is a single-store commit
  rather than a dual write, so the only thing an outbox would automate is the sweep of superseded
  chunks — housekeeping that is already idempotent and already converges. The seam is left in the
  right shape for it if that sweep ever wants a worker.
- **Existing behavior is preserved**, with two named exceptions: conversation history, intent
  classification, booking, rescheduling, cancellation, the practitioner and appointment listings,
  chat creation, renaming, and deletion all continue to work unchanged in an open conversation. The
  exceptions are deliberate and are the substance of two clarifications above — an abstention now
  ends in a handoff instead of a dead end (FR-003), and FAQ answering now reads the session's own
  corpus instead of a shared one (FR-039).
- **There is no staff member to model — only a label.** An earlier draft made it a name derived
  from the session identifier; this one drops the name, and with it the pool, the derivation, and
  the requirement that the derivation stay stable across restarts. What remains is `staff` as a
  sender value and *"Staff"* as what the patient reads (FR-022, FR-023). If a later phase gives
  staff real attributes — several of them per session, an editable name, a role — that is when the
  record gets introduced, and this feature is no harder to extend for having left it out.
- **The patient is shown a role, never a person.** Unlike a practitioner or a patient — both of
  which are real records with names drawn from a pool — staff is not somebody, it is a side of the
  conversation. Naming it would have implied a person the system does not have.
- **Times remain plain local times with no timezone**, as established in Phase 1c and unchanged by
  Phase 1d part 1.

## Dependencies

- **Phase 1c's tool registry** (`specs/005-scheduling-and-booking/`): `escalate_to_staff` joins the
  existing tools through the same seam, so agent reasoning stays decoupled from what a handoff
  actually does.
- **Phase 1a's conversation history** (`specs/003-conversational-chat-history/`): the flat, ordered
  message log is widened from two senders to three, which is the extension 1a was built to allow.
  One of its rules is also **overridden**, not extended: 1a merges consecutive unanswered patient
  messages into a single turn, on the reasoning that a patient typing three quick lines is asking
  one question. A message left unanswered because a person was handling the conversation is not part
  of that question, so messages from a silent window are excluded from the burst a later turn
  answers (FR-019b). This is the second place where the feature changes behavior that already works,
  alongside the FAQ corpus becoming session-scoped.
- **Phase 1b's intent classification** (`specs/004-langgraph-intent-classification/`): the
  `call_staff` label and the graph's routing are reused; the escalation path is a new destination
  for an existing label, not a new classifier.
- **Phase 002's structured logging**: escalation records are written through the existing
  conventions and carry the existing turn correlation identifier.
- **Phase 1c's REST practitioner-management surface**, which the console drives. `docs/ROADMAP.md`
  describes this half as needing "no new backend" beyond the escalation path itself; that is not
  quite right, and the correction belongs here rather than in the plan. The session identity is
  carried in an `HttpOnly` cookie the browser cannot read, and the scheduling service's
  practitioner REST API expects the session as an explicit credential on the request — so frontend code cannot
  call it directly without giving up the property FR-036 and SC-012 protect. The core backend must
  therefore carry practitioner administration on the console's behalf. The rules stay where they
  are; only the route to them is new.
- **The existing FAQ CRUD and its Postgres↔Qdrant ordering**: the console is a new caller, but
  FR-042a to FR-042i **replace** that ordering rather than preserve it. Embedding moves before
  either store is written; chunks are written additively under a new revision instead of
  delete-then-upsert; one local commit publishes that revision and is the only moment a change
  becomes visible; the compensating revert is removed, because a best-effort repair that
  half-succeeds silently is the one thing that can leave the two stores disagreeing with nobody
  told; the delete ordering is reversed, since unretrievability now comes from the row rather than
  from the index being empty; and retrieval gains a live-revision filter alongside its session
  predicate. FR-039 to FR-039d change the entries themselves besides, by giving them an owning
  session that has to reach the retrieval index as well as the stored row. This is the largest
  change this feature makes to something that already works, and it touches
  `specs/001-grounded-faq-chat/`'s model directly rather than extending it. It also supersedes the
  ordering rule recorded in `.claude/CLAUDE.md`, which has to be updated with it. Session
  provisioning is left alone: the corpus starts empty, so nothing is added to it (FR-039b).
- **A new session-level delete in the scheduling service.** It can delete one patient for a chat and
  one practitioner today; FR-047 needs it to remove a whole session's patients, practitioners, and
  appointments in one call. This is the second capability this feature adds across the service
  boundary, alongside the practitioner administration of FR-036, and it is why "no new backend" does
  not describe this half of Phase 1d.
- **The existing frontend** (`services/frontend/`): the patient chat list and chat window are kept
  and joined by a second pane, rather than replaced.
