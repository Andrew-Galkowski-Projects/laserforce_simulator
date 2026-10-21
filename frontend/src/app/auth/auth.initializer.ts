import { EnvironmentProviders, inject, provideAppInitializer } from '@angular/core';
import { AuthService } from './auth.service';

/** One silent refresh on start-up; a visitor with no session starts signed out. */
export function provideAuthInitializer(): EnvironmentProviders {
  return provideAppInitializer(() => inject(AuthService).restoreSession());
}
