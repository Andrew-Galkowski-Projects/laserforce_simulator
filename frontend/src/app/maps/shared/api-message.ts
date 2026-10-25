import { ApiError } from '../../api/api-client';

/** The server's message for a failed request, or `fallback` when it is not an `ApiError`. */
export function apiMessage(error: unknown, fallback = ''): string {
  return error instanceof ApiError ? error.message : fallback;
}
