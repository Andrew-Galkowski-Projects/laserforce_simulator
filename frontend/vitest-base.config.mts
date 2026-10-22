// Vitest options the `@angular/build:unit-test` builder merges in (`runnerConfig` in
// angular.json); the builder owns everything else (jsdom, includes, the Angular plugin).
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Vitest's 5 s default is too tight for the heavier route specs (a full Material form
    // through the router) once the whole suite runs in parallel: player-add's first test
    // took 5.7-6.2 s in full runs on the ANG-06c branch while passing alone. A real hang
    // still fails, just later.
    testTimeout: 15000,
  },
});
