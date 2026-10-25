# The Angular SPA draws its charts with Chart.js 4, used directly and pinned

**Status:** Accepted (ANG-06g grill, 2026-10-25).

The four event charts (shots, lives, points and **SP** over time) are the SPA's first charts, and
the library chosen for them will be reused for the momentum / score charts that ADR-0043 left for
ANG-06. We use **Chart.js 4**, pinned to an exact version (4.5.1), and we call it directly from a small
wrapper component that registers only the controllers, elements, scales and plugins it uses. We do
not use the `ng2-charts` wrapper. The Django events page already draws these charts with Chart.js.
Its stepped per-player lines, the **Chart overlay** plugin (RES-02) and the SP cap line therefore
port one for one, so parity can be checked line by line while both pages are live until ANG-08.

## Considered Options

- **`ng2-charts`**: adds a dependency that has to track each Angular major (the SPA is on Angular 22).
  A wrapper of about 40 lines over `new Chart(canvas, config)` costs less than that.
- **ECharts (`ngx-echarts`)**: has built-in `markLine` overlays and richer interaction, but it is
  about three times the bundle size and every chart would be rewritten instead of ported.
- **uPlot**: very small and fast, but filters, overlays, tooltips and the cap line would all be
  hand-built.

## Consequences

Chart colours and the font cannot come from CSS, because canvas ignores it. The charts page reads
the `--lf-*` tokens with `getComputedStyle`, re-reads them when `prefers-color-scheme` flips, and
hands the wrapper concrete values; no hex goes into a component (ADR-0043).
jsdom has no canvas, so specs check the chart config and the data built for it, not pixels. The browser check covers the drawing.
