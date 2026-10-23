import { tickClock } from './clock';

describe('tickClock', () => {
  it('reads m:ss by default, at two ticks a second', () => {
    expect(tickClock(0)).toBe('0:00');
    expect(tickClock(245)).toBe('2:02');
    expect(tickClock(1800)).toBe('15:00');
  });

  it('zero-pads the minutes to mm:ss with pad', () => {
    expect(tickClock(0, { pad: true })).toBe('00:00');
    expect(tickClock(61, { pad: true })).toBe('00:30');
    expect(tickClock(1800, { pad: true })).toBe('15:00');
  });

  it('drops a half-second tick rather than rounding it up', () => {
    expect(tickClock(119)).toBe('0:59');
  });
});
