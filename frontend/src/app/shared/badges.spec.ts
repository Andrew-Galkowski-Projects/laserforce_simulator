import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { compileString } from 'sass';

// The spec runs from `frontend/` (`npm run test:ci`) or from the repo root
// (`npm --prefix frontend exec -- ng test`), as theme.spec.ts allows for.
const SHARED = [
  join(process.cwd(), 'src', 'app', 'shared'),
  join(process.cwd(), 'frontend', 'src', 'app', 'shared'),
].find((path) => existsSync(join(path, '_badges.scss'))) as string;

/** The declarations of `selector`'s rule in `css`, or null when it has none. */
function rule(css: string, selector: string): string | null {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return css.match(new RegExp(`(?:^|\\n)${escaped}\\s*\\{([^}]*)\\}`))?.[1] ?? null;
}

describe('badges.feat (spec #417)', () => {
  const css = compileString('@use "badges"; .feat { @include badges.feat; }', {
    loadPaths: [SHARED],
  }).css;

  it('styles a threshold feat badge with tokens', () => {
    const base = rule(css, '.feat');
    expect(base).toContain('border: 1px solid var(--lf-accent)');
    expect(base).toContain('background: color-mix(in srgb, var(--lf-accent) 12%, transparent)');
  });

  it('sets a [data-season-best] badge apart: filled and bold', () => {
    const best = rule(css, '.feat[data-season-best]');
    expect(best).toContain('background: var(--lf-accent)');
    expect(best).toContain('font-weight: 700');
  });
});
