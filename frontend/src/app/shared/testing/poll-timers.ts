// Spec helper. Imported only by *.spec.ts files.

/**
 * What a polling spec fakes, after the first render: the interval rxjs `timer` schedules on, and
 * `requestAnimationFrame` with it. Faking `setInterval` alone lets a frame requested meanwhile
 * (Angular's scheduler, Material, Chart.js) start jsdom's frame loop on the fake interval: the
 * Angular test builder shares one jsdom per worker, so every later spec there that waits on a
 * real frame hangs (ANG-06-13b's CI: map-editor's canvas host timed out after batch-simulate).
 * `setTimeout` stays real, so zoneless `whenStable` still settles.
 */
export const POLL_TIMERS: (
  'setInterval' | 'clearInterval' | 'requestAnimationFrame' | 'cancelAnimationFrame'
)[] = ['setInterval', 'clearInterval', 'requestAnimationFrame', 'cancelAnimationFrame'];
