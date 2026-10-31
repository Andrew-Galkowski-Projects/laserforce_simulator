import { Component, signal } from '@angular/core';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { DashboardBody, MemberNightProgress } from '../../api/leagues.models';
import { seasonDashboard } from '../testing/league-fixtures';
import { DashboardCards, DashboardLink } from './dashboard-cards';

@Component({
  imports: [DashboardCards],
  template: `<app-dashboard-cards
    [body]="body"
    [memberNight]="night"
    [leadersLink]="leaders"
    [resultsLink]="results()"
  />`,
})
class Host {
  readonly body: DashboardBody = seasonDashboard();
  readonly night: MemberNightProgress = { played: 3, total: 10 };
  readonly leaders: DashboardLink = { commands: ['/leagues', 1, 'leaders'] };
  readonly results = signal<DashboardLink | null>(null);
}

describe('DashboardCards member-night card (spec #444)', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting(), provideRouter([])],
    });
  });

  async function render(results: DashboardLink | null): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.results.set(results);
    await fixture.whenStable();
    return (fixture.nativeElement as HTMLElement).querySelector(
      '.dashboard__member-night',
    ) as HTMLElement;
  }

  const text = (el: Element): string => (el.textContent ?? '').replace(/\s+/g, ' ').trim();

  it('links "View results" when a results link is given', async () => {
    const card = await render({
      commands: ['/leagues', 1, 'seasons', 11, 'schedule'],
      fragment: 'member-nights',
    });
    expect(text(card)).toBe('Member night: 3 / 10 games played View results');
    expect(card.querySelector('a')?.getAttribute('href')).toBe(
      '/leagues/1/seasons/11/schedule#member-nights',
    );
  });

  it('with no results link shows only the progress: no "View results", no Soon badge', async () => {
    const card = await render(null);
    expect(text(card)).toBe('Member night: 3 / 10 games played');
    expect(card.querySelector('a, app-soon-badge, .soon')).toBeNull();
  });
});
