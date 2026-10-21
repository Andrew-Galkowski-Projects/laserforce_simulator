// Shared spec fixtures for a signed-in Account. Imported only by *.spec.ts files.
import { TestBed } from '@angular/core/testing';
import { HttpTestingController } from '@angular/common/http/testing';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AuthService } from '../../auth/auth.service';
import { User } from '../../auth/user';

export const USER: User = {
  id: 1,
  email: 'ann@example.com',
  date_joined: '2026-10-01T09:00:00Z',
  is_staff: false,
  self_insert: null,
};

/**
 * Signs `user` in the way the app does (`token/` then `accounts/me/`), answering both requests.
 * Needs `provideHttpClientTesting()` in the TestBed.
 */
export async function signIn(user: User = USER): Promise<void> {
  const http = TestBed.inject(HttpTestingController);
  const result = firstValueFrom(TestBed.inject(AuthService).login(user.email, 'pw'));
  http.expectOne(`${environment.apiBaseUrl}/token/`).flush({ data: { access: 'acc-1' }, meta: {} });
  http.expectOne(`${environment.apiBaseUrl}/accounts/me/`).flush({ data: user, meta: {} });
  await result;
}
