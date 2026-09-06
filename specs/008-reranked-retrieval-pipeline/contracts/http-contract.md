# Contract: HTTP and UI Surfaces

## `POST /chat` — terminal NDJSON event

```diff
  {"type": "done",
-  "grounded": true | false | null,
+  "faq_verdict": "answered" | "answered_unreranked"
+                 | "abstained_empty_corpus" | "abstained_similarity_floor"
+                 | "abstained_rerank_floor" | null,
   "citations": [{entry_id, chunk_index, chunk_text}],
   "message": "...", "answer_source": "faq" | "booking" | "merged" | "hand_off"}
```

`citations` is **unchanged and still sent on the patient path** (FR-016a). The patient pane does not
render it (FR-023d); that is presentation, not access, and FR-023f forbids describing it as
withholding — one session owns both panes and can read the whole corpus on the FAQ screen anyway.

`faq_verdict` is `null` exactly where `grounded` was: no FAQ specialist ran.

## `GET /chats/{id}` and `GET /console/chats/{id}` — `MessageOut`

`grounded` → `faq_verdict`, same nullability. **One shape for both endpoints** — the patient
response and the console response stay identical, which is the point of FR-016a: two shapes are two
things that can drift.

## Frontend

| Component | Change |
|---|---|
| `MessageView` | Citations render only when told to (new prop). `data-grounded` → `data-faq-verdict`. New marker, `data-testid="verdict-mark"`, shown **only** for `answered_unreranked`, with a `title` explaining it (FR-023a). |
| `ChatWindow` (patient) | Passes citations off. Renders no citation list and no verdict marker, on any verdict (FR-023b, FR-023d). |
| `StaffThread` (console) | Passes citations on. Renders the verdict marker (FR-023a, FR-023e). |

The marker must be visually distinct from spec 007's `attention-mark`, which sits on **patient**
messages and means a person is needed; this sits on an **assistant** message and means the answer
above it is second-best (FR-023c). Different `data-testid`, so a test can never pass by finding the
wrong one.

**No scores anywhere in the UI** (FR-023g) — not in a tooltip, not behind a toggle. `Citation`
carries none, which makes the rule structural rather than a habit.

## Test hooks

| Hook | Where |
|---|---|
| `data-faq-verdict` | on the message element, both panes (replaces `data-grounded`) |
| `data-testid="citations"` | staff pane only |
| `data-testid="verdict-mark"` | staff pane, `answered_unreranked` only |

`ChatWindow`'s spec must assert citations are **absent** from the patient pane — a rule that only
ever appears as an absence needs a test that fails if the absence stops holding.
