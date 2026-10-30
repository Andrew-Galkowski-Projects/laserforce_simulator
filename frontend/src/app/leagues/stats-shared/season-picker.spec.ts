import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { SeasonParam, SeasonScope } from '../../api/league-stats.models';
import { seasonOption, seasonScope } from '../testing/league-stats-fixtures';
import { SeasonPicker } from './season-picker';

@Component({
  imports: [SeasonPicker],
  template: `<app-season-picker
    [scope]="scope()"
    [career]="career()"
    (seasonChange)="picked.push($event)"
  />`,
})
class Host {
  readonly scope = signal<SeasonScope>(seasonScope());
  readonly career = signal(true);
  readonly picked: (SeasonParam | null)[] = [];
}

describe('SeasonPicker (spec #417)', () => {
  async function render(scope: SeasonScope, career = true) {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.scope.set(scope);
    fixture.componentInstance.career.set(career);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    const select = el.querySelector<HTMLSelectElement>('select#stats-season-select')!;
    return { fixture, el, select };
  }

  const options = (select: HTMLSelectElement): [string, string][] =>
    [...select.options].map((o) => [o.value, o.textContent?.trim() ?? '']);

  it('labels each Season "name (year)", or the name alone without a year, then Career', async () => {
    const { el, select } = await render(
      seasonScope({
        options: [seasonOption(), seasonOption({ id: 9, name: 'Preseason', year: null })],
      }),
    );
    expect(el.querySelector('label')?.textContent?.trim()).toBe('Season:');
    expect(el.querySelector('label')?.getAttribute('for')).toBe('stats-season-select');
    expect(options(select)).toEqual([
      ['11', 'Season 1 (2026)'],
      ['9', 'Preseason'],
      ['career', 'Career'],
    ]);
  });

  it('offers no Career when told not to (Playoffs)', async () => {
    const { select } = await render(seasonScope(), false);
    expect(options(select).map(([v]) => v)).toEqual(['11', '10']);
  });

  it('selects the scope the server resolved: a Season, or Career', async () => {
    let r = await render(seasonScope({ selected: { id: 10, name: 'Season 0' } }));
    expect(r.select.value).toBe('10');
    r = await render(seasonScope({ selected: 'career' }));
    expect(r.select.value).toBe('career');
  });

  it('emits a Season id as a number, and career as career', async () => {
    const { fixture, select } = await render(seasonScope());
    select.value = '10';
    select.dispatchEvent(new Event('change'));
    select.value = 'career';
    select.dispatchEvent(new Event('change'));
    expect(fixture.componentInstance.picked).toEqual([10, 'career']);
  });

  it('shows only Career for a League with no Season', async () => {
    const { select } = await render(seasonScope({ selected: null, options: [] }));
    expect(options(select)).toEqual([['career', 'Career']]);
  });
});
