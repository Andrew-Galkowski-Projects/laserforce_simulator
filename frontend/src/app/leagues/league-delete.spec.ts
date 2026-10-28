import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import {
  HttpTestingController,
  TestRequest,
  provideHttpClientTesting,
} from '@angular/common/http/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { environment } from '../../environments/environment';
import { LeagueDelete, canDeleteLeague } from './league-delete';
import { deleteSummary } from './testing/league-fixtures';

const BASE = environment.apiBaseUrl;

/** A screen that offers Delete the way the list and the dashboard do. */
@Component({
  template: `
    <button type="button" [disabled]="del.busy()" (click)="del.run(target, done)">Delete</button>
    @if (del.error(); as message) {
      <p role="alert">{{ message }}</p>
    }
  `,
})
class Host {
  protected readonly del = new LeagueDelete();
  protected readonly target = { id: 4, name: 'Metro League' };
  deletedCalls = 0;
  protected readonly done = (): void => {
    this.deletedCalls++;
  };
}

@Component({ template: '<p>Leagues list</p>' })
class ListStub {}

describe('League delete flow (spec #409 F6)', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;
  let host: Host;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([
          { path: 'leagues', component: ListStub },
          { path: 'leagues/4', component: Host },
        ]),
        provideHttpClient(),
        provideHttpClientTesting(),
        // jsdom fires no animationend, so a dialog would never finish closing.
        { provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } },
      ],
    });
    http = TestBed.inject(HttpTestingController);
    harness = await RouterTestingHarness.create();
    host = await harness.navigateByUrl('/leagues/4', Host);
  });

  afterEach(() => {
    document.querySelector('.cdk-overlay-container')?.remove();
    http.verify();
  });

  const page = (): HTMLElement => harness.routeNativeElement as HTMLElement;
  const dialog = (): HTMLElement | null => document.querySelector('mat-dialog-container');
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const routerUrl = (): string => TestBed.inject(Router).url;
  const deleteButton = (): HTMLButtonElement =>
    page().querySelector<HTMLButtonElement>('button') as HTMLButtonElement;

  function dialogButton(label: string): HTMLButtonElement {
    const b = [...(dialog()?.querySelectorAll('button') ?? [])].find((el) => text(el) === label);
    if (!b) throw new Error(`no "${label}" button in the dialog`);
    return b;
  }

  function summaryRequest(): TestRequest {
    return http.expectOne(`${BASE}/leagues/4/delete-summary/`);
  }

  async function clickDelete(): Promise<void> {
    deleteButton().click();
    await harness.fixture.whenStable();
  }

  async function openDialog(): Promise<void> {
    await clickDelete();
    const req = summaryRequest();
    expect(req.request.method).toBe('GET');
    req.flush({ data: deleteSummary(), meta: {} });
    await harness.fixture.whenStable();
  }

  function fail(status: number, message: string) {
    return [
      { errors: [{ code: 'x', message, field: null }], meta: {} },
      { status, statusText: 'Error' },
    ] as const;
  }

  it('offers Delete only on a career League (mode "league")', () => {
    expect(canDeleteLeague({ mode: 'league' })).toBe(true);
    expect(canDeleteLeague({ mode: 'sandbox' })).toBe(false);
    expect(canDeleteLeague({ mode: 'multiplayer' })).toBe(false);
  });

  it('fetches the summary first, then asks with the title, the warning and the five counts', async () => {
    await clickDelete();
    expect(dialog()).toBeNull();
    summaryRequest().flush({ data: deleteSummary(), meta: {} });
    await harness.fixture.whenStable();

    expect(text(dialog()?.querySelector('h2'))).toBe('Delete "Metro League"?');
    expect(text(dialog())).toContain('This action is permanent and cannot be undone.');
    const counts = [...(dialog()?.querySelectorAll('li') ?? [])].map((li) => text(li));
    expect(counts).toEqual([
      'Seasons: 2',
      'Matches: 56',
      'Tournaments: 1',
      'Teams: 9',
      'Players: 72',
    ]);
    expect(dialogButton('Delete permanently')).toBeDefined();
  });

  it('sends nothing when the dialog is cancelled', async () => {
    await openDialog();
    dialogButton('Cancel').click();
    await harness.fixture.whenStable();
    expect(dialog()).toBeNull();
    http.expectNone(`${BASE}/leagues/4/`);
    expect(deleteButton().disabled).toBe(false);
    expect(routerUrl()).toBe('/leagues/4');
    expect(host.deletedCalls).toBe(0);
  });

  it('DELETEs on confirm, says so, then goes to /leagues', async () => {
    await openDialog();
    dialogButton('Delete permanently').click();
    await harness.fixture.whenStable();
    const req = http.expectOne(`${BASE}/leagues/4/`);
    expect(req.request.method).toBe('DELETE');
    req.flush(null, { status: 204, statusText: 'No Content' });
    await harness.fixture.whenStable();

    expect(document.body.textContent).toContain('League "Metro League" deleted.');
    expect(routerUrl()).toBe('/leagues');
    expect(host.deletedCalls).toBe(1);
  });

  it('disables Delete while the flow runs (I3)', async () => {
    await clickDelete();
    expect(deleteButton().disabled).toBe(true);
    summaryRequest().flush({ data: deleteSummary(), meta: {} });
    await harness.fixture.whenStable();
    expect(deleteButton().disabled).toBe(true);
    dialogButton('Delete permanently').click();
    await harness.fixture.whenStable();
    expect(deleteButton().disabled).toBe(true);
    http.expectOne(`${BASE}/leagues/4/`).flush(...fail(500, 'Database is down.'));
    await harness.fixture.whenStable();
    expect(deleteButton().disabled).toBe(false);
  });

  it("shows a failed summary's message and opens no dialog", async () => {
    await clickDelete();
    summaryRequest().flush(...fail(409, 'Only career Leagues can be deleted.'));
    await harness.fixture.whenStable();
    expect(dialog()).toBeNull();
    expect(text(page().querySelector('[role="alert"]'))).toBe(
      'Only career Leagues can be deleted.',
    );
    expect(deleteButton().disabled).toBe(false);
  });

  it("shows a failed delete's message and stays put (I3)", async () => {
    await openDialog();
    dialogButton('Delete permanently').click();
    await harness.fixture.whenStable();
    http.expectOne(`${BASE}/leagues/4/`).flush(...fail(500, 'Database is down.'));
    await harness.fixture.whenStable();
    expect(text(page().querySelector('[role="alert"]'))).toBe('Database is down.');
    expect(routerUrl()).toBe('/leagues/4');
    expect(host.deletedCalls).toBe(0);
  });

  it('clears an earlier error when Delete is clicked again', async () => {
    await clickDelete();
    summaryRequest().flush(...fail(500, 'Database is down.'));
    await harness.fixture.whenStable();
    await clickDelete();
    expect(page().querySelector('[role="alert"]')).toBeNull();
    summaryRequest().flush({ data: deleteSummary(), meta: {} });
    await harness.fixture.whenStable();
    dialogButton('Cancel').click();
    await harness.fixture.whenStable();
  });
});
