import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { CrosstableRow } from '../../../api/tournaments.models';
import { RED, playedLeg, twoTeamCrosstable } from '../results-fixtures';
import { Crosstable } from './crosstable';

describe('Crosstable', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
  });

  async function render(rows: CrosstableRow[]): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(Crosstable);
    fixture.componentRef.setInput('rows', rows);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const bodyCells = (el: HTMLElement, row: number): HTMLTableCellElement[] => [
    ...(el.querySelectorAll('tbody tr')[row]?.querySelectorAll('td') ?? []),
  ];

  it('heads the columns and the rows with the Teams, linked to their history', async () => {
    const el = await render(twoTeamCrosstable());
    const head = [...el.querySelectorAll('thead th')];
    expect(head.map((th) => text(th))).toEqual(['', 'Red Raiders', 'Blue Bolts']);
    expect(head.map((th) => th.querySelector('a')?.getAttribute('href') ?? null)).toEqual([
      null,
      '/teams/11/history',
      '/teams/12/history',
    ]);
    const rowHeads = [...el.querySelectorAll('tbody th')];
    expect(rowHeads.map((th) => text(th))).toEqual(['Red Raiders', 'Blue Bolts']);
    expect(rowHeads.map((th) => th.querySelector('a')?.getAttribute('href'))).toEqual([
      '/teams/11/history',
      '/teams/12/history',
    ]);
  });

  it('shows a played leg as its points linked to the Match, then the muted Match score', async () => {
    const el = await render(twoTeamCrosstable());
    const cell = bodyCells(el, 0)[1];
    expect(text(cell)).toBe('30000–21000 (6–0)');
    expect(cell.querySelector('a')?.getAttribute('href')).toBe('/matches/70');
    expect(text(cell.querySelector('a'))).toBe('30000–21000');
    expect(text(cell.querySelector('.crosstable__match-score'))).toBe('(6–0)');
  });

  it('shows an unplayed leg as a dash, with no link', async () => {
    const el = await render(twoTeamCrosstable());
    const cell = bodyCells(el, 1)[0];
    expect(text(cell)).toBe('—');
    expect(cell.querySelector('a')).toBeNull();
  });

  it('leaves the diagonal blank', async () => {
    const el = await render(twoTeamCrosstable());
    expect(text(bodyCells(el, 0)[0])).toBe('');
    expect(text(bodyCells(el, 1)[1])).toBe('');
  });

  it('shows both legs of a cell, leg 1 first', async () => {
    const rows = twoTeamCrosstable();
    rows[0].cells[1] = {
      opponent_team_id: 12,
      leg1: playedLeg(),
      leg2: playedLeg({
        team_score: 18000,
        opp_score: 25000,
        match_team: 2,
        match_opp: 4,
        match_id: 71,
      }),
    };
    const el = await render(rows);
    const cell = bodyCells(el, 0)[1];
    expect([...cell.querySelectorAll('.crosstable__leg')].map((leg) => text(leg))).toEqual([
      '30000–21000 (6–0)',
      '18000–25000 (2–4)',
    ]);
    expect([...cell.querySelectorAll('a')].map((a) => a.getAttribute('href'))).toEqual([
      '/matches/70',
      '/matches/71',
    ]);
  });

  it('shows a Team-less row head as a dash', async () => {
    const el = await render([{ team: null, cells: [null] }]);
    expect(text(el.querySelector('tbody th'))).toBe('—');
    expect(el.querySelector('tbody th a')).toBeNull();
  });

  it('scrolls sideways inside its own box', async () => {
    const el = await render([{ team: RED, cells: [null] }]);
    const scroll = el.querySelector('.crosstable');
    expect(scroll?.querySelector('table')).not.toBeNull();
    expect(getComputedStyle(scroll as Element).overflowX).toBe('auto');
  });
});
