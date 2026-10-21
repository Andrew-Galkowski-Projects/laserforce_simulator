import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { routes } from './app.routes';
import { Home } from './home/home';

describe('app routes', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideRouter(routes)],
    });
  });

  it('shows the home page at /', async () => {
    const harness = await RouterTestingHarness.create();
    const page = await harness.navigateByUrl('/', Home);
    expect(page).toBeInstanceOf(Home);
  });

  it('shows the sign-in placeholder at /login, keeping a returnUrl', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/login?returnUrl=%2Fteams');
    expect(TestBed.inject(Router).url).toBe('/login?returnUrl=%2Fteams');
    expect(harness.routeNativeElement?.textContent).toContain('Sign in (coming in ANG-06)');
  });

  it('redirects an unknown path to / and shows the home page', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/no-such-page');
    expect(TestBed.inject(Router).url).toBe('/');
    expect(harness.routeDebugElement?.componentInstance).toBeInstanceOf(Home);
  });
});
