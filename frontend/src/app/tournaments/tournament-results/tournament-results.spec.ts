import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { TournamentDetail } from '../../api/tournaments.models';
import { tournamentDetail } from '../testing/detail-fixtures';
import {
  BLUE,
  RED,
  bracketGroup,
  bracketNode,
  bracketRounds,
  eliminationGroups,
  standingsRow,
  twoTeamCrosstable,
} from './results-fixtures';
import { TournamentResults } from './tournament-results';

describe('TournamentResults', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
  });

  async function render(detail: TournamentDetail): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(TournamentResults);
    fixture.componentRef.setInput('detail', detail);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const all = (el: Element, selector: string): string[] =>
    [...el.querySelectorAll(selector)].map((h) => text(h));
  const columnNames = (el: Element): string[] => all(el, '.bracket__round-name');

  const STANDINGS = [standingsRow(), standingsRow({ team_id: BLUE.id, team: BLUE, rank: 2 })];

  describe('single elimination', () => {
    const singleElim = (rounds: number): TournamentDetail =>
      tournamentDetail({
        state: 'active',
        brackets: [bracketGroup('winners', bracketRounds(rounds))],
      });

    it('shows one "Bracket" section, its columns named from the end (2 rounds)', async () => {
      const el = await render(singleElim(2));
      expect(all(el, 'h2')).toEqual(['Bracket']);
      expect(el.querySelectorAll('app-bracket').length).toBe(1);
      expect(columnNames(el)).toEqual(['Semifinals', 'Final']);
    });

    it('names a 4-round field Round 1, Quarterfinals, Semifinals, Final', async () => {
      const el = await render(singleElim(4));
      expect(columnNames(el)).toEqual(['Round 1', 'Quarterfinals', 'Semifinals', 'Final']);
    });

    it('heads its columns h3, straight under the h2 "Bracket" (no skipped level)', async () => {
      const el = await render(singleElim(2));
      expect(all(el, 'h3')).toEqual(['Semifinals', 'Final']);
      expect(all(el, 'h4')).toEqual([]);
    });

    it('shows each node card with its Teams', async () => {
      const el = await render(singleElim(1));
      expect(text(el.querySelector('app-node-card'))).toContain('[1] Red Raiders');
    });

    it('shows nothing before the Bracket is built', async () => {
      const el = await render(tournamentDetail());
      expect(all(el, 'h2')).toEqual([]);
      expect(el.querySelector('app-bracket')).toBeNull();
    });
  });

  describe('double elimination', () => {
    it('shows "Bracket" with Winners, Losers and Grand Final, every column "Round N"', async () => {
      const el = await render(
        tournamentDetail({
          format: 'double_elimination',
          state: 'active',
          brackets: eliminationGroups(),
        }),
      );
      expect(all(el, 'h2')).toEqual(['Bracket']);
      expect(all(el, 'h3')).toEqual(['Winners Bracket', 'Losers Bracket', 'Grand Final']);
      expect(columnNames(el)).toEqual(['Round 1', 'Round 2', 'Round 1', 'Round 2', 'Round 3']);
      expect(el.querySelector('app-crosstable')).toBeNull();
      expect(el.querySelector('app-standings-table')).toBeNull();
    });
  });

  describe('round robin', () => {
    const roundRobin = (): TournamentDetail =>
      tournamentDetail({
        format: 'round_robin',
        state: 'active',
        stage: 'round_robin',
        brackets: [bracketGroup('round_robin', bracketRounds(2))],
        rr_crosstable: twoTeamCrosstable(),
        rr_standings: STANDINGS,
      });

    it('shows the Crosstable and the Standings only, with no node cards', async () => {
      const el = await render(roundRobin());
      expect(all(el, 'h2')).toEqual(['Crosstable', 'Standings']);
      expect(el.querySelector('app-node-card')).toBeNull();
      expect(el.querySelector('app-bracket')).toBeNull();
    });

    it('fills the Crosstable and the Standings from the detail', async () => {
      const el = await render(roundRobin());
      expect(all(el, 'app-crosstable tbody th')).toEqual(['Red Raiders', 'Blue Bolts']);
      expect(all(el, 'app-standings-table tbody tr td:nth-child(2)')).toEqual([
        'Red Raiders',
        'Blue Bolts',
      ]);
      expect(all(el, 'app-standings-table thead th')).not.toContain('Cut');
    });
  });

  describe('round robin -> double elimination', () => {
    const rrde = (overrides: Partial<TournamentDetail> = {}): TournamentDetail =>
      tournamentDetail({
        format: 'round_robin_double_elim',
        state: 'active',
        stage: 'seeding',
        brackets: [bracketGroup('round_robin', bracketRounds(2))],
        rr_crosstable: twoTeamCrosstable(),
        rr_standings: STANDINGS,
        cut_labels: { [String(RED.id)]: 'wb', [String(BLUE.id)]: 'out' },
        ...overrides,
      });

    it('shows the Crosstable and the Standings with the Cut column in the seeding stage', async () => {
      const el = await render(rrde());
      expect(all(el, 'h2')).toEqual(['Crosstable', 'Standings']);
      expect(all(el, 'app-standings-table thead th')).toContain('Cut');
      expect(el.querySelector('app-bracket')).toBeNull();
    });

    it('adds the Finals Bracket (Winners, Losers, Grand Final) once it exists', async () => {
      const el = await render(
        rrde({
          stage: 'finals',
          cut_labels: {},
          brackets: [bracketGroup('round_robin', bracketRounds(2)), ...eliminationGroups()],
        }),
      );
      expect(all(el, 'h2')).toEqual(['Crosstable', 'Standings', 'Finals Bracket']);
      expect(all(el, 'h3')).toEqual(['Winners Bracket', 'Losers Bracket', 'Grand Final']);
      expect(columnNames(el)).toEqual(['Round 1', 'Round 2', 'Round 1', 'Round 2', 'Round 3']);
    });
  });

  describe('swiss', () => {
    it('shows a "Round N" group of node cards per Swiss round, then the Standings', async () => {
      const el = await render(
        tournamentDetail({
          format: 'swiss',
          state: 'active',
          stage: 'swiss',
          brackets: [
            bracketGroup('swiss', [
              { bracket_round: 1, nodes: [bracketNode(), bracketNode({ position: 1 })] },
              { bracket_round: 2, nodes: [bracketNode()] },
            ]),
          ],
          swiss_standings: STANDINGS,
        }),
      );
      expect(all(el, 'h2')).toEqual(['Swiss Rounds', 'Standings']);
      expect(all(el, 'h3')).toEqual(['Round 1', 'Round 2']);
      const rounds = [...el.querySelectorAll('.tournament-results__swiss-round')];
      expect(rounds.map((r) => r.querySelectorAll('app-node-card').length)).toEqual([2, 1]);
      expect(all(el, 'app-standings-table tbody tr td:nth-child(2)')).toEqual([
        'Red Raiders',
        'Blue Bolts',
      ]);
      expect(el.querySelector('app-crosstable')).toBeNull();
    });
  });
});
