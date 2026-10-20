# Frontend

The Angular single-page app for Laserforce Simulator (Phase 8, replacing the Django templates).

Run every command from the repo root; the Angular CLI is a local dev dependency, so no global
install is needed:

```bash
npm --prefix frontend ci                 # install from package-lock.json
npm --prefix frontend run start          # dev server on http://localhost:4200
npm --prefix frontend run test:ci        # Vitest, one headless run
npm --prefix frontend run lint           # ESLint
npm --prefix frontend run format:check   # Prettier check
npm --prefix frontend run build          # production build to dist/frontend/browser/
```

See [`CLAUDE.md`](CLAUDE.md) for the stack, layout and conventions, and
[ADR-0042](../docs/adr/0042-angular-frontend-stack.md) for why the stack is what it is.
