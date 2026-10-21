import { environment } from './environment.worktree';

describe('environment (worktree)', () => {
  it("points the API at the worktree's Django on 8001, off the owner's 8000", () => {
    expect(environment.production).toBe(false);
    expect(environment.apiBaseUrl).toBe('http://localhost:8001/api');
  });
});
