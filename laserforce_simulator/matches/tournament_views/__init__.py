"""LG-02a / ANG-01f-1 — sandbox Tournament HTML views, one module per area.

- ``list_create`` — the Tournament index and the create form.
- ``detail`` — the detail page (bracket tree, seeding, pool, play controls).
- ``setup`` — reseed, lock and participant CSV import.
- ``draw`` — the Random-Draw player pool, the draw and its hand-edit.
- ``play`` — play-next, the async Play All job and its polling status.

No re-exports: ``matches.tournament_urls`` imports each submodule. The bracket
STRUCTURE lives in the pure ``matches.bracket`` module and the detail builders
in ``matches.tournament_services.detail``.
"""
