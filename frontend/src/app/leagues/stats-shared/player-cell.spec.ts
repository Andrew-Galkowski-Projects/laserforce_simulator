import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { provideRouter } from '@angular/router';
import { PlayerRef } from '../../api/analytics.models';
import { SELF_INSERT, USER, signIn } from '../../api/testing/auth-fixtures';
import { User } from '../../auth/user';
import { PlayerCell } from './player-cell';

@Component({
  imports: [PlayerCell],
  template: `<app-player-cell [player]="player()" [watched]="watched()" />`,
})
class Host {
  readonly player = signal<PlayerRef>({ id: 25, name: 'Ace', team: null });
  readonly watched = signal(false);
}

describe('PlayerCell (spec #417 I4)', () => {
  async function render(watched: boolean, user: User = USER, id = 25) {
    TestBed.configureTestingModule({
      providers: [provideRouter([]), provideHttpClient(), provideHttpClientTesting()],
    });
    await signIn(user);
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.watched.set(watched);
    fixture.componentInstance.player.set({ id, name: 'Ace', team: null });
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  const text = (el: Element | null): string => el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';

  it("links the name to the Player's career stats", async () => {
    const el = await render(false);
    const link = el.querySelector('a');
    expect(text(link)).toBe('Ace');
    expect(link?.getAttribute('href')).toBe('/players/25/stats');
  });

  it('shows the read-only watched marker only on a watched Player', async () => {
    let el = await render(true);
    const marker = el.querySelector('.player-cell__watched');
    expect(marker?.getAttribute('title')).toBe('Watched');
    expect(marker?.tagName).toBe('SPAN');
    expect(el.querySelector('button')).toBeNull();
    TestBed.resetTestingModule();
    el = await render(false);
    expect(el.querySelector('.player-cell__watched')).toBeNull();
  });

  it("marks the signed-in Account's Self-insert with You", async () => {
    const el = await render(false, { ...USER, self_insert: { ...SELF_INSERT, id: 25 } });
    expect(text(el)).toBe('Ace You');
  });

  it('marks no one else', async () => {
    const el = await render(false, { ...USER, self_insert: { ...SELF_INSERT, id: 26 } });
    expect(text(el)).toBe('Ace');
  });
});
