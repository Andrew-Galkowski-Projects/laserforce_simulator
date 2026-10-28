import { ComponentFixture, TestBed } from '@angular/core/testing';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { compile } from 'sass';
import { PlaybackSnapshot, TimelinePlayer } from '../../../api/rounds.models';
import { playbackSnapshot } from '../../testing/playback-fixtures';
import { timelinePlayer } from '../../testing/timeline-fixtures';
import { PlaybackScoreboard } from './playback-scoreboard';

/** Red's three players: Ace (commander), Cleo (medic), Finn (scout). */
const ACE = timelinePlayer();
const CLEO = timelinePlayer({ id: 102, name: 'Cleo', role: 'medic' });
const FINN = timelinePlayer({ id: 103, name: 'Finn', role: 'scout' });
const PLAYERS = [ACE, CLEO, FINN];

describe('PlaybackScoreboard', () => {
  let fixture: ComponentFixture<PlaybackScoreboard>;

  async function render(
    snapshots: Record<number, PlaybackSnapshot[]>,
    cursor: number,
    players: TimelinePlayer[] = PLAYERS,
  ): Promise<void> {
    fixture = TestBed.createComponent(PlaybackScoreboard);
    const ref = fixture.componentRef;
    ref.setInput('side', 'red');
    ref.setInput('teamName', 'Comets');
    ref.setInput('players', players);
    ref.setInput('snapshots', new Map(Object.entries(snapshots).map(([id, s]) => [Number(id), s])));
    ref.setInput('cursor', cursor);
    ref.setInput('notTargetableTicks', 8);
    ref.setInput('respawnTicks', 16);
    await fixture.whenStable();
  }

  async function setCursor(cursor: number): Promise<void> {
    fixture.componentRef.setInput('cursor', cursor);
    await fixture.whenStable();
  }

  const host = (): HTMLElement => fixture.nativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const rows = (): HTMLElement[] => [...host().querySelectorAll<HTMLElement>('tbody tr')];
  const cells = (row: HTMLElement): string[] => [...row.querySelectorAll('td')].map((c) => text(c));
  const names = (): string[] => rows().map((r) => text(r.querySelector('.sb-name__text')));
  const rowOf = (name: string): HTMLElement =>
    rows().find((r) => text(r.querySelector('.sb-name__text')) === name)!;

  it('heads the board with the team name and the live Side total, and shows the values at the cursor', async () => {
    await render(
      {
        101: [
          playbackSnapshot({ missiles: 5 }),
          playbackSnapshot({ tick: 50, score: 100, tags: 2, shots: 28, missiles: 4, sp: 3 }),
          playbackSnapshot({
            tick: 90,
            score: 300,
            tags: 4,
            misses: 4,
            shots: 22,
            missiles: 4,
            sp: 6,
          }),
        ],
        102: [playbackSnapshot({ lives: 20, shots: 15 })],
        103: [playbackSnapshot(), playbackSnapshot({ tick: 60, score: 40, lives: 14 })],
      },
      70,
    );

    expect(text(host().querySelector('.sb-head__team'))).toBe('Comets');
    expect(text(host().querySelector('.sb-head__total'))).toBe('140');
    expect([...host().querySelectorAll('thead th')].map((th) => text(th))).toEqual([
      '#',
      'Player',
      '',
      'Pos',
      'Score',
      'Acc',
      'Lives',
      'Shots',
      'Msl',
      'SP',
    ]);
    expect(cells(rowOf('Ace'))).toEqual([
      '1',
      'Ace',
      '',
      'CMD',
      '100',
      '100%',
      '15',
      '28',
      '4',
      '3',
    ]);

    await setCursor(90);
    expect(text(host().querySelector('.sb-head__total'))).toBe('340');
    expect(cells(rowOf('Ace'))).toEqual([
      '1',
      'Ace',
      '',
      'CMD',
      '300',
      '50%',
      '15',
      '22',
      '4',
      '6',
    ]);
    expect(cells(rowOf('Cleo'))).toEqual(['3', 'Cleo', '', 'MED', '0', '—', '20', '15', '0', '0']);
  });

  it('re-sorts the rows by live score, with # the live rank', async () => {
    await render(
      {
        101: [playbackSnapshot(), playbackSnapshot({ tick: 10, score: 50 })],
        102: [playbackSnapshot(), playbackSnapshot({ tick: 20, score: 200 })],
        103: [playbackSnapshot(), playbackSnapshot({ tick: 30, score: 120 })],
      },
      10,
    );
    expect(names()).toEqual(['Ace', 'Cleo', 'Finn']);
    expect(rows().map((r) => cells(r)[0])).toEqual(['1', '2', '3']);

    await setCursor(30);
    expect(names()).toEqual(['Cleo', 'Finn', 'Ace']);
    expect(rows().map((r) => cells(r)[0])).toEqual(['1', '2', '3']);
  });

  describe('Down', () => {
    /** Ace is Down at tick 100: not targetable to 108, resetting to 116. */
    const downAt100 = {
      101: [playbackSnapshot(), playbackSnapshot({ tick: 100, lives: 14, downed_at: 100 })],
    };
    const ace = (): HTMLElement => rowOf('Ace');
    const dot = (): HTMLElement | null => ace().querySelector('.sb-dot');
    const isDim = (): boolean => ace().querySelector('.sb-name')!.classList.contains('is-down');

    it('dims the name while cursor - downed_at < respawn_ticks', async () => {
      await render(downAt100, 99, [ACE]);
      expect(isDim()).toBe(false);
      await setCursor(100);
      expect(isDim()).toBe(true);
      await setCursor(115.8);
      expect(isDim()).toBe(true);
      await setCursor(116);
      expect(isDim()).toBe(false);
    });

    it('fills the respawn dot over respawn_ticks, one colour before not_targetable_ticks and another after', async () => {
      await render(downAt100, 99, [ACE]);
      expect(dot()).toBeNull();

      await setCursor(104);
      expect(dot()!.classList).toContain('is-not-targetable');
      expect(dot()!.style.getPropertyValue('--sb-dot-fill')).toBe('90deg');

      await setCursor(110);
      expect(dot()!.classList).toContain('is-resetting');
      expect(dot()!.classList).not.toContain('is-not-targetable');
      expect(dot()!.style.getPropertyValue('--sb-dot-fill')).toBe('225deg');

      await setCursor(116);
      expect(dot()).toBeNull();
    });
  });

  it('dims an eliminated row and shows an OUT badge instead of a respawn dot', async () => {
    await render(
      {
        101: [playbackSnapshot()],
        103: [
          playbackSnapshot(),
          playbackSnapshot({ tick: 200, lives: 0, downed_at: 200, eliminated: true }),
        ],
      },
      199,
      [ACE, FINN],
    );
    expect(rowOf('Finn').classList).not.toContain('is-out');
    expect(rowOf('Finn').querySelector('.sb-out')).toBeNull();

    await setCursor(202);
    expect(rowOf('Finn').classList).toContain('is-out');
    expect(text(rowOf('Finn').querySelector('.sb-out'))).toBe('OUT');
    expect(rowOf('Finn').querySelector('.sb-dot')).toBeNull();
    expect(rowOf('Ace').classList).not.toContain('is-out');
  });

  it("shows a Heavy's SP as '—', since the role has no special", async () => {
    const bolt = timelinePlayer({ id: 201, name: 'Bolt', side: 'blue', role: 'heavy' });
    await render(
      { 101: [playbackSnapshot({ sp: 4 })], 201: [playbackSnapshot({ lives: 10 })] },
      0,
      [ACE, bolt],
    );
    expect(cells(rowOf('Bolt'))[3]).toBe('HVY');
    expect(cells(rowOf('Bolt'))[9]).toBe('—');
    expect(cells(rowOf('Ace'))[9]).toBe('4');
  });

  it("shows Acc as tags / (tags + misses), or '—' before the first shot", async () => {
    await render(
      {
        101: [playbackSnapshot({ tags: 1, misses: 3 })],
        102: [playbackSnapshot({ tags: 2, misses: 1 })],
        103: [playbackSnapshot({ tags: 0, misses: 2 })],
      },
      0,
    );
    expect(cells(rowOf('Ace'))[5]).toBe('25%');
    expect(cells(rowOf('Cleo'))[5]).toBe('67%');
    expect(cells(rowOf('Finn'))[5]).toBe('0%');

    await render({ 101: [playbackSnapshot()] }, 0, [ACE]);
    expect(cells(rowOf('Ace'))[5]).toBe('—');
  });

  describe('animations', () => {
    const nameText = (name: string): Element => rowOf(name).querySelector('.sb-name__text')!;

    it('pulses the name while rapid_fire is on', async () => {
      await render(
        {
          101: [playbackSnapshot()],
          103: [
            playbackSnapshot(),
            playbackSnapshot({ tick: 50, rapid_fire: true }),
            playbackSnapshot({ tick: 90, rapid_fire: false }),
          ],
        },
        49,
        [ACE, FINN],
      );
      expect(nameText('Finn').classList).not.toContain('sb-rapid');
      await setCursor(50);
      expect(nameText('Finn').classList).toContain('sb-rapid');
      expect(nameText('Ace').classList).not.toContain('sb-rapid');
      await setCursor(90);
      expect(nameText('Finn').classList).not.toContain('sb-rapid');
    });

    it('shows the name in rainbow text while nuking is on', async () => {
      await render(
        {
          101: [
            playbackSnapshot(),
            playbackSnapshot({ tick: 300, nuking: true }),
            playbackSnapshot({ tick: 306, nuking: false, score: 500 }),
          ],
          102: [playbackSnapshot()],
        },
        299,
        [ACE, CLEO],
      );
      expect(nameText('Ace').classList).not.toContain('sb-rainbow');
      await setCursor(303);
      expect(nameText('Ace').classList).toContain('sb-rainbow');
      expect(nameText('Cleo').classList).not.toContain('sb-rainbow');
      await setCursor(306);
      expect(nameText('Ace').classList).not.toContain('sb-rainbow');
    });

    describe('rank-up flash', () => {
      /**
       * Fakes only setTimeout / clearTimeout, after the first render, and drives change detection
       * with `detectChanges()`: zoneless `whenStable` needs the real setTimeout.
       */
      afterEach(() => {
        vi.useRealTimers();
      });

      function moveTo(cursor: number): void {
        fixture.componentRef.setInput('cursor', cursor);
        fixture.detectChanges();
      }
      function advance(ms: number): void {
        vi.advanceTimersByTime(ms);
        fixture.detectChanges();
      }
      const flashing = (): string[] =>
        rows()
          .filter((r) => r.classList.contains('sb-flash'))
          .map((r) => text(r.querySelector('.sb-name__text')));

      it('flashes a row for ~700 ms when its rank improves since the previous cursor', async () => {
        await render(
          {
            101: [playbackSnapshot(), playbackSnapshot({ tick: 10, score: 50 })],
            102: [playbackSnapshot(), playbackSnapshot({ tick: 20, score: 200 })],
            103: [playbackSnapshot(), playbackSnapshot({ tick: 30, score: 10 })],
          },
          10,
        );
        expect(flashing()).toEqual([]);
        vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });

        moveTo(20);
        expect(names()).toEqual(['Cleo', 'Ace', 'Finn']);
        expect(flashing()).toEqual(['Cleo']);

        advance(650);
        expect(flashing()).toEqual(['Cleo']);
        advance(100);
        expect(flashing()).toEqual([]);

        moveTo(25);
        expect(flashing()).toEqual([]);
      });
    });

    it('animates the pulse, rainbow and flash only under prefers-reduced-motion: no-preference', () => {
      // The spec runs from `frontend/` (`npm run test:ci`) or from the repo root
      // (`npm --prefix frontend exec -- ng test`), as theme.spec.ts allows for.
      const scss = 'app/matches/playback/playback-scoreboard/playback-scoreboard.scss';
      const file = [
        join(process.cwd(), 'src', scss),
        join(process.cwd(), 'frontend', 'src', scss),
      ].find((path) => existsSync(path)) as string;
      const css = compile(file).css;
      const opener = '@media (prefers-reduced-motion: no-preference)';
      const start = css.indexOf(opener);
      expect(start).toBeGreaterThan(-1);
      let depth = 0;
      let end = css.indexOf('{', start);
      do {
        if (css[end] === '{') depth++;
        if (css[end] === '}') depth--;
        end++;
      } while (depth > 0);
      const motion = css.slice(start, end);
      const rest = css.slice(0, start) + css.slice(end);

      for (const cls of ['.sb-rapid', '.sb-rainbow', '.sb-flash']) {
        expect(motion).toMatch(new RegExp(`\\${cls}[^{]*\\{[^}]*animation:`));
      }
      expect(motion.match(/@keyframes/g)?.length).toBe(3);
      expect(rest).not.toMatch(/@keyframes|animation/);
      // Reduced motion: the 700 ms rank-up flash does not show at all (no green rank, no row
      // tint), while the still rainbow text and rapid-fire glow stay.
      expect(rest).not.toMatch(/\.sb-flash/);
      expect(rest).toMatch(/\.sb-rapid[^{]*\{[^}]*text-shadow:/);
      expect(rest).toMatch(/\.sb-rainbow[^{]*\{[^}]*background-clip:/);
    });
  });
});
