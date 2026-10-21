import { TestBed } from '@angular/core/testing';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Title } from '@angular/platform-browser';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { signIn } from './api/testing/auth-fixtures';
import { routes } from './app.routes';
import { authInterceptor } from './auth/auth.interceptor';

describe('app routes', () => {
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter(routes),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  const routerUrl = (): string => TestBed.inject(Router).url;

  describe('signed out', () => {
    it('sends a deep link to /login with a returnUrl', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/teams/5/edit');
      expect(routerUrl()).toBe('/login?returnUrl=%2Fteams%2F5%2Fedit');
    });

    it('sends the app root to /login with /teams as the returnUrl', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/');
      expect(routerUrl()).toBe('/login?returnUrl=%2Fteams');
    });

    it('shows the sign-in page at /login', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/login');
      expect(routerUrl()).toBe('/login');
      expect(TestBed.inject(Title).getTitle()).toBe('Sign in - Laserforce Simulator');
    });
  });

  describe('signed in', () => {
    beforeEach(async () => {
      await signIn();
    });

    it('redirects the app root to /teams', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/');
      expect(routerUrl()).toBe('/teams');
    });

    it('redirects an unknown path to /teams', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/no-such-page');
      expect(routerUrl()).toBe('/teams');
    });

    it('sends a visit to /login on to /teams', async () => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl('/login?returnUrl=%2Fteams%2F3');
      expect(routerUrl()).toBe('/teams');
    });

    it.each([
      ['/teams', 'Teams', 'Teams - Laserforce Simulator'],
      ['/teams/new', 'Create Team', 'Create Team - Laserforce Simulator'],
      ['/teams/3', 'Team Detail', 'Team - Laserforce Simulator'],
      ['/teams/3/edit', 'Edit Team', 'Edit Team - Laserforce Simulator'],
      ['/teams/3/slots', 'Assign Slots', 'Assign Slots - Laserforce Simulator'],
    ])('resolves %s to its Teams screen and page title', async (url, heading, title) => {
      const harness = await RouterTestingHarness.create();
      await harness.navigateByUrl(url);
      expect(routerUrl()).toBe(url);
      expect(harness.routeNativeElement?.querySelector('h1')?.textContent).toBe(heading);
      expect(TestBed.inject(Title).getTitle()).toBe(title);
    });
  });
});
