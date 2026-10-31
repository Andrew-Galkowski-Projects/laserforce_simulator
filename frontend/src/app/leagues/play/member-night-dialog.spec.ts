import { ApplicationRef } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { MATERIAL_ANIMATIONS } from '@angular/material/core';
import { MatDialog } from '@angular/material/dialog';
import { MemberNightSite } from '../../api/leagues.models';
import { MemberNightDialog, MemberNightDialogData } from './member-night-dialog';

const SITES: MemberNightSite[] = [
  { site: 'Downtown', count: 14 },
  { site: '', count: 12 },
  { site: 'Uptown', count: 20 },
];

describe('Member-night setup dialog (spec #454)', () => {
  let closed: string[] | undefined | 'open';

  async function open(sites: MemberNightSite[] = SITES): Promise<void> {
    TestBed.configureTestingModule({
      // jsdom fires no animationend, so a dialog would never finish closing.
      providers: [{ provide: MATERIAL_ANIMATIONS, useValue: { animationsDisabled: true } }],
    });
    closed = 'open';
    TestBed.inject(MatDialog)
      .open<MemberNightDialog, MemberNightDialogData, string[]>(MemberNightDialog, {
        data: { sites },
      })
      .afterClosed()
      .subscribe((result) => (closed = result));
    await stable();
  }

  async function stable(): Promise<void> {
    await TestBed.inject(ApplicationRef).whenStable();
  }

  afterEach(() => document.querySelector('.cdk-overlay-container')?.remove());

  const dialog = (): HTMLElement | null => document.querySelector('mat-dialog-container');
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const boxes = (): Element[] => [...(dialog()?.querySelectorAll('mat-checkbox') ?? [])];
  const labels = (): string[] => boxes().map((box) => text(box));
  const input = (label: string): HTMLInputElement => {
    const found = boxes()
      .find((box) => text(box) === label)
      ?.querySelector<HTMLInputElement>('input[type="checkbox"]');
    if (!found) throw new Error(`no ${label} checkbox`);
    return found;
  };
  const button = (label: string): HTMLButtonElement => {
    const found = [...(dialog()?.querySelectorAll('button') ?? [])].find((b) => text(b) === label);
    if (!found) throw new Error(`no ${label} button`);
    return found;
  };
  const CONFIRM = 'Confirm — Set Up Member Night';

  async function click(el: HTMLElement): Promise<void> {
    el.click();
    await stable();
  }

  it('lists "All Sites" then every Site with its count, "(no site)" for an empty one', async () => {
    await open();
    expect(labels()).toEqual(['All Sites', 'Downtown (14)', '(no site) (12)', 'Uptown (20)']);
  });

  it('starts with every Site checked and "All Sites" checked', async () => {
    await open();
    expect(labels().map((label) => input(label).checked)).toEqual([true, true, true, true]);
    expect(input('All Sites').indeterminate).toBe(false);
  });

  it('confirms with every Site by default', async () => {
    await open();
    await click(button(CONFIRM));
    expect(closed).toEqual(['Downtown', '', 'Uptown']);
  });

  it('"All Sites" goes indeterminate when the Sites are mixed', async () => {
    await open();
    await click(input('Downtown (14)'));
    expect(input('All Sites').indeterminate).toBe(true);
    await click(button(CONFIRM));
    expect(closed).toEqual(['', 'Uptown']);
  });

  it('"All Sites" clears every Site, then checks them all again', async () => {
    await open();
    await click(input('All Sites'));
    expect(['Downtown (14)', '(no site) (12)', 'Uptown (20)'].map((l) => input(l).checked)).toEqual(
      [false, false, false],
    );
    expect(input('All Sites').checked).toBe(false);
    expect(input('All Sites').indeterminate).toBe(false);
    await click(input('All Sites'));
    expect(labels().map((label) => input(label).checked)).toEqual([true, true, true, true]);
  });

  it('"All Sites" from mixed checks every Site', async () => {
    await open();
    await click(input('Uptown (20)'));
    await click(input('All Sites'));
    expect(labels().map((label) => input(label).checked)).toEqual([true, true, true, true]);
  });

  it('disables Confirm with no Site checked', async () => {
    await open();
    await click(input('All Sites'));
    expect(button(CONFIRM).disabled).toBe(true);
    await click(input('Uptown (20)'));
    expect(button(CONFIRM).disabled).toBe(false);
  });

  it('Cancel closes with nothing', async () => {
    await open();
    await click(button('Cancel'));
    expect(closed).toBeUndefined();
  });

  it('says why with no viable Site, and Confirm is disabled', async () => {
    await open([]);
    expect(text(dialog())).toContain('No Site has enough players (need 12+).');
    expect(labels()).toEqual(['All Sites']);
    expect(button(CONFIRM).disabled).toBe(true);
  });
});
