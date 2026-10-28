import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { BracketNode } from '../../../api/tournaments.models';
import { BLUE, RED, bracketNode } from '../results-fixtures';
import { NodeCard } from './node-card';

describe('NodeCard', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideRouter([])] });
  });

  async function render(node: BracketNode): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(NodeCard);
    fixture.componentRef.setInput('node', node);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const slots = (card: HTMLElement): string[] =>
    [...card.querySelectorAll('.node-card__slot')].map((slot) => text(slot));
  const hrefs = (card: HTMLElement, selector: string): (string | null)[] =>
    [...card.querySelectorAll(selector)].map((a) => a.getAttribute('href'));

  it('shows each slot as its seed and Team, the Team linking to its match history', async () => {
    const card = await render(bracketNode());
    expect(slots(card)).toEqual(['[1] Red Raiders', '[2] Blue Bolts']);
    expect(hrefs(card, '.node-card__slot a')).toEqual(['/teams/11/history', '/teams/12/history']);
  });

  it('shows an empty slot as a dash, with no seed', async () => {
    const card = await render(bracketNode({ team_b: null, seed_b: null }));
    expect(slots(card)).toEqual(['[1] Red Raiders', '—']);
  });

  it('shows "Bye" and no Series for a bye', async () => {
    const card = await render(bracketNode({ is_bye: true, team_b: null, seed_b: null }));
    expect(text(card.querySelector('.node-card__bye'))).toBe('Bye');
    expect(card.textContent).not.toContain('Bo1');
    expect(card.querySelector('.node-card__series')).toBeNull();
  });

  it('shows the Series length, the Series score and one linked line per Game', async () => {
    const card = await render(
      bracketNode({
        series_length: 3,
        wins_a: 2,
        wins_b: 1,
        games: [
          { game_number: 1, match_id: 41, score_a: 6, score_b: 0 },
          { game_number: 2, match_id: 42, score_a: 2, score_b: 4 },
        ],
      }),
    );
    expect(text(card.querySelector('.node-card__length'))).toBe('Bo3');
    expect(text(card.querySelector('.node-card__score'))).toBe('2–1');
    expect([...card.querySelectorAll('.node-card__game')].map((g) => text(g))).toEqual([
      'Game 1 6–0 match pts',
      'Game 2 2–4 match pts',
    ]);
    expect(hrefs(card, '.node-card__game a')).toEqual(['/matches/41', '/matches/42']);
    expect(card.querySelector('.node-card__bye')).toBeNull();
  });

  it('shows "Winner: <team>" linking to its history once decided', async () => {
    const card = await render(bracketNode({ wins_a: 1, winner: RED }));
    const winner = card.querySelector('.node-card__winner');
    expect(text(winner)).toBe('Winner: Red Raiders');
    expect(winner?.querySelector('a')?.getAttribute('href')).toBe('/teams/11/history');
  });

  it('shows no winner line while undecided', async () => {
    const card = await render(bracketNode({ team_b: BLUE }));
    expect(card.querySelector('.node-card__winner')).toBeNull();
    expect(card.textContent).not.toContain('Winner');
  });
});
