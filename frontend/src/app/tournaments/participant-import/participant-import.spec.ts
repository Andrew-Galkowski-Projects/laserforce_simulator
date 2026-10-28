import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { HarnessLoader } from '@angular/cdk/testing';
import { TestbedHarnessEnvironment } from '@angular/cdk/testing/testbed';
import { MatSnackBarHarness } from '@angular/material/snack-bar/testing';
import { provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../../environments/environment';
import { signIn } from '../../api/testing/auth-fixtures';
import { TournamentDetail } from '../../api/tournaments.models';
import { routes } from '../../app.routes';
import { authInterceptor } from '../../auth/auth.interceptor';
import { tournamentDetail } from '../testing/detail-fixtures';
import { releasePoolLoads } from '../testing/pool-fixtures';

const BASE = environment.apiBaseUrl;
const IMPORT_URL = `${BASE}/tournaments/5/import-participants/`;

/** A jsdom-safe stand-in for the browser's object-URL pair, restored by `restore()`. */
function stubObjectUrls(): {
  create: ReturnType<typeof vi.fn>;
  revoke: ReturnType<typeof vi.fn>;
  restore: () => void;
} {
  const url = URL as unknown as Record<string, unknown>;
  const before = { create: url['createObjectURL'], revoke: url['revokeObjectURL'] };
  const create = vi.fn(() => 'blob:participant-template');
  const revoke = vi.fn();
  url['createObjectURL'] = create;
  url['revokeObjectURL'] = revoke;
  return {
    create,
    revoke,
    restore: () => {
      url['createObjectURL'] = before.create;
      url['revokeObjectURL'] = before.revoke;
    },
  };
}

describe('Participant import (Tournament detail)', () => {
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

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const card = (): HTMLElement | null => page().querySelector('app-participant-import');

  function detailRequest(): TestRequest {
    return http.expectOne(`${BASE}/tournaments/5/`);
  }

  async function open(detail: TournamentDetail = tournamentDetail()): Promise<void> {
    await harness.navigateByUrl(`/tournaments/${detail.id}`);
    detailRequest().flush({ data: detail, meta: {} });
    await harness.fixture.whenStable();
  }

  function button(label: string): HTMLButtonElement {
    const found = [...(card()?.querySelectorAll('button') ?? [])].find((b) => text(b) === label);
    if (!found) throw new Error(`no ${label} button`);
    return found;
  }

  function fileInput(): HTMLInputElement {
    const input = card()?.querySelector<HTMLInputElement>('input[type="file"][name="csv_file"]');
    if (!input) throw new Error('no csv_file input');
    return input;
  }

  /** Picks `file` the way the browser does: a `change` on the hidden input. */
  async function choose(file: File): Promise<void> {
    const input = fileInput();
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    input.dispatchEvent(new Event('change'));
    await harness.fixture.whenStable();
  }

  const csv = (name = 'field.csv'): File =>
    new File(['team,name,role\nGreen Giants,Ace,commander\n'], name, { type: 'text/csv' });

  /** Clicks Import Participants and answers the POST with an error envelope. */
  async function failWith(
    errors: { code: string; message: string; field: string | null }[],
    status = 400,
  ): Promise<void> {
    button('Import Participants').click();
    await harness.fixture.whenStable();
    http.expectOne(IMPORT_URL).flush({ errors, meta: {} }, { status, statusText: 'Bad Request' });
    await harness.fixture.whenStable();
  }

  it('shows the "Import Participants (CSV)" card for a preset sandbox Tournament in setup', async () => {
    await open();
    expect(text(card()?.querySelector('h2'))).toBe('Import Participants (CSV)');
    expect(fileInput().getAttribute('accept')).toBe('.csv,text/csv');
    expect(text(card())).toContain('No file chosen');
    expect(button('Import Participants').type).toBe('submit');
    expect(button('Import Participants').disabled).toBe(true);
    expect(button('Download a template CSV').type).toBe('button');
  });

  it.each([
    ['active', { state: 'active' }],
    ['completed', { state: 'completed' }],
    ['player_pool', { team_assembly: 'player_pool', participants: [] }],
    ['embedded', { embedded: true, season: { id: 3, name: 'Season 2026' } }],
  ] as [string, Partial<TournamentDetail>][])(
    'hides the import card for a %s Tournament',
    async (_label, overrides) => {
      await open(tournamentDetail(overrides));
      // A Player-pool Tournament shows the Player Pool section instead, which loads its own data.
      releasePoolLoads();
      expect(page().querySelector('h1')).not.toBeNull();
      expect(card()).toBeNull();
      expect(page().textContent).not.toContain('Import Participants');
    },
  );

  it('POSTs the chosen file as multipart csv_file to import-participants', async () => {
    await open();
    const file = csv('autumn-field.csv');
    await choose(file);
    expect(text(card())).toContain('autumn-field.csv');
    expect(button('Import Participants').disabled).toBe(false);
    button('Import Participants').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(IMPORT_URL);
    expect(req.request.method).toBe('POST');
    const form = req.request.body as FormData;
    expect([...form.keys()]).toEqual(['csv_file']);
    expect(form.get('csv_file')).toBe(file);
    req.flush(
      { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
      { status: 400, statusText: 'Bad Request' },
    );
    await harness.fixture.whenStable();
  });

  const rowErrors = (): string[] =>
    [...(card()?.querySelectorAll('.participant-import__rows li') ?? [])].map((li) => text(li));
  const fileError = (): string =>
    text(fileInput().closest('.participant-import__field')?.querySelector('mat-error'));
  const alert = (): HTMLElement | null =>
    card()?.querySelector<HTMLElement>('[role="alert"]') ?? null;

  it('lists row errors as "Row N (column): message" and keeps the chosen file', async () => {
    await open();
    await choose(csv('autumn-field.csv'));
    await failWith([
      { code: 'invalid', message: 'Unknown role "pilot".', field: 'row:2:role' },
      { code: 'invalid', message: 'Row has 7 columns, expected 8.', field: 'row:4' },
    ]);
    expect(rowErrors()).toEqual([
      'Row 2 (role): Unknown role "pilot".',
      'Row 4: Row has 7 columns, expected 8.',
    ]);
    expect(alert()).toBeNull();
    expect(fileError()).toBe('');
    expect(text(card())).toContain('autumn-field.csv');
    expect(button('Import Participants').disabled).toBe(false);
  });

  it('shows a csv_file error on the file control, not in the alert', async () => {
    await open();
    await choose(csv());
    await failWith([
      { code: 'invalid', message: 'The file must be UTF-8 encoded.', field: 'csv_file' },
    ]);
    expect(fileError()).toBe('The file must be UTF-8 encoded.');
    expect(alert()).toBeNull();
    expect(rowErrors()).toEqual([]);
  });

  it('shows any other error (a stale 409) in the alert', async () => {
    await open();
    await choose(csv());
    await failWith(
      [
        {
          code: 'tournament_not_setup',
          message: 'The Tournament is no longer in setup.',
          field: null,
        },
      ],
      409,
    );
    expect(text(alert())).toBe('The Tournament is no longer in setup.');
    expect(fileError()).toBe('');
  });

  it('clears the previous errors when Import is clicked again', async () => {
    await open();
    await choose(csv());
    await failWith([
      { code: 'invalid', message: 'Unknown role.', field: 'row:1:role' },
      { code: 'invalid', message: 'Nothing to import.', field: null },
    ]);
    expect(rowErrors()).toHaveLength(1);
    button('Import Participants').click();
    await harness.fixture.whenStable();
    expect(rowErrors()).toEqual([]);
    expect(alert()).toBeNull();
    http
      .expectOne(IMPORT_URL)
      .flush(
        { errors: [{ code: 'x', message: 'x', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
    await harness.fixture.whenStable();
  });

  it('disables Import Participants while the request is in flight', async () => {
    await open();
    await choose(csv());
    button('Import Participants').click();
    await harness.fixture.whenStable();
    expect(button('Import Participants').disabled).toBe(true);
    button('Import Participants').click();
    await harness.fixture.whenStable();
    http
      .expectOne(IMPORT_URL)
      .flush(
        { errors: [{ code: 'invalid', message: 'Nothing to import.', field: null }], meta: {} },
        { status: 400, statusText: 'Bad Request' },
      );
    await harness.fixture.whenStable();
    expect(button('Import Participants').disabled).toBe(false);
  });

  it('on a 201 shows "Imported N participants." and reloads the detail', async () => {
    const before = tournamentDetail();
    await open(before);
    await choose(csv());
    button('Import Participants').click();
    await harness.fixture.whenStable();
    const after = tournamentDetail({
      participants: [
        { team: { id: 13, name: 'Green Giants' }, seed: 1 },
        { team: { id: 11, name: 'Red Raiders' }, seed: 2 },
        { team: { id: 14, name: 'Gold Guns' }, seed: 3 },
        { team: { id: 12, name: 'Blue Bolts' }, seed: 4 },
      ],
    });
    http
      .expectOne(IMPORT_URL)
      .flush({ data: after, meta: {} }, { status: 201, statusText: 'Created' });
    await harness.fixture.whenStable();
    const snackBar = await loader.getHarness(MatSnackBarHarness);
    expect(await snackBar.getMessage()).toBe('Imported 2 participants.');
    expect(await snackBar.getActionDescription()).toBe('Close');
    const reload = detailRequest();
    expect(reload.request.method).toBe('GET');
    reload.flush({ data: after, meta: {} });
    await harness.fixture.whenStable();
    expect(
      [...page().querySelectorAll('.tournament-participants tbody tr')].map((tr) =>
        text(tr.querySelector('a')),
      ),
    ).toEqual(['Green Giants', 'Red Raiders', 'Gold Guns', 'Blue Bolts']);
    expect(text(card())).toContain('No file chosen');
    expect(rowErrors()).toEqual([]);
  });

  it('says "Imported 1 participant." for a single new Team', async () => {
    const before = tournamentDetail();
    await open(before);
    await choose(csv());
    button('Import Participants').click();
    await harness.fixture.whenStable();
    const after = tournamentDetail({
      participants: [
        ...before.participants,
        { team: { id: 13, name: 'Green Giants' }, seed: before.participants.length + 1 },
      ],
    });
    http
      .expectOne(IMPORT_URL)
      .flush({ data: after, meta: {} }, { status: 201, statusText: 'Created' });
    await harness.fixture.whenStable();
    const snackBar = await loader.getHarness(MatSnackBarHarness);
    expect(await snackBar.getMessage()).toBe('Imported 1 participant.');
    detailRequest().flush({ data: after, meta: {} });
    await harness.fixture.whenStable();
  });

  it('downloads the template CSV through the API and saves the blob', async () => {
    await open();
    const urls = stubObjectUrls();
    const downloads: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      downloads.push(this.download);
    });
    try {
      button('Download a template CSV').click();
      await harness.fixture.whenStable();
      const req = http.expectOne(`${BASE}/teams/import/template/`);
      expect(req.request.method).toBe('GET');
      expect(downloads).toEqual([]);
      req.flush(new Blob(['team,name\n'], { type: 'text/csv' }));
      await harness.fixture.whenStable();
      expect(urls.create).toHaveBeenCalledTimes(1);
      expect(downloads).toEqual(['roster_template.csv']);
      expect(urls.revoke).toHaveBeenCalledWith('blob:participant-template');
    } finally {
      click.mockRestore();
      urls.restore();
    }
  });

  it('shows a failed template download in the alert', async () => {
    await open();
    button('Download a template CSV').click();
    await harness.fixture.whenStable();
    http
      .expectOne(`${BASE}/teams/import/template/`)
      .flush(new Blob(['nope']), { status: 500, statusText: 'Server Error' });
    await harness.fixture.whenStable();
    expect(text(alert())).toBe('The server responded with 500.');
  });
});
