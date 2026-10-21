// ANG-04b prototype contract: any layout must work under any palette.
// 1. Every palette class resolves every contract token (and the key --mat-sys-* roles).
// 2. No layout stylesheet contains a colour literal: layouts read colour only from tokens.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { routes } from '../../app.routes';
import { LF_TOKENS, MAT_SYS_TOKENS, PALETTE_KEYS } from './palette-contract';

const SAMPLES_DIR = join(process.cwd(), 'src', 'app', 'prototype', 'style-samples');

function scssFilesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return scssFilesUnder(path);
    return path.endsWith('.scss') ? [path] : [];
  });
}

/** Every stylesheet a layout owns: the four layout folders plus the shared placeholder. */
function layoutStylesheets(): string[] {
  return readdirSync(SAMPLES_DIR)
    .filter((name) => /^layout-[a-d]$/.test(name) || name === 'placeholder')
    .flatMap((name) => scssFilesUnder(join(SAMPLES_DIR, name)));
}

// Hex, or a colour function. `color-mix(... var(--side) ...)` stays allowed: it derives from tokens.
const COLOUR_LITERAL = /#[0-9a-f]{3,8}\b|\b(?:rgba?|hsla?|oklch|oklab|lab|lch|hwb|color)\(/gi;

function stripComments(scss: string): string {
  return scss.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
}

describe('style-samples contract', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter(routes)] });
  });

  for (const palette of PALETTE_KEYS) {
    it(`palette ${palette} resolves every contract token`, async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(`/prototype/style-samples?palette=${palette}`);
      const root = harness.routeNativeElement?.querySelector<HTMLElement>(`.lf-palette-${palette}`);
      expect(root, `.lf-palette-${palette} root element`).toBeTruthy();
      const style = getComputedStyle(root as HTMLElement);
      const missing = [...LF_TOKENS, ...MAT_SYS_TOKENS].filter(
        (token) => style.getPropertyValue(token).trim() === '',
      );
      expect(missing).toEqual([]);
    });
  }

  it('finds the layout stylesheets to check', () => {
    expect(layoutStylesheets().length).toBeGreaterThanOrEqual(5);
  });

  it('no layout stylesheet contains a colour literal', () => {
    const offenders = layoutStylesheets().flatMap((file) => {
      const hits = stripComments(readFileSync(file, 'utf-8')).match(COLOUR_LITERAL) ?? [];
      return hits.map((hit) => `${relative(SAMPLES_DIR, file)}: ${hit}`);
    });
    expect(offenders).toEqual([]);
  });
});
