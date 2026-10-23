# The Angular app follows an lfstats-like layout, themed by one `--lf-*` token contract (palette B dark, palette A light, all JetBrains Mono), with Material kept and themed

**Status:** Accepted (ANG-04b, 2026-10-21). Spec #246, ticket #253, owner pick on #252.

## Context

ANG-06 is about to build the first real Angular screens, and the app had no agreed look. The
Django templates use stock Bootstrap (red `#dc3545`, blue `#0d6efd`, no dark mode, hex colours
inline). The ANG-04 scaffold had only Material's default azure theme. Left alone, every ANG-06
component would pick its own colours and density.

ANG-04b built four layouts (structure and density) and four palettes (colour, typography,
effects) as independent axes in a throwaway route, fed by a fixture captured from a real
simulated round: the Teams list, the Round detail stat table with MVP, and a ~40-event timeline.
The prototype lives on the pushed, never-merged branch
[`prototype/ang-04b-samples`](https://github.com/Andrew-Galkowski-Projects/laserforce_simulator/tree/prototype/ang-04b-samples)
(@7e0efb8). Screenshots of every layout and palette at 1440px and 390px live in
[`pr-assets/ang-04b-visual-style-samples/`](https://github.com/Andrew-Galkowski-Projects/laserforce_simulator/tree/pr-assets/ang-04b-visual-style-samples).
They are named `L<layout>-P<palette>-<screen>-<width>.png`, for example the chosen
[layout B + palette B round](https://github.com/Andrew-Galkowski-Projects/laserforce_simulator/blob/pr-assets/ang-04b-visual-style-samples/Lb-Pb-round-1440.png?raw=true)
and
[layout B + palette A round](https://github.com/Andrew-Galkowski-Projects/laserforce_simulator/blob/pr-assets/ang-04b-visual-style-samples/Lb-Pa-round-1440.png?raw=true).

## Decision

**Layout B (LFstats-like) is the structural guide for ANG-06.** Its component code does not
merge; ANG-06 rebuilds the screens to this structure:

- a top header, plus a collapsible sidebar that becomes a drawer below 768px;
- per-Side **team strips**: a 4px `--side` left border, the team name, a Win / Loss / ELIMINATED
  badge, and the score;
- **score convention:** the round score shows the base score, with the elimination bonus as a
  separate muted `(+N elim bonus)`;
- a flat per-team stat table (Callsign, Pos, Score, MVP, HitDiff, Acc, Medic Hits, Lives, Shots,
  Msls, Msld, **Survived** as a bar), with **sortable columns**, a **sticky header** and a
  **highlighted top-MVP row**;
- the event log as `m:ss` lines with an **event-type glyph** in `--lf-ev-<type>` and
  Side-coloured actor and target names;
- roles as CMD / HVY / SCT / AMO / MED; **R / B Side badges are kept** next to Side colour so
  colour-blind users never depend on hue alone.

**Theme.** Dark mode takes palette B's colours (lfstats' indigo / slate OKLCH), light mode takes
palette A's colours (neutral greys). Both modes use palette B's typography and shape: **all-mono
JetBrains Mono** for body, headings and numbers, radius `0.375rem`, no glow. The mode follows the
OS (`color-scheme: light dark`): light on `:root`, dark under
`@media (prefers-color-scheme: dark)`.

Ported with three fixes (WCAG 2 contrast ratios, computed from the token values):

- light `--lf-ev-elimination` moved from rose `#9f1239` (too close to `--lf-side-red`) to fuchsia
  `#a21caf`;
- the Sides now differ in lightness as well as hue; red is the lighter Side in both modes
  (light text luminance 0.129 red vs 0.049 blue, was 0.084 vs 0.078; dark 0.626 vs 0.407, was
  0.497 vs 0.530). Every Side text token stays at least 5:1 on `--lf-surface` and `--lf-bg`;
- light `--lf-ev-missile`, `-miss`, `-resupply` and `-tag` were darkened to reach 4.5:1 on
  `--lf-bg` (they were 4.16 to 4.61).

ANG-06e added `--lf-ev-locking`, `--lf-ev-base` and `--lf-ev-movement` for the **Event groups**
with no event-type token of their own (Locking, Base capture, Movement on the Event log). They meet
the same 4.5:1 on `--lf-bg` (light: locking `#b4235a` 5.43, base `#467010` 5.04, movement
`#706a62` 4.60). Light base was `#4d7c0f` (4.30) and movement `#8a94a6` (2.63), so both were
darkened; movement is a warm grey so it stays apart from the cool-grey `--lf-ev-miss`.

**The token contract.** `frontend/src/styles/_tokens.scss` defines, and `LF_TOKENS` in
`frontend/src/app/theme/theme-tokens.ts` lists, exactly these custom properties. Components read
colour, fonts, radius and glow from nothing else; no component holds a colour literal.

- surfaces and text: `--lf-bg`, `--lf-surface`, `--lf-surface-alt`, `--lf-text`,
  `--lf-text-muted`, `--lf-border`, `--lf-accent`;
- Sides: `--lf-side-red`, `--lf-side-red-text`, `--lf-side-red-bg`, `--lf-side-red-dim`,
  `--lf-side-blue`, `--lf-side-blue-text`, `--lf-side-blue-bg`, `--lf-side-blue-dim`;
- event types: `--lf-ev-elimination`, `--lf-ev-missile`, `--lf-ev-special`, `--lf-ev-nuke`,
  `--lf-ev-miss`, `--lf-ev-resupply`, `--lf-ev-tag`, and for the Event groups with no
  event-type token (ANG-06e) `--lf-ev-locking`, `--lf-ev-base`, `--lf-ev-movement`;
- fonts and shape (shared by both modes): `--lf-font-body`, `--lf-font-heading`,
  `--lf-font-numeric`, `--lf-radius`, `--lf-glow` (a shadow geometry used with a colour token).

**The `--side` convention.** Every team container carries `data-lf-side="red"` or
`data-lf-side="blue"`. The global rule in `_sides.scss` points `--side`, `--side-text`,
`--side-bg` and `--side-dim` at that Side's tokens. Nothing inside a container picks red or blue
itself.

**Material is kept and themed; the CDK stays.** `mat.theme` (`theme-type: color-scheme`) generates
the full `--mat-sys-*` role set from tonal palettes seeded with indigo `#6366f1` and slate
`#64748b` (`_theme-colors.scss`, from `ng generate @angular/material:theme-color`), so every role
follows the OS mode through `light-dark()`. `mat.theme-overrides` then points every colour role
except error, shadow and scrim at the `--lf-*` tokens: primary, secondary and their containers at
`--lf-accent` (secondary toned towards `--lf-text-muted`), tertiary at `--lf-ev-tag`, the
surfaces, text, outlines and inverse roles at the surface and text tokens, with containers as
`color-mix` tints of a token. Because the tokens switch with the mode, light Material follows
palette A and dark follows palette B; the error roles keep Material's generated red. A Material
component and a hand-rolled one match without local patches. Material typography reads
`--lf-font-body` / `--lf-font-heading`; density stays at Material's default (density is a layout
concern for ANG-06). Data screens (team strips, stat tables, the log) are hand-rolled per layout
B; Material serves dialogs, forms, menus and the like.

**The font is self-hosted** through the `@fontsource/jetbrains-mono` npm package (weights 400,
500, 700, in the `angular.json` `styles` list). There is no Google Fonts link or other font CDN.

**A theme spec guards it.** `frontend/src/app/theme/theme.spec.ts` compiles the global
stylesheet into jsdom and reads computed values on the document root and on `[data-lf-side]`
elements, in light and dark. It asserts that every contract token and the Material roles resolve,
that every Material colour role but error derives from a `--lf-*` token, that the dark block
declares every colour token itself and gives each its own value, and that no `.scss` under
`frontend/src/` other than `_tokens.scss` and the generated `_theme-colors.scss` holds a colour
literal.

## Considered options

- **Layouts A (dense dashboard), C (clean Material), D (esports broadcast) and palettes C (clean
  Material, light), D (arena neon).** The owner compared all 16 layout x palette combinations,
  live in the prototype and through the screenshots, and picked layout B with palette B for dark
  mode and palette A's colours for light mode (#252). The recommendation given was: B is the
  clearest layout for the densest screen (the round stat table) and matches lfstats.com, which
  players already know.
- **A proportional body font with mono numerics only.** Not taken: the owner ruled all-mono
  JetBrains Mono in both modes (#252).
- **Dropping `@angular/material` (CDK only).** Not taken: the owner ruled to keep Material, themed
  with the full `--mat-sys-*` role set, with the CDK kept and data screens hand-rolled (#252).

## Consequences

- ANG-06 builds to this ADR: every screen uses the tokens and `data-lf-side`, never a hex value.
  The theme spec fails the build if a token goes missing or a Side colour is hard-coded.
- Adding a token means adding it to `_tokens.scss` (light and dark when it is a colour) and to
  `LF_TOKENS`, and updating this ADR.
- The prototype branch stays unmerged; the screenshots in the PR are the lasting record.
- Out of scope, left as ANG-06 candidates: the MVP breakdown dialog, the player side sheet,
  per-player expandable timelines, momentum and score charts (and the charting library), replay
  controls and log search, and position icons.
