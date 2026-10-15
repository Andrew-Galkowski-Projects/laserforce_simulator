# The REST API wraps every response in one envelope, and API writes validate through the existing Django Forms

ANG-01 hardens `/api/` into the backend the Angular SPA (Phase 8) is built against. Two conventions
set here bind every later API slice (ANG-01b..g) and every Angular service, so they are recorded once.

1. **One envelope for every `/api/` response.** Success is `{"data": <object | list>, "meta": {...}}`
   (`meta` carries `page`, `page_size`, `count`, `total_pages` on paginated lists and is `{}`
   otherwise); failure is `{"errors": [{"code", "message", "field"}], "meta": {}}` with `field` null
   for non-field errors. It is applied by a custom renderer, pagination class and exception handler,
   not per view. The API-03 **Job** endpoints are enveloped too: their `data` stays byte-identical to
   the UI polling JSON, so "the REST and UI polling shapes are identical" now means "`data` equals the
   UI JSON". Cross-Account access remains **404, never 403** (ADR-0038).
2. **API write input binds the same Django `Form` the HTML view binds.** Serializers are output
   representation only; a write view binds the Form to `request.data`/`request.FILES` and maps
   `form.errors` into the envelope. This **overturns the API-03 idiom** ("UI uses a Form, REST uses a
   Serializer") for every new endpoint; API-03's existing `SimulateBatchRequestSerializer` is left in
   place rather than churned.
3. **List filtering uses `django-filter`** with an explicit per-viewset allowlist, plus DRF
   `OrderingFilter` with explicit `ordering_fields`. An invalid filter value is a 400 in the envelope —
   unlike the HTML list pages, which silently fall back to defaults (LG-00c / HX-02).

## Considered options

- **DRF native shapes plus an error normaliser** — less code, but two success shapes (bare object vs
  `{count, next, previous, results}`) for every Angular service to special-case.
- **Write serializers mirroring each Form**, kept honest by parity tests — idiomatic DRF and friendlier
  to the browsable API, but every domain rule (`_PROFILE_BOUNDS`, `GenerateLeagueForm.clean`'s
  cross-field rule, `TeamSlotForm`'s own-players queryset, `RosterImportForm`'s byte cap) would exist
  twice across ~140 routes' worth of later slices. The Forms also outlive ANG-08 (which deletes the
  templates, not the Forms), so they remain the single rule source after the HTML views are gone.
- **Extracting shared validator functions** called by both a Form and a Serializer — cleanest
  long-term, but a refactor of every HTML form inside the slice that was meant to add an API.

## Consequences

- The browsable API's auto-generated write forms are not meaningful for Form-bound endpoints; Angular
  and tests are the consumers.
- Anonymous requests stay **403** (`code: "not_authenticated"`) while `SessionAuthentication` is the
  only scheme; ANG-03's JWT swap is what turns them into 401.
