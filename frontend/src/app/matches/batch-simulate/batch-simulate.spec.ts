import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { HarnessLoader } from '@angular/cdk/testing';
import { TestbedHarnessEnvironment } from '@angular/cdk/testing/testbed';
import { MatFormFieldHarness } from '@angular/material/form-field/testing';
import { MatSelectHarness } from '@angular/material/select/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { ArenaMap } from '../../api/maps.models';
import { TeamListItem } from '../../api/teams.models';
import { signIn } from '../../api/testing/auth-fixtures';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { Chart } from 'chart.js';
import { BatchRunStatus, SaveGamesStatus } from '../../api/batch.models';
import { aggregate, runStatus, STARTED } from '../testing/batch-fixtures';
import { stubCanvasContext } from '../testing/fake-canvas';
import { pageEnvelope } from '../testing/match-fixtures';
import { POLL_TIMERS } from '../../shared/testing/poll-timers';

const BASE = environment.apiBaseUrl;

function team(id: number, name: string, valid = true): TeamListItem {
  return {
    id,
    name,
    created_date: '2026-10-22T12:00:00Z',
    wins: 0,
    losses: 0,
    slot_commander: null,
    slot_heavy: null,
    slot_scout_1: null,
    slot_scout_2: null,
    slot_medic: null,
    slot_ammo: null,
    is_valid_roster: valid,
    roster_errors: valid ? [] : ['Missing Commander'],
    players: [],
  };
}

function arenaMap(id: number, name: string): ArenaMap {
  return {
    id,
    name,
    image: null,
    is_default: false,
    img_width: 800,
    img_height: 600,
    created_at: '2026-10-22T12:00:00Z',
    has_confirmed_config: true,
    confirmed_zone_size: 50,
  };
}

const TEAMS = [team(1, 'Alpha'), team(2, 'Bravo'), team(5, 'Charlie', false)];
const MAPS = [arenaMap(11, 'Classic Arena'), arenaMap(12, 'Warehouse')];

