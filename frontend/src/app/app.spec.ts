import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { App } from './app';

describe('App shell', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [App],
      providers: [provideRouter([])],
    }).compileComponents();
  });

  it('shows the app title in a Material toolbar', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const shell = fixture.nativeElement as HTMLElement;
    expect(shell.querySelector('mat-toolbar')?.textContent).toContain('Laserforce Simulator');
  });

  it('does not use the Material 2 `color` input on the toolbar', async () => {
    // Under the Material 3 `mat.theme()` mixin `color="primary"` is a no-op; the primary colour
    // comes from `mat.toolbar-overrides` in app.scss instead.
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const shell = fixture.nativeElement as HTMLElement;
    const toolbar = shell.querySelector('mat-toolbar');
    expect(toolbar).not.toBeNull();
    expect(toolbar?.hasAttribute('color')).toBe(false);
  });

  it('has a router outlet for the pages', async () => {
    const fixture = TestBed.createComponent(App);
    await fixture.whenStable();
    const shell = fixture.nativeElement as HTMLElement;
    expect(shell.querySelector('router-outlet')).not.toBeNull();
  });
});
