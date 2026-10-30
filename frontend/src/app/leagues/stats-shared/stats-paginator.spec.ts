import { Component, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { PerPagePicker, StatsPaginator } from './stats-paginator';

@Component({
  imports: [StatsPaginator, PerPagePicker],
  template: `
    <app-stats-paginator
      [page]="page()"
      [totalPages]="totalPages()"
      (pageChange)="pages.push($event)"
    />
    <app-per-page-picker [perPage]="perPage()" (perPageChange)="sizes.push($event)" />
  `,
})
class Host {
  readonly page = signal(1);
  readonly totalPages = signal(3);
  readonly perPage = signal(10);
  readonly pages: number[] = [];
  readonly sizes: number[] = [];
}

describe('StatsPaginator and PerPagePicker (spec #417)', () => {
  async function render(page: number, totalPages: number, perPage = 10) {
    const fixture = TestBed.createComponent(Host);
    fixture.componentInstance.page.set(page);
    fixture.componentInstance.totalPages.set(totalPages);
    fixture.componentInstance.perPage.set(perPage);
    await fixture.whenStable();
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }

  const text = (el: Element | null): string => el?.textContent?.replace(/\s+/g, ' ').trim() ?? '';
  const button = (el: HTMLElement, label: string): HTMLButtonElement =>
    [...el.querySelectorAll<HTMLButtonElement>('nav button')].find((b) => text(b) === label)!;

  it('shows Previous / "Page X of N" / Next', async () => {
    const { el } = await render(2, 3);
    expect(text(el.querySelector('nav.stats-pager'))).toBe('Previous Page 2 of 3 Next');
  });

  it('disables Previous on page 1 and Next on the last page', async () => {
    let r = await render(1, 3);
    expect(button(r.el, 'Previous').disabled).toBe(true);
    expect(button(r.el, 'Next').disabled).toBe(false);
    r = await render(3, 3);
    expect(button(r.el, 'Previous').disabled).toBe(false);
    expect(button(r.el, 'Next').disabled).toBe(true);
  });

  it('emits the page before and after', async () => {
    const { fixture, el } = await render(2, 3);
    button(el, 'Previous').click();
    button(el, 'Next').click();
    expect(fixture.componentInstance.pages).toEqual([1, 3]);
  });

  it('hides itself when everything fits on one page', async () => {
    const { el } = await render(1, 1);
    expect(el.querySelector('nav.stats-pager')).toBeNull();
  });

  it('offers 10 / 25 / 50 / 100 per page, the current one chosen, and emits a new size', async () => {
    const { fixture, el } = await render(1, 1, 25);
    const select = el.querySelector<HTMLSelectElement>('select#stats-per-page-select')!;
    expect(text(el.querySelector('label[for="stats-per-page-select"]'))).toBe('Per page:');
    expect([...select.options].map((o) => o.value)).toEqual(['10', '25', '50', '100']);
    expect(select.value).toBe('25');
    select.value = '100';
    select.dispatchEvent(new Event('change'));
    expect(fixture.componentInstance.sizes).toEqual([100]);
  });
});
