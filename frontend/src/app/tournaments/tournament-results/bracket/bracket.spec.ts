import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { BracketRound } from '../../../api/tournaments.models';
import { BLUE, GOLD, GREEN, RED, bracketNode, bracketRounds } from '../results-fixtures';
import { RoundNamer, singleEliminationRoundName } from '../round-names';
import { Bracket } from './bracket';

describe('Bracket', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
  });

  async function render(rounds: BracketRound[], roundName?: RoundNamer): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(Bracket);
    fixture.componentRef.setInput('rounds', rounds);
    if (roundName) {
      fixture.componentRef.setInput('roundName', roundName);
    }
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const columnNames = (el: HTMLElement): string[] =>
    [...el.querySelectorAll('.bracket__round-name')].map((h) => text(h));

  it('names every column "Round N" by default', async () => {
    const el = await render(bracketRounds(3));
    expect(columnNames(el)).toEqual(['Round 1', 'Round 2', 'Round 3']);
  });

  it('names the columns with the given rule', async () => {
    const el = await render(bracketRounds(2), singleEliminationRoundName);
    expect(columnNames(el)).toEqual(['Semifinals', 'Final']);
  });

  it('puts each round its node cards, in order', async () => {
    const el = await render([
      {
        bracket_round: 1,
        nodes: [
          bracketNode({ position: 0, team_a: RED, team_b: BLUE }),
          bracketNode({ position: 1, team_a: GREEN, team_b: GOLD, seed_a: 3, seed_b: 4 }),
        ],
      },
      { bracket_round: 2, nodes: [bracketNode({ team_a: null, team_b: null })] },
    ]);
    const columns = [...el.querySelectorAll('.bracket__round')];
    expect(columns.map((c) => c.querySelectorAll('app-node-card').length)).toEqual([2, 1]);
    expect(text(columns[0].querySelectorAll('app-node-card')[1])).toContain('[3] Green Giants');
  });

  it('scrolls its columns sideways inside itself', async () => {
    const el = await render(bracketRounds(4));
    const columns = el.querySelector('.bracket');
    expect(columns).not.toBeNull();
    expect(getComputedStyle(columns as Element).overflowX).toBe('auto');
  });
});
