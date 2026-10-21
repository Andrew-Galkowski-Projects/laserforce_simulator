// Agent worktrees: Django on 8001 and `ng serve` on 4201, so the owner's 8000 / 4200 stay theirs.
// Swapped in by the `worktree` build configuration (`npm --prefix frontend run start:worktree`).
export const environment = {
  production: false,
  apiBaseUrl: 'http://localhost:8001/api',
};
