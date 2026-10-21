import { HttpContextToken } from '@angular/common/http';

/** Path prefix (relative to `apiBaseUrl`) of the JWT endpoints: cookie, no Bearer, no refresh. */
export const TOKEN_PATH = 'token/';

/**
 * Set on a request to have `authInterceptor` pass its 401 straight through: no
 * refresh, no replay, no redirect to `/login`. The Bearer is still attached.
 */
export const SKIP_AUTH_REFRESH = new HttpContextToken<boolean>(() => false);
