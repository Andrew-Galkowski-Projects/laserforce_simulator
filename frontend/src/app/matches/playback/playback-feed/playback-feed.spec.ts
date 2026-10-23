import { ComponentFixture, TestBed } from '@angular/core/testing';
import { TimelineEvent } from '../../../api/rounds.models';
import { timelineEvent } from '../../testing/timeline-fixtures';
import { PlaybackFeed } from './playback-feed';

describe('PlaybackFeed', () => {
  let fixture: ComponentFixture<PlaybackFeed>;

  async function render(events: TimelineEvent[], cursor: number): Promise<void> {
    fixture = TestBed.createComponent(PlaybackFeed);
    fixture.componentRef.setInput('events', events);
    fixture.componentRef.setInput('cursor', cursor);
    await fixture.whenStable();
  }

  async function setCursor(cursor: number): Promise<void> {
    fixture.componentRef.setInput('cursor', cursor);
    await fixture.whenStable();
  }

  const host = (): HTMLElement => fixture.nativeElement as HTMLElement;
  const text = (el: Element | null | undefined): string =>
    el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const rows = (): HTMLElement[] => [...host().querySelectorAll<HTMLElement>('.pf-row')];
  const descriptions = (): string[] => rows().map((r) => text(r.querySelector('.pf-row__desc')));

  const tag = (tick: number, description: string): TimelineEvent =>
    timelineEvent({ tick, description });

  it('shows the events up to the cursor, newest first, the first row current', async () => {
    await render([tag(10, 'first'), tag(20, 'second'), tag(30, 'third')], 20);
    expect(descriptions()).toEqual(['second', 'first']);
    expect(rows().map((r) => r.classList.contains('is-current'))).toEqual([true, false]);

    await setCursor(30);
    expect(descriptions()).toEqual(['third', 'second', 'first']);
    expect(rows()[0].classList).toContain('is-current');
  });

  it('colours each row by its Event group and the names by their Side, with R / B badges', async () => {
    await render(
      [
        timelineEvent({ tick: 5, event_type: 'resupply_lives', description: 'Cleo resupplied' }),
        timelineEvent({
          tick: 9,
          event_type: 'missiled',
          description: 'Dax missiled Ace',
          actor_name: 'Dax',
          actor_side: 'blue',
          target_name: 'Ace',
          target_side: 'red',
        }),
      ],
      100,
    );
    const [missile, resupply] = rows();
    expect(missile.style.getPropertyValue('--pf-ev')).toBe('var(--lf-ev-missile)');
    expect(text(missile.querySelector('.pf-row__chip'))).toBe('Missile');
    expect(resupply.style.getPropertyValue('--pf-ev')).toBe('var(--lf-ev-resupply)');
    expect(text(missile.querySelector('.pf-row__time'))).toBe('00:04');
    const actor = missile.querySelector('.pf-row__actor');
    expect(actor?.getAttribute('data-lf-side')).toBe('blue');
    expect(text(actor)).toBe('B Dax');
    const target = missile.querySelector('.pf-row__target');
    expect(target?.getAttribute('data-lf-side')).toBe('red');
    expect(text(target)).toBe('R Ace');
  });

  describe('filters', () => {
    const checkbox = (label: string): HTMLInputElement => {
      const box = [...host().querySelectorAll('mat-checkbox')].find((c) => text(c) === label);
      const input = box?.querySelector<HTMLInputElement>('input[type="checkbox"]');
      if (!input) throw new Error(`no ${label} checkbox`);
      return input;
    };
    async function toggle(label: string): Promise<void> {
      checkbox(label).click();
      await fixture.whenStable();
    }
    const mixed = (): TimelineEvent[] => [
      tag(1, 'Ace tagged Bolt'),
      timelineEvent({ tick: 2, event_type: 'miss', description: 'Ace missed' }),
      timelineEvent({ tick: 3, event_type: 'movement', description: 'Ace moved' }),
    ];

    it('hides Misses and Movement by default, each shown once its toggle is on', async () => {
      await render(mixed(), 10);
      expect(checkbox('Misses').checked).toBe(false);
      expect(checkbox('Movement').checked).toBe(false);
      expect(descriptions()).toEqual(['Ace tagged Bolt']);

      await toggle('Misses');
      expect(descriptions()).toEqual(['Ace missed', 'Ace tagged Bolt']);
      await toggle('Movement');
      expect(descriptions()).toEqual(['Ace moved', 'Ace missed', 'Ace tagged Bolt']);
      await toggle('Misses');
      expect(descriptions()).toEqual(['Ace moved', 'Ace tagged Bolt']);
    });

    async function search(term: string): Promise<void> {
      const box = host().querySelector<HTMLInputElement>('input[type="search"]');
      if (!box) throw new Error('no search box');
      box.value = term;
      box.dispatchEvent(new Event('input'));
      await fixture.whenStable();
    }

    it('searches actor, description and target, ignoring case', async () => {
      await render(
        [
          timelineEvent({ tick: 1, description: 'Ace tagged Bolt' }),
          timelineEvent({
            tick: 2,
            description: 'Cleo healed',
            event_type: 'resupply_lives',
            actor_name: 'Cleo',
            target_name: 'Dax',
          }),
          timelineEvent({
            tick: 3,
            description: 'zap',
            actor_name: 'Dax',
            target_name: null,
            target_side: null,
            target_id: null,
          }),
        ],
        10,
      );
      await search('cLeO');
      expect(descriptions()).toEqual(['Cleo healed']);
      await search('DAX');
      expect(descriptions()).toEqual(['zap', 'Cleo healed']);
      await search('tagged');
      expect(descriptions()).toEqual(['Ace tagged Bolt']);
      await search('');
      expect(descriptions()).toEqual(['zap', 'Cleo healed', 'Ace tagged Bolt']);
    });

    it('says "No events match your search." when filtering empties the feed', async () => {
      await render([tag(1, 'Ace tagged Bolt')], 10);
      expect(text(host())).not.toContain('No events match your search.');
      await search('nobody');
      expect(rows()).toEqual([]);
      expect(text(host())).toContain('No events match your search.');
      expect(text(host())).not.toContain('No events yet.');
    });

    it('marks the newest *shown* row current', async () => {
      await render(mixed(), 10);
      expect(rows().filter((r) => r.classList.contains('is-current')).length).toBe(1);
      expect(text(rows()[0].querySelector('.pf-row__desc'))).toBe('Ace tagged Bolt');
      expect(rows()[0].classList).toContain('is-current');
    });
  });

  it('says "No events yet." before the cursor reaches the first event', async () => {
    await render([tag(10, 'first')], 9);
    expect(rows()).toEqual([]);
    expect(text(host())).toContain('No events yet.');
    await setCursor(10);
    expect(text(host())).not.toContain('No events yet.');
  });

  it('says "No events yet." for a Round with no events', async () => {
    await render([], 1800);
    expect(text(host())).toContain('No events yet.');
  });

  it('keeps the rows in a scrolling box with a max height', async () => {
    await render([tag(1, 'a')], 10);
    const box = host().querySelector<HTMLElement>('.pf-box');
    const style = getComputedStyle(box as HTMLElement);
    expect(style.overflowY).toBe('auto');
    expect(style.maxHeight).not.toBe('none');
    expect(style.maxHeight).not.toBe('');
  });

  it('shows no target for an event without one', async () => {
    await render(
      [
        timelineEvent({
          event_type: 'locking',
          target_id: null,
          target_name: null,
          target_side: null,
        }),
      ],
      1800,
    );
    expect(rows()[0].querySelector('.pf-row__target')).toBeNull();
  });
});
