import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { TeamRef } from '../../api/analytics.models';
import { teamOptions } from '../testing/league-stats-fixtures';
import { TeamPicker } from './team-picker';

@Component({
  imports: [TeamPicker],
  template: `<app-team-picker
    [options]="options()"
    [selected]="selected()"
    [allLabel]="allLabel()"
    (teamChange)="picked.push($event)"
  />`,
})
class Host {
  readonly options = signal<TeamRef[]>(teamOptions());
  readonly selected = signal<number | null>(null);
  readonly allLabel = signal('All Teams');
  readonly picked: (number | null)[] = [];
}

@Component({
  imports: [TeamPicker],
  template: `<app-team-picker
    [options]="options"
    [selected]="3"
    [allowAll]="false"
    (teamChange)="picked.push($event)"
  />`,
})
class NoAllHost {
  readonly options = teamOptions();
  readonly picked: (number | null)[] = [];
}

describe('TeamPicker (spec #417)', () => {
  async function render(selected: number | null = null, allLabel = 'All Teams') {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.selected.set(selected);
    fixture.componentInstance.allLabel.set(allLabel);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const select = el.querySelector<HTMLSelectElement>('select#stats-team-select')!;
    return { fixture, el, select };
  }

  const options = (select: HTMLSelectElement): [string, string][] =>
    [...select.options].map((o) => [o.value, o.textContent?.trim() ?? '']);

  it('lists "All Teams" then the Teams by name, under a "Team:" label', async () => {
    const { el, select } = await render();
    expect(el.querySelector('label')?.textContent?.trim()).toBe('Team:');
    expect(options(select)).toEqual([
      ['', 'All Teams'],
      ['5', 'Blue Bolts'],
      ['3', 'Red Raiders'],
    ]);
    expect(select.value).toBe('');
  });

  it('takes its own "All" label (Game Log: "All teams")', async () => {
    const { select } = await render(null, 'All teams');
    expect(options(select)[0]).toEqual(['', 'All teams']);
  });

  it('drops the "All" option with [allowAll]="false" (the League team screens)', async () => {
    const fixture = TestBed.createComponent(NoAllHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const select = el.querySelector<HTMLSelectElement>('select#stats-team-select')!;
    expect(el.querySelector('label')?.textContent?.trim()).toBe('Team:');
    expect(options(select)).toEqual([
      ['5', 'Blue Bolts'],
      ['3', 'Red Raiders'],
    ]);
    expect(select.value).toBe('3');
    select.value = '5';
    select.dispatchEvent(new Event('change'));
    expect(fixture.componentInstance.picked).toEqual([5]);
  });

  it('selects the chosen Team', async () => {
    const { select } = await render(3);
    expect(select.value).toBe('3');
  });

  it('emits a Team id, or null for All', async () => {
    const { fixture, select } = await render(3);
    select.value = '5';
    select.dispatchEvent(new Event('change'));
    select.value = '';
    select.dispatchEvent(new Event('change'));
    expect(fixture.componentInstance.picked).toEqual([5, null]);
  });
});
