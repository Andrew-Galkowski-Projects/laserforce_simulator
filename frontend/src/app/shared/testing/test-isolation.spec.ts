// Pins vitest-base.config.mts's isolation guard: `unstubGlobals` and `restoreMocks` undo every
// stub and spy before the next test, so none can leak into a later spec sharing the worker
// (RV-07b's matchMedia stub failed 329 later tests at CI's core count). The tests run in order:
// each "leaves" test plants something, and the next one checks it is gone.
describe('test isolation (vitest-base.config.mts)', () => {
  it('leaves a stubbed global behind', () => {
    vi.stubGlobal('lfLeakProbe', 'stubbed');
    expect((globalThis as Record<string, unknown>)['lfLeakProbe']).toBe('stubbed');
  });

  it('starts the next test without it', () => {
    expect((globalThis as Record<string, unknown>)['lfLeakProbe']).toBeUndefined();
  });

  it('leaves a spy on a global function behind', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5);
    expect(vi.isMockFunction(Math.random)).toBe(true);
  });

  it('starts the next test with the real function', () => {
    expect(vi.isMockFunction(Math.random)).toBe(false);
  });
});
