import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { compile } from 'sass';
import { environment } from '../../../environments/environment';
import { PlayerRef } from '../../api/analytics.models';
import { LeagueContext } from '../stats-shared/league-context';
import { watchResult } from '../testing/league-players-fixtures';
import { failure } from '../testing/league-stats-fixtures';
import { WatchFlag } from './watch-flag';
import { WatchState } from './watch-state';

const BASE = environment.apiBaseUrl;
const ACE: PlayerRef = { id: 25, name: 'Ace', team: null };
const BOLT: PlayerRef = { id: 26, name: 'Bolt', team: null };

/** Two flags for Ace (as on two tables of one page) and one for Bolt. */
@Component({
  imports: [WatchFlag],
  template: `
    <app-watch-flag id="ace-1" [player]="ace" [watched]="aceSeed()" />
    <app-watch-flag id="ace-2" [player]="ace" [watched]="aceSeed()" />
    <app-watch-flag id="bolt" [player]="bolt" [watched]="true" />
  `,
})
class Host {
  readonly ace = ACE;
  readonly bolt = BOLT;
  readonly aceSeed = signal(false);
}

describe('app-watch-flag (spec #428 F4)', () => {
  let http: HttpTestingController;
  let el: HTMLElement;
  let fixture: ReturnType<typeof TestBed.createComponent<Host>>;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), LeagueContext, WatchState],
    });
    TestBed.inject(LeagueContext).leagueId.set(4);
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    el = fixture.nativeElement as HTMLElement;
  });

  afterEach(() => http.verify());

  const flag = (id: string): HTMLButtonElement =>
    el.querySelector<HTMLButtonElement>(`#${id} button`)!;
  const watchRequest = (method: string, playerId = 25): TestRequest => {
    const req = http.expectOne(`${BASE}/leagues/4/watch-list/${playerId}/`);
    expect(req.request.method).toBe(method);
    return req;
  };

  async function click(id: string): Promise<void> {
    flag(id).click();
    await fixture.whenStable();
  }

  it('is a ★ button that says what it does, pressed only when watched', () => {
    const off = flag('ace-1');
    expect(off.type).toBe('button');
    expect(off.textContent?.trim()).toBe('★');
    expect(off.getAttribute('aria-pressed')).toBe('false');
    expect(off.getAttribute('aria-label')).toBe('Watch Ace');
    expect(off.title).toBe('Watch Ace');
    const on = flag('bolt');
    expect(on.getAttribute('aria-pressed')).toBe('true');
    expect(on.getAttribute('aria-label')).toBe('Stop watching Bolt');
  });

  it('is disabled while its request is in flight', async () => {
    await click('ace-1');
    expect(flag('ace-1').disabled).toBe(true);
    expect(flag('bolt').disabled).toBe(false);
    watchRequest('PUT').flush({ data: watchResult(true), meta: {} });
    await fixture.whenStable();
    expect(flag('ace-1').disabled).toBe(false);
  });

  it('re-colours every flag for the Player once the server saves it', async () => {
    await click('ace-1');
    watchRequest('PUT').flush({ data: watchResult(true), meta: {} });
    await fixture.whenStable();
    for (const id of ['ace-1', 'ace-2']) {
      expect(flag(id).getAttribute('aria-pressed')).toBe('true');
      expect(flag(id).getAttribute('aria-label')).toBe('Stop watching Ace');
      expect(flag(id).classList).toContain('watch-flag--on');
    }
    await click('ace-2');
    watchRequest('DELETE').flush({ data: watchResult(false), meta: {} });
    await fixture.whenStable();
    expect(flag('ace-1').getAttribute('aria-pressed')).toBe('false');
    expect(flag('bolt').getAttribute('aria-pressed')).toBe('true');
  });

  it('stays as it was when the request fails', async () => {
    await click('bolt');
    watchRequest('DELETE', 26).flush(...failure(500, 'Database is down.'));
    await fixture.whenStable();
    expect(flag('bolt').getAttribute('aria-pressed')).toBe('true');
    expect(flag('bolt').disabled).toBe(false);
  });

  it('is coloured by tokens only: muted when off, the accent when on', () => {
    // The spec runs from `frontend/` (`npm run test:ci`) or from the repo root.
    const scss = 'app/leagues/watch/watch-flag.scss';
    const file = [
      join(process.cwd(), 'src', scss),
      join(process.cwd(), 'frontend', 'src', scss),
    ].find((path) => existsSync(path)) as string;
    const css = compile(file).css;
    expect(/\.watch-flag\s*\{[^}]*color: var\(--lf-text-muted\)/.exec(css)).not.toBeNull();
    expect(/\.watch-flag--on\s*\{[^}]*color: var\(--lf-accent\)/.exec(css)).not.toBeNull();
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b/i);
  });
});
