import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { StatRate } from '../../api/league-stats.models';
import { RATE_OPTIONS, RatePicker } from './rate-picker';

@Component({
  imports: [RatePicker],
  template: `<app-rate-picker [rate]="rate()" (rateChange)="picked.set($event)" />`,
})
class Host {
  readonly rate = signal<StatRate>('per_game');
  readonly picked = signal<StatRate | null>(null);
}

describe('RatePicker (stats kit, spec #428 F7)', () => {
  it('lists Totals / Per Game / Per 10 min, Totals first', () => {
    expect(RATE_OPTIONS.map(([value, label]) => `${value}:${label}`)).toEqual([
      'total:Totals',
      'per_game:Per Game',
      'per_10:Per 10 min',
    ]);
  });

  it('shows the given rate and emits the picked one', async () => {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const select = (fixture.nativeElement as HTMLElement).querySelector<HTMLSelectElement>(
      '#stats-rate-select',
    )!;
    expect(select.value).toBe('per_game');
    select.value = 'per_10';
    select.dispatchEvent(new Event('change'));
    expect(fixture.componentInstance.picked()).toBe('per_10');
  });
});
