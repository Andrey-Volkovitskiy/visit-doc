# The chosen design direction

`a-front-desk.html` is the mockup this feature's visual design was chosen from. Open it directly in
a browser — it is a single self-contained file and needs no server, no build and no running stack:

```bash
wslview specs/015-frontend-design-pass/design/a-front-desk.html
```

## What it is

A **static mockup**, not a prototype. Nothing in it is wired to anything: no fetch, no state, no
routing. Every message, name and FAQ entry in it is real content — the patients are from
`scheduler.domain.name_pools.WRITER_POOL`, the practitioners from `PHYSICIAN_POOL`, and the cited
FAQ text is a genuine entry of `chat.rag.default_corpus` — so that the layout was judged against the
lengths and shapes the real product produces rather than against lorem ipsum.

It was one of three, spanning conventional to experimental; this is the one that was chosen, and the
other two are deliberately not kept. A rejected alternative invites a later "what if we went back to
B" that re-opens a settled decision, and the reasoning that mattered is recorded in the spec.

## What it is authoritative for, and what it is not

**Authoritative**: the palette, the type scale and spacing rhythm, the treatment of the two panes,
message bubbles and sender grouping, the conversation rail, the tab sets, the assistant switch, and
the expanded evidence block. Where the spec's requirements and this file disagree about *how
something looks*, this file is the reference.

**Not authoritative**: anything about behaviour, structure or implementation. The mockup hard-codes
one open state of everything at once; it renders both evidence blocks expanded because that is the
only way a static page can show them; and its markup is written to produce a picture, not to be
lifted into components. In particular it predates the decision that the evidence block opens on
**click** — the mockup simply shows the opened state.

The spec is authoritative for behaviour, and `/speckit-plan` is authoritative for structure. Where
this file and `../spec.md` disagree about what the interface *does*, the spec wins.

## Two things drawn here that the backend cannot serve

Both are recorded in `docs/ROADMAP.md` Phase 3a and appear in the mockup as deliberate stubs rather
than as invented data — see FR-031 and FR-033 in `../spec.md`:

- the booking outcome inside the evidence block, and
- a practitioner's standing appointments for the next seven days.

## Known defects in the mockup

Carried here as-is rather than silently patched, so that a later "the mockup does it this way" is
not mistaken for a decision. **The list stays**; each line now also says what the shipped
implementation did about it.

- **It loads its typeface from a font service.** The product does not: the clarification session of
  2026-09-23 settled that the font is self-hosted (`../spec.md`, FR-004). This file was written
  before that and still carries a `fonts.googleapis.com` link. The typeface it selects is right;
  the way it fetches it is not.
  → **Resolved in the product, and left standing here.** `services/frontend/src/styles/app.css`
  `@font-face`s three committed `woff2` files from `src/styles/fonts/` with the SIL OFL 1.1 text
  beside them, and `index.html` carries no `<link>` and no third-party URL at all. This file keeps
  its `fonts.googleapis.com` link, because patching the mockup would erase the only record that the
  product's self-hosting is a decision rather than an accident.
- The staff thread clips at the bottom: the composer sits over the last message. A height/scroll
  bug in the static page, not an intended layout.
  → **Resolved, and it could not recur.** The console thread is a flex column whose message list is
  the only scrolling part (`min-h-0 flex-1 overflow-y-auto`), with the switch header and the
  composer as fixed siblings — so the composer cannot overlap the thread, it bounds it. The rule
  that keeps the newest message in view is FR-015b's, tested in
  `tests/StaffThread.test.tsx`, not a height that happens to work.
- The chat tabs read as underlined text links rather than as tabs.
  → **Resolved.** `ChatList` is a `<nav aria-label="Your chats">` of `<button>`s, with the open one
  carrying `aria-current="true"` and marked by its own surface, border and weight rather than by an
  underline. Nothing in the strip is an `<a>`, so nothing offers a link's affordances for an action
  that navigates nowhere.
- One 404 on first load, for a favicon the page does not ship.
  → **Resolved, after first being declined.** The decline is kept here rather than overwritten,
  because the objection was a fair one and what answered it is the interesting part.

  It was declined on the ground that no requirement asks for a favicon and that the product's mark
  already exists as inline SVG in the page header (`Wordmark` in `App.tsx`) — so a favicon would be
  *a second copy of the mark to keep in step*, traded for one 404. That reasoning was sound about
  the cost and wrong about it being unavoidable: `services/frontend/public/favicon.svg` is now that
  second copy, and `services/frontend/tests/favicon.test.ts` reads both files and compares their
  paths, their canvas, their rounded square and their colours against `app.css`'s tokens. "In step"
  stopped being a hope and became a checked invariant, and both halves of it were confirmed to fail
  when violated before being trusted.

  `index.html` declares `<link rel="icon" href="/favicon.svg" type="image/svg+xml">`, which is what
  actually removes the 404: with no declaration a browser asks for `/favicon.ico` by default. It is
  served from `public/` rather than imported from `src/` for the opposite reason the typefaces are
  not: a favicon must sit at one fixed, unhashed path a browser can guess, and content-hashing it
  would defeat the purpose.

  **The first version of it shipped broken, and how it was missed is worth more than the fix.** Its
  comment referred to the theme tokens by their CSS names, which put a double hyphen inside an XML
  comment. XML forbids that, so the document was not well-formed, every browser refused to decode
  it, and the tab showed nothing. All six tests above passed against it, because every one of them
  matched the file's *text* with a regex — and so did the browser check, which confirmed
  `/favicon.svg` answered 200 and `/favicon.ico` was never requested. Both were true. Neither could
  have been false: a 200 for an undecodable file is still a 200, and a browser holding a declared
  `rel="icon"` does not fall back to `/favicon.ico` when that icon fails to parse. **The
  verification was structurally incapable of detecting the failure it was there to detect**, which
  is the same defect this feature's spec calls a test that cannot fail for the right reason.

  It was caught by a person opening the page and saying the icon was not there. What replaced the
  bad check: four tests that parse the file with `DOMParser` and assert a well-formed SVG root with
  a drawable shape — confirmed to fail when the double hyphen is put back — and a browser check
  that loads the file as an `<img>` and reads `naturalWidth`, which is 0 for a file that does not
  decode and 26 for this one.
