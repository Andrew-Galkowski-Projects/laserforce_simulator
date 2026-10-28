import { poolSizeInvalid } from './pool-size';

describe('poolSizeInvalid', () => {
  it.each([
    [0, true],
    [18, true],
    [23, true],
    [24, false],
    [25, true],
    [30, false],
  ])('pool size %i -> invalid %s', (size, invalid) => {
    expect(poolSizeInvalid(size)).toBe(invalid);
  });
});