describe('Batch Simulate (/matches/batch)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let loader: HarnessLoader;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    await signIn();
    harness = await RouterTestingHarness.create();
    loader = TestbedHarnessEnvironment.documentRootLoader(harness.fixture);
  });

  afterEach(() => http.verify());

  const page = (): HTMLElement => {
    const host = harness.routeNativeElement?.querySelector<HTMLElement>('app-batch-simulate');
    if (!host) throw new Error('not on Batch Simulate');
    return host;
  };
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const field = (label: string): Promise<MatFormFieldHarness> =>
    loader.getHarness(MatFormFieldHarness.with({ floatingLabelText: label }));
  const stable = (): Promise<void> => harness.fixture.whenStable();

  async function select(label: string): Promise<MatSelectHarness> {
    const control = await (await field(label)).getControl(MatSelectHarness);
    if (!control) throw new Error(`no ${label} select`);
    return control;
  }

  async function optionsOf(label: string): Promise<{ text: string; disabled: boolean }[]> {
    const control = await select(label);
    await control.open();
    const options = await Promise.all(
      (await control.getOptions()).map(async (o) => ({
        text: await o.getText(),
        disabled: await o.isDisabled(),
      })),
    );
    await control.close();
    return options;
  }

  function button(label: string): HTMLButtonElement {
    const found = [...page().querySelectorAll('button')].find((b) => text(b) === label);
    if (!found) throw new Error(`no ${label} button`);
    return found;
  }

  /** Opens the screen and answers both picker GETs. */
  async function open(teams: TeamListItem[] = TEAMS, maps: ArenaMap[] = MAPS): Promise<void> {
    await harness.navigateByUrl('/matches/batch');
    http.expectOne((req) => req.url === `${BASE}/teams/`).flush(pageEnvelope(teams));
    http.expectOne((req) => req.url === `${BASE}/maps/`).flush(pageEnvelope(maps));
    await stable();
  }

  async function pickTeams(red = 'Alpha', blue = 'Bravo'): Promise<void> {
    await (await select('Red Team')).clickOptions({ text: red });
    await (await select('Blue Team')).clickOptions({ text: blue });
  }

  describe('the form', () => {
    it('loads the Teams by name and the confirmed-config maps by name', async () => {
      await harness.navigateByUrl('/matches/batch');
      expect(TestBed.inject(Router).url).toBe('/matches/batch');
      const teams = http.expectOne((req) => req.url === `${BASE}/teams/`);
      expect(teams.request.params.get('ordering')).toBe('name');
      const maps = http.expectOne((req) => req.url === `${BASE}/maps/`);
      expect(maps.request.params.get('has_confirmed_config')).toBe('true');
      expect(maps.request.params.get('ordering')).toBe('name');
      teams.flush(pageEnvelope(TEAMS));
      maps.flush(pageEnvelope(MAPS));
      await stable();
      expect(text(page().querySelector('h1'))).toBe('Batch Simulate');
      expect(text(page())).toContain(
        'Run 10–500 rounds in memory and watch the win-probability distribution build up live. ' +
          'No match records are saved.',
      );
    });

    it('offers every Team on both sides, invalid rosters disabled and marked', async () => {
      await open();
      for (const label of ['Red Team', 'Blue Team']) {
        expect(await optionsOf(label)).toEqual([
          { text: 'Alpha', disabled: false },
          { text: 'Bravo', disabled: false },
          { text: 'Charlie (invalid roster)', disabled: true },
        ]);
      }
    });

    it('shows a link to Teams instead of the form when no Team has a valid roster', async () => {
      await open([team(5, 'Charlie', false)]);
      expect(page().querySelector('form')).toBeNull();
      const link = [...page().querySelectorAll('a')].find((a) => text(a) === 'Go to Teams');
      expect(link?.getAttribute('href')).toBe('/teams');
    });

    it('offers the maps after "No map (3-zone fallback)", the default', async () => {
      await open();
      expect(await (await select('Arena Map')).getValueText()).toBe('No map (3-zone fallback)');
      expect((await optionsOf('Arena Map')).map((o) => o.text)).toEqual([
        'No map (3-zone fallback)',
        'Classic Arena',
        'Warehouse',
      ]);
    });

    it('offers 10, 50, 100 or 500 simulations, 100 by default', async () => {
      await open();
      expect(await (await select('Number of simulations')).getValueText()).toBe('100');
      expect((await optionsOf('Number of simulations')).map((o) => o.text)).toEqual([
        '10',
        '50',
        '100',
        '500',
      ]);
    });

    it('flags both empty Team pickers and sends nothing', async () => {
      await open();
      button('Run').click();
      await stable();
      for (const label of ['Red Team', 'Blue Team']) {
        expect(await (await field(label)).getTextErrors()).toEqual(['This field is required.']);
      }
      http.expectNone(`${BASE}/simulate-batch/`);
    });

    it('starts a Batch run with the picked Teams, map and count', async () => {
      await open();
      await pickTeams('Bravo', 'Alpha');
      await (await select('Arena Map')).clickOptions({ text: 'Warehouse' });
      await (await select('Number of simulations')).clickOptions({ text: '500' });
      button('Run').click();
      await stable();
      const req = http.expectOne(`${BASE}/simulate-batch/`);
      expect(req.request.method).toBe('POST');
      expect(req.request.body).toEqual({ team_red: 2, team_blue: 1, arena_map: 12, n: 500 });
      req.flush({ data: { ...STARTED, n: 500 }, meta: {} });
      await stable();
      http.expectOne(`${BASE}/simulate-batch/job-1/`);
    });

    it('shows a refused run above the form and stays ready to run again', async () => {
      await open();
      await pickTeams('Alpha', 'Alpha');
      button('Run').click();
      await stable();
      http.expectOne(`${BASE}/simulate-batch/`).flush(
        {
          errors: [{ code: 'invalid', message: 'A team cannot play against itself!', field: null }],
          meta: {},
        },
        { status: 400, statusText: 'Bad Request' },
      );
      await stable();
      expect(text(page().querySelector('[role="alert"]'))).toBe(
        'A team cannot play against itself!',
      );
      expect(button('Run').disabled).toBe(false);
    });

    it('shows a field error on its own control', async () => {
      await open();
      await pickTeams();
      button('Run').click();
      await stable();
      http.expectOne(`${BASE}/simulate-batch/`).flush(
        {
          errors: [
            {
              code: 'max_value',
              message: 'Ensure this value is less than or equal to 500.',
              field: 'n',
            },
          ],
          meta: {},
        },
        { status: 400, statusText: 'Bad Request' },
      );
      await stable();
      expect(await (await field('Number of simulations')).getTextErrors()).toEqual([
        'Ensure this value is less than or equal to 500.',
      ]);
    });
  });

  describe('a run', () => {
    let restoreCanvas: () => void;

    beforeEach(() => {
      restoreCanvas = stubCanvasContext();
    });

    afterEach(() => {
      vi.useRealTimers();
      restoreCanvas();
    });

    const statusUrl = `${BASE}/simulate-batch/job-1/`;
    const label = (): string => text(page().querySelector('.progress__label'));
    const results = (): HTMLElement | null => page().querySelector('.results');
    /** The text of each element in `selector`, in order. */
    const texts = (selector: string): string[] =>
      [...page().querySelectorAll(selector)].map((el) => text(el));
    /** A card's lines: its badge (if any), value, caption and record. */
    const card = (name: string): string[] => texts(`.card--${name} > span`);

    /** Opens the screen, presses Run with Alpha v Bravo, and answers the start (Batch run 10). */
    async function startRun(): Promise<void> {
      await open();
      vi.useFakeTimers({ toFake: POLL_TIMERS });
      await pickTeams();
      button('Run').click();
      await stable();
      http.expectOne(`${BASE}/simulate-batch/`).flush({ data: STARTED, meta: {} });
      await stable();
    }

    async function answer(status: BatchRunStatus): Promise<void> {
      http.expectOne(statusUrl).flush({ data: status, meta: {} });
      await stable();
    }

    /** Waits out the poll cadence. */
    async function wait(ms: number): Promise<void> {
      vi.advanceTimersByTime(ms);
      await stable();
    }

    it('reads "Starting…" until the first poll answers, then counts the games', async () => {
      await startRun();
      expect(label()).toBe('Starting…');
      await answer(runStatus({ completed: 0, total: 10 }));
      expect(label()).toBe('Simulating game 0 of 10…');
      expect(results()).toBeNull();
    });

    it("counts against the run's size while the job is still pending (total 0)", async () => {
      await startRun();
      await answer(runStatus({ completed: 0, total: 0 }));
      expect(label()).toBe('Simulating game 0 of 10…');
    });

    it('polls again 500 ms after each answer, painting the partial results live', async () => {
      await startRun();
      await answer(runStatus({ completed: 0 }));
      await wait(499);
      http.expectNone(statusUrl);
      await wait(1);
      await answer(runStatus({ completed: 4, partial: aggregate({ n: 4 }) }));
      expect(label()).toBe('Simulating game 4 of 10…');
      expect(text(results()?.querySelector('h2'))).toBe('Results — 4 simulations');
      await wait(500);
      http.expectOne(statusUrl);
    });

    it("shows each Team's win rate and record, and the ties", async () => {
      await startRun();
      await answer(runStatus({ completed: 10, partial: aggregate() }));
      expect(card('red')).toEqual(['R', '70.0%', 'Alpha win rate', '7 W / 1 T / 2 L']);
      expect(card('ties')).toEqual(['1', 'Ties']);
      expect(card('blue')).toEqual(['B', '20.0%', 'Bravo win rate', '2 W / 1 T / 7 L']);
      expect(page().querySelector('.card--red')?.getAttribute('data-lf-side')).toBe('red');
      expect(page().querySelector('.card--blue')?.getAttribute('data-lf-side')).toBe('blue');
    });

    it("shows each Team's average score and survivors", async () => {
      await startRun();
      await answer(runStatus({ completed: 10, partial: aggregate() }));
      expect(texts('.averages caption')).toEqual(['R Alpha averages', 'B Bravo averages']);
      expect(texts('.averages[data-lf-side="red"] tr > *')).toEqual([
        'Score',
        '31,187',
        'Survivors',
        '3.40',
      ]);
      expect(texts('.averages[data-lf-side="blue"] tr > *')).toEqual([
        'Score',
        '27,307',
        'Survivors',
        '1.90',
      ]);
    });

    it('shows the map-side advantage', async () => {
      await startRun();
      await answer(runStatus({ completed: 10, partial: aggregate() }));
      const table = page().querySelector('.side-advantage');
      expect(text(table?.querySelector('caption'))).toBe(
        'Map-side advantage (sides alternated evenly)',
      );
      expect(texts('.side-advantage tbody tr > *')).toEqual([
        'Red side',
        '60.0% (6 W)',
        '30,500',
        'Blue side',
        '30.0% (3 W)',
        '28,001',
      ]);
      expect(text(page().querySelector('.side-advantage__note'))).toBe(
        '1 side ties. Raw physical-side signal — the per-team results above are side-neutral.',
      );
    });

    it('hides the map-side advantage for an aggregate of no games', async () => {
      await startRun();
      await answer(runStatus({ partial: aggregate({ n: 0 }) }));
      expect(results()).not.toBeNull();
      expect(page().querySelector('.side-advantage')).toBeNull();
    });

    it('draws the score distribution of both Teams', async () => {
      await startRun();
      await answer(runStatus({ completed: 10, partial: aggregate() }));
      expect(text(results()?.querySelector('.distribution h3'))).toBe('Score distribution');
      const chart = Chart.getChart(
        results()?.querySelector('app-score-histogram canvas') as HTMLCanvasElement,
      );
      expect(chart?.data.labels).toEqual([
        '0–5,000',
        '5,000–10,000',
        '10,000–15,000',
        '15,000–20,000',
        '20,000–25,000',
        '25,000–30,000',
        '30,000–35,000',
      ]);
      expect(chart?.data.datasets.map((d) => [d.label, d.data])).toEqual([
        ['Alpha', [1, 0, 0, 0, 0, 0, 1]],
        ['Bravo', [0, 0, 1, 0, 1, 0, 0]],
      ]);
    });

    it('keeps Run disabled while the batch runs', async () => {
      await startRun();
      expect(button('Run').disabled).toBe(true);
      await answer(runStatus({ completed: 3 }));
      expect(button('Run').disabled).toBe(true);
    });

    it('finishes on "complete": full progress, the time taken, Run enabled, no more polls', async () => {
      await startRun();
      await answer(runStatus({ status: 'complete', completed: 10, partial: aggregate() }));
      expect(label()).toMatch(/^Complete in \d+\.\ds$/);
      expect(page().querySelector('mat-progress-bar')?.getAttribute('aria-valuenow')).toBe('100');
      expect(button('Run').disabled).toBe(false);
      await wait(1000);
      http.expectNone(statusUrl);
    });

    it('stops on a failed job with its error, hides the progress and enables Run', async () => {
      await startRun();
      await answer(runStatus({ status: 'error', error: 'Team 9 does not exist.' }));
      expect(text(page().querySelector('.run-error'))).toBe('Team 9 does not exist.');
      expect(page().querySelector('.progress')).toBeNull();
      expect(button('Run').disabled).toBe(false);
      await wait(1000);
      http.expectNone(statusUrl);
    });

    it('reads "Unknown error." for a failed job with no message', async () => {
      await startRun();
      await answer(runStatus({ status: 'error', error: null }));
      expect(text(page().querySelector('.run-error'))).toBe('Unknown error.');
    });

    it('stops when a poll cannot reach the server', async () => {
      await startRun();
      http.expectOne(statusUrl).error(new ProgressEvent('error'), { status: 0 });
      await stable();
      expect(text(page().querySelector('.run-error'))).toBe('Could not reach the server.');
      expect(button('Run').disabled).toBe(false);
      await wait(1000);
      http.expectNone(statusUrl);
    });

    it('clears the last run when Run is pressed again', async () => {
      await startRun();
      await answer(runStatus({ status: 'complete', completed: 10, partial: aggregate() }));
      expect(results()).not.toBeNull();
      button('Run').click();
      await stable();
      expect(results()).toBeNull();
      expect(label()).toBe('Starting…');
      http.expectOne(`${BASE}/simulate-batch/`).flush({
        data: { ...STARTED, job_id: 'job-2', team_red_name: 'Delta' },
        meta: {},
      });
      await stable();
      http
        .expectOne(`${BASE}/simulate-batch/job-2/`)
        .flush({ data: runStatus({ completed: 1, partial: aggregate() }), meta: {} });
      await stable();
      expect(card('red')).toContain('Delta win rate');
      expect(label()).toBe('Simulating game 1 of 10…');
    });

    it('clears a failed run’s error when Run is pressed again', async () => {
      await startRun();
      await answer(runStatus({ status: 'error', error: 'Boom.' }));
      button('Run').click();
      await stable();
      expect(page().querySelector('.run-error')).toBeNull();
      http.expectOne(`${BASE}/simulate-batch/`);
    });

    it('stops polling when the screen is left', async () => {
      await startRun();
      await answer(runStatus({ completed: 2 }));
      harness.fixture.destroy();
      vi.advanceTimersByTime(1000);
      http.expectNone(statusUrl);
    });

    describe('Save games', () => {
      const saveUrl = `${BASE}/simulate-batch/job-1/save/`;
      const saveStatusUrl = `${BASE}/simulate-batch/save/save-1/`;
      const section = (): HTMLElement | null => page().querySelector('.save-games');
      const saveStatus = (): string => text(page().querySelector('.save-status'));

      async function complete(agg = aggregate()): Promise<void> {
        await startRun();
        await answer(runStatus({ status: 'complete', completed: 10, partial: agg }));
      }

      async function answerSave(status: SaveGamesStatus): Promise<void> {
        http.expectOne(saveStatusUrl).flush({ data: status, meta: {} });
        await stable();
      }

      it('stays hidden while the batch runs', async () => {
        await startRun();
        await answer(runStatus({ completed: 4, partial: aggregate() }));
        expect(section()).toBeNull();
      });

      it('stays hidden after a run with no Average or Highlight games', async () => {
        await complete(aggregate({ avg_seeds: [], outlier_seeds: [] }));
        expect(results()).not.toBeNull();
        expect(section()).toBeNull();
      });

      it('offers 1, 5 or 10 Average and Highlight games once the run completes', async () => {
        await complete();
        expect(text(section()?.querySelector('h3'))).toBe('Save games');
        expect(text(section())).toContain(
          'Re-run selected games with the exact same random sequence and save them as browsable rounds.',
        );
        for (const label of ['Average games', 'Highlight games']) {
          expect(await (await select(label)).getValueText()).toBe('1 game');
          expect((await optionsOf(label)).map((o) => o.text)).toEqual([
            '1 game',
            '5 games',
            '10 games',
          ]);
        }
      });

      it('saves the picked number of Average games for the run, disabling both saves', async () => {
        await complete();
        button('Save Average Game(s)').click();
        await stable();
        expect(saveStatus()).toBe('Starting save…');
        expect(button('Save Average Game(s)').disabled).toBe(true);
        expect(button('Save Highlight Game(s)').disabled).toBe(true);
        const req = http.expectOne(saveUrl);
        expect(req.request.method).toBe('POST');
        expect(req.request.body).toEqual({
          team_red: 1,
          team_blue: 2,
          arena_map: null,
          game_type: 'avg',
          n: 1,
        });
        req.flush({ data: { job_id: 'save-1' }, meta: {} });
        await stable();
        expect(saveStatus()).toBe('Saving 1 game(s)…');
        expect(button('Save Highlight Game(s)').disabled).toBe(true);
        http.expectOne(saveStatusUrl);
      });

      it("saves Highlight games with the run's map", async () => {
        await open();
        vi.useFakeTimers({ toFake: POLL_TIMERS });
        await pickTeams();
        button('Run').click();
        await stable();
        http.expectOne(`${BASE}/simulate-batch/`).flush({
          data: { ...STARTED, arena_map_id: 12 },
          meta: {},
        });
        await stable();
        await answer(runStatus({ status: 'complete', completed: 10, partial: aggregate() }));
        await (await select('Highlight games')).clickOptions({ text: '5 games' });
        button('Save Highlight Game(s)').click();
        await stable();
        expect(http.expectOne(saveUrl).request.body).toEqual({
          team_red: 1,
          team_blue: 2,
          arena_map: 12,
          game_type: 'outlier',
          n: 5,
        });
      });

      it('polls the save every 1.5 s and links every saved Round', async () => {
        await complete();
        button('Save Average Game(s)').click();
        await stable();
        http.expectOne(saveUrl).flush({ data: { job_id: 'save-1' }, meta: {} });
        await stable();
        await answerSave({ status: 'running', error: null, round_ids: [] });
        await wait(1499);
        http.expectNone(saveStatusUrl);
        await wait(1);
        await answerSave({ status: 'complete', error: null, round_ids: [5, 6] });
        expect(text(page().querySelector('.save-status p'))).toBe('Saved 2 Round(s).');
        const links = [...page().querySelectorAll<HTMLAnchorElement>('.save-status a')];
        expect(links.map((a) => [text(a), a.getAttribute('href')])).toEqual([
          ['View Round 5', '/rounds/5'],
          ['View Round 6', '/rounds/6'],
        ]);
        expect(button('Save Average Game(s)').disabled).toBe(false);
        await wait(3000);
        http.expectNone(saveStatusUrl);
      });

      it('reports a failed save job and enables the saves again', async () => {
        await complete();
        button('Save Average Game(s)').click();
        await stable();
        http.expectOne(saveUrl).flush({ data: { job_id: 'save-1' }, meta: {} });
        await stable();
        await answerSave({ status: 'error', error: 'Boom.', round_ids: [] });
        expect(saveStatus()).toBe('Save failed: Boom.');
        expect(button('Save Average Game(s)').disabled).toBe(false);
      });

      it('reads "unknown error" for a failed save job with no message', async () => {
        await complete();
        button('Save Highlight Game(s)').click();
        await stable();
        http.expectOne(saveUrl).flush({ data: { job_id: 'save-1' }, meta: {} });
        await stable();
        await answerSave({ status: 'error', error: null, round_ids: [] });
        expect(saveStatus()).toBe('Save failed: unknown error');
      });

      it('shows a refused save with its message and enables the saves again', async () => {
        await complete();
        button('Save Average Game(s)').click();
        await stable();
        http.expectOne(saveUrl).flush(
          {
            errors: [
              { code: 'job_not_complete', message: 'The batch job is not complete.', field: null },
            ],
            meta: {},
          },
          { status: 409, statusText: 'Conflict' },
        );
        await stable();
        expect(saveStatus()).toBe('The batch job is not complete.');
        expect(button('Save Average Game(s)').disabled).toBe(false);
      });

      it('clears the save section and its status when Run is pressed again', async () => {
        await complete();
        button('Save Average Game(s)').click();
        await stable();
        http.expectOne(saveUrl).flush({ data: { job_id: 'save-1' }, meta: {} });
        await stable();
        await answerSave({ status: 'complete', error: null, round_ids: [5] });
        button('Run').click();
        await stable();
        expect(section()).toBeNull();
        expect(page().querySelector('.save-status')).toBeNull();
        http.expectOne(`${BASE}/simulate-batch/`);
      });

      it('stops polling the save when the screen is left', async () => {
        await complete();
        button('Save Average Game(s)').click();
        await stable();
        http.expectOne(saveUrl).flush({ data: { job_id: 'save-1' }, meta: {} });
        await stable();
        await answerSave({ status: 'running', error: null, round_ids: [] });
        harness.fixture.destroy();
        vi.advanceTimersByTime(3000);
        http.expectNone(saveStatusUrl);
      });
    });
  });
});
