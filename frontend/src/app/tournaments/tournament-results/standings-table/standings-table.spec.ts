import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { CutLabel, TournamentStandingsRow } from '../../../api/tournaments.models';
import { BLUE, GREEN, RED, standingsRow } from '../results-fixtures';
import { StandingsTable } from './standings-table';

describe('StandingsTable', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
  });

  async function render(
    rows: TournamentStandingsRow[],
    cutLabels?: Record<string, CutLabel>,
  ): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(StandingsTable);
    fixture.componentRef.setInput('rows', rows);
    if (cutLabels) {
      fixture.componentRef.setInput('cutLabels', cutLabels);
    }
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const headers = (el: HTMLElement): string[] =>
    [...el.querySelectorAll('thead th')].map((th) => text(th));
  const bodyRows = (el: HTMLElement): HTMLTableRowElement[] => [
    ...el.querySelectorAll<HTMLTableRowElement>('tbody tr'),
  ];
  const cells = (row: HTMLTableRowElement): string[] =>
    [...row.querySelectorAll('td')].map((td) => text(td));

  const rows = (): TournamentStandingsRow[] => [
    standingsRow(),
    standingsRow({
      team_id: BLUE.id,
      team: BLUE,
      rank: 2,
      wins: 1,
      losses: 1,
      ties: 0,
      league_points: 8,
      round_wins: 3,
      total_score: 47000,
    }),
    standingsRow({
      team_id: GREEN.id,
      team: GREEN,
      rank: 3,
      wins: 0,
      losses: 1,
      ties: 1,
      league_points: 3,
      round_wins: 1,
      total_score: 39000,
    }),
  ];

  it('has the Rank, Team, MP, W, L, T, Match Pts, RW and TS columns', async () => {
    const el = await render(rows());
    expect(headers(el)).toEqual(['Rank', 'Team', 'MP', 'W', 'L', 'T', 'Match Pts', 'RW', 'TS']);
  });

  it('shows one row per Standings row, the Team linked to its history', async () => {
    const el = await render(rows());
    expect(bodyRows(el).map(cells)).toEqual([
      ['1', 'Red Raiders', '2', '2', '0', '0', '12', '4', '51000'],
      ['2', 'Blue Bolts', '2', '1', '1', '0', '8', '3', '47000'],
      ['3', 'Green Giants', '2', '0', '1', '1', '3', '1', '39000'],
    ]);
    expect([...el.querySelectorAll('tbody a')].map((a) => a.getAttribute('href'))).toEqual([
      '/teams/11/history',
      '/teams/12/history',
      '/teams/13/history',
    ]);
  });

  it('shows the team id when the row has no Team', async () => {
    const el = await render([standingsRow({ team_id: 99, team: null })]);
    expect(cells(bodyRows(el)[0])[1]).toBe('99');
    expect(el.querySelector('tbody a')).toBeNull();
  });

  it('has no Cut column and no cut markers without cut labels', async () => {
    const el = await render(rows(), {});
    expect(headers(el)).not.toContain('Cut');
    expect(bodyRows(el).map((tr) => tr.getAttribute('data-cut'))).toEqual([null, null, null]);
  });

  it('adds a Cut column (WB / LB / Out) and marks each row with its cut', async () => {
    const el = await render(rows(), {
      [String(RED.id)]: 'wb',
      [String(BLUE.id)]: 'lb',
      [String(GREEN.id)]: 'out',
    });
    expect(headers(el)).toEqual([
      'Rank',
      'Team',
      'MP',
      'W',
      'L',
      'T',
      'Match Pts',
      'RW',
      'TS',
      'Cut',
    ]);
    expect(bodyRows(el).map((tr) => cells(tr).at(-1))).toEqual(['WB', 'LB', 'Out']);
    expect(bodyRows(el).map((tr) => tr.getAttribute('data-cut'))).toEqual(['wb', 'lb', 'out']);
  });

  it('scrolls sideways inside its own box', async () => {
    const el = await render(rows());
    const scroll = el.querySelector('.standings');
    expect(scroll?.querySelector('table')).not.toBeNull();
    expect(getComputedStyle(scroll as Element).overflowX).toBe('auto');
  });

  it('keeps Team names whole: they wrap only between words and keep a minimum width', async () => {
    // At 768px `overflow-wrap: anywhere` broke names letter by letter (browser check).
    const el = await render(rows());
    const team = getComputedStyle(el.querySelector('.standings__team') as Element);
    expect(team.overflowWrap).not.toBe('anywhere');
    expect(team.minWidth).toBe('128px'); // 8rem
  });
});
