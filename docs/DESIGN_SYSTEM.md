# Design System — Hero1 graphical theme standard (PERMANENT)

**Status:** binding for every new or modified Hero1 UI element. Introduced
2026-11-06 with the graphical-theme consistency fix (global instrument
listbox, graphical segmented controls, starred-count badge). Maintained by
whoever touches `apps/web/src/styles/theme.css` or `components/`.

Mission-control slate + signal amber, rendered in TWO tiers:

| Tier | Recipe | Used by |
|---|---|---|
| **Graphical** | linear gradients (`180deg, var(--accent-strong) → var(--accent)`), amber glows (`0 0 14–16px rgba(230,185,61,.35)`), inset top highlight (`inset 0 1px 0 rgba(255,255,255,.5)`), tinted accent-soft panels, drop-shadow glows on brand/interactive glyphs | primary interactive elements: sidebar nav active, brand mark, **segmented control active state**, starred/favorite toggles on, primary CTA buttons |
| **Utility** | flat `var(--bg-2)`/`var(--border)` surfaces, `var(--text)`/`var(--muted)` | structural chrome: panels, headers, dividers, table rows, empty states |

Utility-tier flatness is for chrome only. Anything interactive gets the full
state matrix below — grey/white generic browser styling is a defect.

## Tokens (source of truth: `apps/web/src/styles/theme.css`)

Surfaces: `--bg-0 #07090d` · `--bg-1 #0b0f16` · `--bg-2 #10151d` ·
`--bg-3 #161d27` (hover). Text: `--text #e8e4d8` · `--muted #8b9bb0` ·
`--faint #57657a`. Accent: `--accent #e6b93d` · `--accent-strong #f5cd5e` ·
`--accent-soft rgba(230,185,61,.14)` · `--red #e5484d` · `--green #46a758` ·
`--blue #58a6ff`. Borders: `--border rgba(148,184,220,.12)` ·
`--border-strong rgba(148,184,220,.28)`. Type: `--mono` (labels/values,
uppercase + letterspacing), `--sans` (prose). Radii: `--radius 12px` ·
`--radius-sm 10px` · chips `999px`. Shadows: `--shadow-lg` (popover/panel) ·
inset highlights for depth. **No arbitrary hex values in components** beyond
these families; if you need a value that does not exist, extend `theme.css`
with a named token and document it here.

## Shared components & classes (reuse these — do not fork)

- `.segmented` / `.segmented.mini button` — graphical segmented control
  (Visual Builder/JSON Editor, All Nodes/Starred Only, condition AND/OR,
  exit/risk toggles). Active = amber gradient + glow + dark `#171106` text +
  inset highlight; hover(inactive) = `--bg-3` + `--text`;
  `:focus-visible` = 2px accent outline; `:active` = 1px translateY;
  `:disabled` = 45% opacity. `.mini` = compact size.
- `.scope-count` — amber-soft pill badge for count metadata inside a
  segmented option (starred count). On an ACTIVE segment it flips to a dark
  translucent chip with `#171106` text (contrast on amber).
- `.instrument-select-wrap` / `.instrument-trigger` / `.instrument-panel` /
  `.instrument-option(.active|.selected|.disabled)` / `.instrument-chevron` /
  `.instrument-panel-group` / `.instrument-option-check` — themed custom
  listbox replacing the native `<select>` (component:
  `components/InstrumentSelect.tsx`; behavior contract: state/instrument.tsx
  owns registry, persistence (`hero1.instrument`), XAUUSD default, disabled
  unavailable options; the component owns visuals, keyboard, ARIA).
- `.star-btn` / `.star-btn.on` — star toggle (research metadata only).
- `.scope-line`, `.selection-chip`, `.instrument-warn` — meta rows/badges.
- Focus ring is global (`:focus-visible` outline `--accent`); never remove it.

## Interactive-element state matrix (every control must cover all of these)

| State | Rule |
|---|---|
| default | tokens above; graphical tier if primary-interactive |
| hover | `--bg-3` + `--text` (inactive) or brighter gradient stop (active); always visible |
| focus-visible | 2px `--accent` outline, offset ≥ 1px — never suppressed |
| active/selected | amber gradient family + `#171106` text (graphical) or `--accent-soft` tint + amber border (tinted) |
| disabled | `opacity: .45`, `cursor: not-allowed`, no hover effect, never focusable-selectable |
| selected-inactive | `--accent-soft` bg + `rgba(230,185,61,.35)` border + `--accent-strong` text |

## Permanent rules (carried from the 2026-11-06 mandate)

1. Inspect `theme.css` and existing components first; reuse shared classes.
2. No generic grey/white surfaces, no default browser/OS widgets (native
   `<select>` popups, default checkboxes) in the product UI.
3. No arbitrary hex/shadow/gradient/radius/fonts/spacing outside tokens.
4. Full state matrix on every interactive element; contrast must hold in both
   tier states (dark-on-amber or amber-on-slate — never amber-on-amber).
5. All 8 tabs must look and behave consistently; theme changes are global.
6. If a needed themed component does not exist, CREATE ONE reusable
   component — never a one-off `style={{}}` kludge.
7. This document is updated whenever a shared token/component is added.
8. `prefers-reduced-motion` respected: panel animation is subtle (≤170ms);
   if extended, keep it behind the existing media query pattern.

## A11y notes
- Custom listbox: trigger `button` + `aria-haspopup="listbox"` +
  `aria-expanded` + `aria-controls`; panel `role="listbox"` +
  `aria-activedescendant`; options `role="option"` + `aria-selected` +
  `aria-disabled`; focus stays on the trigger, keyboard drives the active
  option (ArrowUp/Down, Home, End, Enter, Space, Escape, Tab=close);
  outside-mousedown closes. Screen-reader name = `Instrument — current: X`.
- Disabled options are announced and unselectable via click AND keyboard.
