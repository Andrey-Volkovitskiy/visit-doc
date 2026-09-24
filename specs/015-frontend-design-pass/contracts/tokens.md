# Contract: the design theme

The values every component styles from (FR-001), declared once in the single global stylesheet
(FR-003). Under Tailwind v4 the theme *is* CSS custom properties, declared in `@theme`, so this
contract survived the switch from CSS Modules nearly intact — the names and values below are the
same ones the chosen mockup uses.

Values are lifted from `design/a-front-desk.html`, which stays authoritative for appearance.

## How the layers fit

```css
/* src/styles/app.css — the one global stylesheet */
@import "tailwindcss";

@theme {
  /* design A, as Tailwind theme variables -> utilities like bg-surface, text-ink */
}

:root {
  /* shadcn's semantic names, mapped onto the above, so vendored
     components inherit the theme instead of shipping their own look */
}
```

Two layers because they answer different questions. `@theme` is what *this* app's markup uses.
`:root` is what shadcn's vendored components read. Mapping the second onto the first is what stops
the product looking like every other shadcn app — **it is not optional polish**, it is the step that
makes the chosen direction survive the library.

## Colour

| Token | Value | Means |
|---|---|---|
| `--color-page` | `#EEF2F3` | The page behind the panes. Cool grey-green, deliberately not cream. |
| `--color-surface` | `#FFFFFF` | A pane, a card, a composer. |
| `--color-surface-sunken` | `#F7F9F9` | A region set into a surface — a rail, an expanded block. |
| `--color-ink` | `#0F2E33` | Body text. |
| `--color-ink-muted` | `#587177` | Secondary text: labels, explanations, empty states. |
| `--color-accent` | `#0E7C7B` | The interactive colour: active tab, send control, focus ring. |
| `--color-accent-dark` | `#0A5F5E` | Its hover/active state. |
| `--color-accent-wash` | `#E4F0EF` | A tint of it, for a selected row. |
| `--color-bubble-me` | `#DCEEEC` | The reader's own messages — the patient's on one side, staff's on the other. |
| `--color-bubble-them` | `#F4F6F7` | Everyone else's. |
| `--color-attention` | `#B4442E` | **A person is needed. Nothing else may use it** (FR-005). |
| `--color-attention-wash` | `#FBEDEA` | Its background tint, same restriction. |
| `--color-rule` | `#D3DCDC` | A structural border. |
| `--color-rule-soft` | `#E3E9EA` | A divider within a surface. |

### The shadcn mapping

shadcn components reference `--background`, `--foreground`, `--primary`, `--muted`,
`--destructive`, `--border`, `--ring` and friends. Each MUST resolve to a token above — never to a
new colour:

```
--background        -> --color-surface
--foreground        -> --color-ink
--muted-foreground  -> --color-ink-muted
--primary           -> --color-accent
--destructive       -> --color-attention
--border / --input  -> --color-rule
--ring              -> --color-accent
```

A shadcn default left unmapped is a defect: it introduces a colour outside this contract and it is
how a themed app quietly reverts to looking like the library's demo.

**`--color-attention` is a reserved word.** FR-005 makes it mean one thing. An error banner may use
it, because a failed action is a thing needing a person; a decorative accent, a required-field
asterisk or a delete button's resting state may not. Note that shadcn maps `--destructive` onto it,
so a `variant="destructive"` button is making that claim — use the variant only where it is true.

**Colour is never the only carrier** (FR-006). Every state it marks is also marked by text, weight,
shape or position — checked at review, not assumed.

## Type

One family (FR-004): **IBM Plex Sans**, self-hosted `woff2` at weights 400, 500 and 600, under
`src/styles/fonts/`, SIL OFL 1.1, license shipped beside them. Declared as `--font-sans` in
`@theme`, which makes it Tailwind's default sans and so the default for shadcn's components too.

```
--font-sans: "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
```

The fallback chain is required, not decorative: FR-004 says a font that fails to load must degrade
to a named stack rather than to a browser default.

A 1.200 minor third off a 15px base. Tailwind's own `text-sm`/`text-base` scale is 14/16px and is
**overridden**, not worked around:

| Token | Value | Used for |
|---|---|---|
| `--text-xs` | 12px | Counters, the smallest metadata |
| `--text-sm` | 13px | Secondary text, explanations, citations |
| `--text-base` | 15px | Body, messages, controls |
| `--text-md` | 16px | Pane titles |
| `--text-lg` | 19px | Section headings |
| `--text-xl` | 22px | The product name |

Weights: 400 body, 500 controls and emphasis, 600 headings. **No all-caps labels** and no
letter-spaced eyebrows — both are house tells rather than choices.

## Space

Tailwind's default 4px spacing scale already matches the mockup's rhythm, so it is kept rather than
redeclared: `1`=4px, `2`=8px, `3`=12px, `4`=16px, `6`=24px, `8`=32px. A padding or gap off that
scale needs a reason in a comment.

## Shape

| Token | Value | Used for |
|---|---|---|
| `--radius` | 6px | Controls, cards, bubbles, panes |
| (Tailwind `rounded-full`) | — | Only a genuine pill: the switch's track |

shadcn derives its own `--radius-sm/md/lg` from `--radius`; setting the base is enough.

**No drop shadows.** The chosen direction builds structure from hairline rules and background tint.
Several shadcn components ship a shadow by default — dialog, dropdown, popover — and each must have
it removed in the vendored source. This is the second place the library's defaults have to be
overridden deliberately rather than accepted.

## Layout

```
--stack-below: 1100px
```

Where the panes stop sitting side by side (FR-008). Tailwind's default breakpoints have no 1100px
stop, so it is declared as a custom screen in `@theme` (`--breakpoint-panes: 1100px`) and used as
`panes:grid-cols-2`. Declaring it means the number lives in the theme like every other value rather
than as a bare `[1100px]` arbitrary variant scattered through markup.

## Motion

One animation exists: the three-dot working indicator. It must stop under
`prefers-reduced-motion: reduce` while still conveying that a reply is coming (FR-042) — suppressing
the animation must not suppress the state.

`tw-animate-css` (which shadcn v4 uses in place of `tailwindcss-animate`) brings its own
enter/exit animations for dialog and dropdown; those must respect reduced motion too, which is a
thing to verify rather than assume.

## Rules for styling a component

1. Use theme utilities. A hard-coded colour, radius or font size in markup — `bg-[#0E7C7B]`,
   `text-[15px]` — is a defect; the token exists so the value has one home.
2. An arbitrary value (`w-[37ch]`) is allowed for genuine one-offs of *layout*, never for a value
   this contract names.
3. Vendored shadcn components under `src/components/ui/` are ours to edit. Theme them here rather
   than overriding them from call sites with `!important` or long class lists.
4. No shadows, no gradients, no colour outside the tokens above.
