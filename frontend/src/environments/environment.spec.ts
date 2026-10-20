import { environment } from './environment';

describe('environment (dev)', () => {
  it('points the API at the local Django server', () => {
    expect(environment.production).toBe(false);
    expect(environment.apiBaseUrl).toBe('http://localhost:8000/api');
  });
});
