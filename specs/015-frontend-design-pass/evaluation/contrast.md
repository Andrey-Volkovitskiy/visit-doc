# T062 — contrast, measured

FR-041 and SC-003. Computed from the token values in `services/frontend/src/styles/app.css`
using the WCAG 2.1 relative-luminance formula, over **every** foreground/background pair the
palette can form — not only the pairs that appear on screen today, so a later call site that puts
one on the other is already answered.

Run: 2026-09-23. Method: sRGB linearisation, `(L_lighter + 0.05) / (L_darker + 0.05)`.

## The two pairs `quickstart.md` names as the closest

| Pair | Ratio | AA normal (4.5) |
|---|---|---|
| `--color-ink-muted` on `--color-surface-sunken` | **4.91** | PASS |
| `--color-attention` on `--color-attention-wash` | **4.83** | PASS |

Both pass. They were the right two to worry about and neither is the problem.

## What the full sweep found instead

Six pairs miss AA for normal text. All six pass AA for large text (3.0), so none is a failure of
the palette as such — each is a combination that must simply not be used for body text:

| Text | Background | Ratio |
|---|---|---|
| `--color-ink-muted` | `--color-accent-wash` | 4.45 |
| `--color-ink-muted` | `--color-bubble-me` | 4.32 |
| `--color-accent` | `--color-page` | 4.45 |
| `--color-accent` | `--color-accent-wash` | 4.30 |
| `--color-accent` | `--color-bubble-me` | 4.18 |
| `--color-accent` | `--color-attention-wash` | 4.39 |

**Two of them were live, and both were fixed by changing the text colour rather than the token.**
The token values are the mockup's and stay as they are; what changed is which of them may sit on
which.

1. **The sender label on a bubble.** `MessageView`'s `role-label` was `text-ink-muted`. That was
   safe only while it sat on `--color-bubble-them` (4.79) — and FR-023's fix, which puts a staff
   member's own messages on `--color-bubble-me` in the console, moved it onto the 4.32 pair. It is
   now `text-ink` (11.98 on `bubble-me`, 13.28 on `bubble-them`).
2. **The active tab and the link button.** Both were `text-accent`. On the tab strip's
   `--color-bubble-them` that measures 4.63 and passes, but neither call site controls its own
   background and a move onto `--color-page` would silently drop to 4.45. Both are now
   `text-accent-dark`, which is what `design/a-front-desk.html` uses for a selected tab anyway
   (`.console__tab[aria-selected="true"]`), and which measures **6.22 or better on every background
   in the palette**.

The remaining four combinations do not occur: `grep` for `text-accent` (excluding
`text-accent-dark`) now returns nothing, and no `text-ink-muted` sits inside an `accent-wash` or
`bubble-me` region.

## Everything that does occur

| Text | Background | Ratio | Verdict |
|---|---|---|---|
| `ink` | `surface` / `page` / `surface-sunken` / `bubble-me` / `bubble-them` | 11.98 – 14.39 | PASS |
| `ink-muted` | `surface` | 5.19 | PASS |
| `ink-muted` | `surface-sunken` | 4.91 | PASS |
| `ink-muted` | `bubble-them` | 4.79 | PASS |
| `ink-muted` | `page` | 4.61 | PASS |
| `accent-dark` | every palette background | 6.22 – 7.47 | PASS |
| `attention` | every palette background | 4.59 – 5.52 | PASS |
| `surface` | `accent` | 5.01 | PASS |
| `surface` | `attention` | 5.52 | PASS |

## Non-text contrast

`--color-rule` (1.40 on `surface`) and `--color-rule-soft` (1.23) are well under the 3.0 that
WCAG 1.4.11 asks of a control boundary. This is **accepted, not overlooked**: they are decorative
separators — a pane edge, a divider between rows — and no control's boundary is the only thing
identifying it. Every interactive control carries a label, a role and a focus ring; the focus ring
is `--color-accent`, which measures 4.45–5.01 against the backgrounds it appears on and clears 3.0
comfortably. A rule raised to 3.0 would read as a heavy border and lose the hairline structure the
direction is built from.

## What this does not cover

Contrast of text over the **dialog overlay** (`bg-ink/40` over arbitrary page content) is not
computed here: the value depends on what is behind it, and the dialog's own content sits on
`--color-surface`, which is measured above. The overlay dims; nothing is read through it.
