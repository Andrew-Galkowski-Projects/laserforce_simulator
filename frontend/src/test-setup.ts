// Vitest setup (angular.json `test.options.setupFiles`), run before every spec file.
import { TestBed } from '@angular/core/testing';

// Angular resets the TestBed in a global afterEach. A spec's own afterEach that throws
// (`http.verify()` with a request left open) skips it, and every later test in the file
// then fails with "Cannot configure the test module when the test module has already been
// instantiated" (ANG-06-11). Resetting before each test keeps the failure on the test
// that caused it.
beforeEach(() => TestBed.resetTestingModule());
