// Spec-only routes for the auth flow specs. Imported only by *.spec.ts files.
import { Component } from '@angular/core';
import { Routes } from '@angular/router';

@Component({ selector: 'app-flow-page', template: '' })
class FlowPage {}

/**
 * Any URL resolves, unguarded, so the auth specs see exactly where the interceptor and
 * `AuthService` navigate. The app's own routes (and their guards) are covered by app.routes.spec.ts.
 */
export const FLOW_ROUTES: Routes = [{ path: '**', component: FlowPage }];
