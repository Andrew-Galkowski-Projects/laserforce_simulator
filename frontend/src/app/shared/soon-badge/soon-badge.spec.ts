import { Component } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { SoonBadge } from './soon-badge';

@Component({
  imports: [SoonBadge],
  template: '<app-soon-badge label="View bracket" />',
})
class Host {}

describe('SoonBadge (spec #409 I1)', () => {
  async function render(): Promise<HTMLElement> {
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('renders the label as a disabled span with a "Soon" badge', async () => {
    const host = await render();
    const span = host.querySelector<HTMLElement>('span.soon');
    expect(span).not.toBeNull();
    expect(span?.getAttribute('aria-disabled')).toBe('true');
    expect(span?.textContent?.replace(/\s+/g, ' ').trim()).toBe('View bracket Soon');
    expect(span?.querySelector('.soon__badge')?.textContent?.trim()).toBe('Soon');
  });

  it('is never a link', async () => {
    const host = await render();
    expect(host.querySelector('a')).toBeNull();
    expect(host.querySelector('[href]')).toBeNull();
  });
});
