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
(@7e0efb8). Screenshots of every layout and palette at 1440px and 390px are linked from the PR.

## Decision

**Layout B (LFstats-like) is the structural guide for ANG-06.** Its component code does not
merge; ANG-06 rebuilds the screens to this structure:

- a top header with breadcrumbs, plus a collapsible sidebar that becomes a drawer below 768px;
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

**The token contract.** `frontend/src/styles/_tokens.scss` defines, and `LF_TOKENS` in
`frontend/src/app/theme/theme-tokens.ts` lists, exactly these custom properties. Components read
colour, fonts, radius and glow from nothing else; no component holds a colour literal.

- surfaces and text: `--lf-bg`, `--lf-surface`, `--lf-surface-alt`, `--lf-text`,
  `--lf-text-muted`, `--lf-border`, `--lf-accent`;
- Sides: `--lf-side-red`, `--lf-side-red-text`, `--lf-side-red-bg`, `--lf-side-red-dim`,
  `--lf-side-blue`, `--lf-side-blue-text`, `--lf-side-blue-bg`, `--lf-side-blue-dim`;
- event types: `--lf-ev-elimination`, `--lf-ev-missile`, `--lf-ev-special`, `--lf-ev-nuke`,
  `--lf-ev-miss`, `--lf-ev-resupply`, `--lf-ev-tag`;
- fonts and shape (shared by both modes): `--lf-font-body`, `--lf-font-heading`,
  `--lf-font-numeric`, `--lf-radius`, `--lf-glow` (a shadow geometry used with a colour token).

**The `--side` convention.** Every team container carries `data-lf-side="red"` or
`data-lf-side="blue"`. The global rule in `_sides.scss` points `--side`, `--side-text`,
`--side-bg` and `--side-dim` at that Side's tokens. Nothing inside a container picks red or blue
itself.

**Material is kept and themed; the CDK stays.** `mat.theme` (`theme-type: color-scheme`) generates
the full `--mat-sys-*` role set from tonal palettes seeded with indigo `#6366f1` and slate
`#64748b` (`_theme-colors.scss`, from `ng generate @angular/material:theme-color`), so every role
follows the OS mode through `light-dark()`. `mat.theme-overrides` then points the primary,
background, surface, surface-container, on-surface and outline roles at the `--lf-*` tokens, so a
Material component and a hand-rolled one match without local patches. Material typography reads
`--lf-font-body` / `--lf-font-heading`. Data screens (team strips, stat tables, the log) are
hand-rolled per layout B; Material serves dialogs, forms, menus and the like.

**The font is self-hosted** through the `@fontsource/jetbrains-mono` npm package (weights 400,
500, 700, in the `angular.json` `styles` list). There is no Google Fonts link or other font CDN.

**A theme spec guards it.** `frontend/src/app/theme/theme.spec.ts` renders the real `App` with
the compiled global stylesheet and asserts, in light and dark, that every contract token and the
key Material roles resolve, that dark gives every colour token its own value, and that no
stylesheet outside the token block hard-codes a Side colour.

## Considered options

- **Layout A (dense dashboard), C (clean Material), D (esports broadcast).** Rejected: A's team
  sidebar and C's stock Material tables are less readable for the 12-column stat table than B's
  flat sortable tables; D's scoreboard and ticker suit a live broadcast, not browsing results.
  Layout B also matches lfstats.com, which players already know.
- **Palette C (clean Material, light) and D (arena neon, dark with glow).** Rejected: C reads as a
  generic admin app; D's glow hurts dense tables. B (dark) and A (light) were the most readable
  pair.
- **A proportional body font with mono numerics only.** Rejected by the owner: all-mono keeps
  columns aligned everywhere and matches lfstats.
- **Dropping `@angular/material` (CDK only).** Rejected: dialogs, forms and menus would have to be
  rebuilt by hand. Theming it from the contract costs one partial.

## Consequences

- ANG-06 builds to this ADR: every screen uses the tokens and `data-lf-side`, never a hex value.
  The theme spec fails the build if a token goes missing or a Side colour is hard-coded.
- Adding a token means adding it to `_tokens.scss` (light and dark when it is a colour) and to
  `LF_TOKENS`, and updating this ADR.
- The prototype branch stays unmerged; the screenshots in the PR are the lasting record.
- Out of scope, left as ANG-06 candidates: the MVP breakdown dialog, the player side sheet,
  per-player expandable timelines, momentum and score charts (and the charting library), replay
  controls and log search, and position icons.
