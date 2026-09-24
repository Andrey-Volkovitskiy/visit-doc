# T060, T061, T063, T067 — the browser pass, recorded

The tier `quickstart.md` says the automated suite cannot answer. Run 2026-09-23 against the
production build (`npm run build`, served by `vite preview` on `:4173`) in the cached Chromium from
`CLAUDE.local.md`, driven by a throwaway Playwright script rather than by hand, so the numbers are
measured rather than judged.

**What the backend being absent does and does not affect.** The preview server serves the built
SPA and 404s every API call. That is fine for all four checks here and is why they could be
automated: FR-010a makes the whole structure — header, both panes, their headings, the tab set —
render before any request returns, so layout, focus order, request origins and motion are all
observable without a stack. It is *not* enough for the checks that need live content (a streaming
reply, two markers open across a poll tick, the overflow with six chats); those stay in
`quickstart.md` as browser work against a running stack.

## T060 — the four widths (FR-008, FR-009, SC-001)

`document.documentElement.scrollWidth - clientWidth`, and the computed column count of `<main>`.

| Width | Horizontal overflow | Pane columns | Side by side |
|---|---|---|---|
| 1440 | **0px** | 2 | yes |
| 1100 | **0px** | 2 | yes |
| 900 | **0px** | 1 | no |
| 375 | **0px** | 1 | no |

The stacking boundary behaves as `--breakpoint-panes: 1100px` states: 1100 is still side by side,
900 is stacked. A 200-character unbroken string was typed into the composer at each width before
measuring; no element's right edge passes the viewport at any of them.

**This pass found a defect and it was fixed.** The first run measured **1px of horizontal overflow
at 375px** — and one pixel of horizontal scroll is still a horizontal scrollbar. A second probe
named the element: the third tab trigger ("FAQ"). Three `whitespace-nowrap` labels plus `gap-6` and
`px-4` came to one pixel more than the pane gave them. The tab strip now uses `gap-3`/`px-2` below
`sm` and carries `overflow-x-auto`, so it scrolls inside the pane instead of widening the page, and
`<main>` drops from `p-6` to `p-4` there. Re-measured: 0px.

## T061 — the keyboard (FR-039, FR-040, SC-002)

Tab was pressed repeatedly from a fresh load and `document.activeElement` read after each press,
with its computed outline and box-shadow.

- Focus order follows reading order: chat tab → its delete → new chat → the thread → the composer
  → the staff tab set → the open tab's panel. It then leaves the document, because with no backend
  the console holds no conversation to focus and the staff thread renders no composer.
- **Focus is visible on every focusable element**, and every one of them matches `:focus-visible`.
- The tab set is operable by keyboard and announces itself: focusing the first tab and pressing
  ArrowRight moves focus to "Practitioners" and sets `aria-selected="true"` on it — Radix's roving
  tabindex, which is one of the three controls the stack was adopted for.
- Nothing focusable is invisible.

**This pass found a defect and it was fixed.** The first run reported one focusable element with
neither an outline nor a ring: the tab **panel**, which Radix makes focusable (`tabindex="0"`) so
its content is reachable. The vendored `TabsContent` shipped `outline-none`, and a class beats the
zero-specificity `:where(...)` global rule in `app.css`, so a reader tabbing into the panel was
given no sign of where they were. `outline-none` was removed from the primitive.

## T063 — reduced motion (FR-042)

Measured in a context with `reducedMotion: "reduce"`, against a probe element carrying the
indicator's own classes (`working-dot animate-dot`) — the indicator itself only exists mid-turn,
which needs a live backend.

| Property | Value |
|---|---|
| `animation-name` | `none` |
| `animation-duration` | `0s` |
| `opacity` | `0.55` |

The animation is off and the dots keep a visible resting opacity rather than settling at the
keyframe's 0.28. **Suppressing the motion does not suppress the state**: the indicator's meaning is
carried by `role="status"` and the accessible name "The assistant is replying", neither of which a
media query can reach — pinned by a unit test as well, so a build that conveyed "working" only by
movement would fail.

The global block also shortens every animation to 0.01ms with one iteration, which is what covers
the dialog and dropdown enter/exit animations `tw-animate-css` brought with the vendored
components. That is the quickstart's "verified, not assumed" item, and this is the verification.

## T067 — no third-party request (FR-004, SC-010)

Every request the page issued was recorded for 2.5s after load, long enough for the 2-second
console poll to fire.

- **11 requests, 0 off-origin.** Every one is `http://localhost:4173`.
- The three typefaces are served from the app's own origin, content-hashed by Vite:
  `ibm-plex-sans-latin-{400,500,600}-normal-*.woff2`.
- No `fonts.googleapis.com`, no `fonts.gstatic.com`, no CDN.

## Still only checkable against a running stack

Left in `quickstart.md`, not done here, and not claimed: the working indicator appearing within
half a second of a real send and staying absent while the assistant is paused; a reply typing in
while a staff post arrives whole; two evidence markers open at once surviving a poll tick; the
overflow control with six real chats; and the greyscale reading of sender and attention state.

## A note on stopping the preview server

`pkill -f "vite preview"` was used to stop it and **killed the shell that ran it**, taking the
command after it with it — the exact failure `CLAUDE.local.md` and the repository's own
`quickstart.md` warn about, since the pattern matches the calling shell's own command line too. It
cost nothing here beyond a re-run. Stop a preview by its recorded pid, or by the job control of the
shell that started it.
