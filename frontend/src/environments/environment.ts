// Dev settings. `ng build` (production) swaps in environment.prod.ts via
// fileReplacements in angular.json. This pair is the only place an API host may
// appear: services build URLs from `environment.apiBaseUrl`.
export const environment = {
  production: false,
  apiBaseUrl: 'http://localhost:8000/api',
};
