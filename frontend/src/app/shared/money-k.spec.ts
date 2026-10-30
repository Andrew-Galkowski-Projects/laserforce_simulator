import { moneyK } from './money-k';

describe('moneyK (spec #439, mirrors the Django money_k filter)', () => {
  it('renders thousands of dollars to one decimal: 7 => $7.0k', () => {
    expect(moneyK(7)).toBe('$7.0k');
    expect(moneyK(7.24)).toBe('$7.2k');
    expect(moneyK(1234.5)).toBe('$1234.5k');
  });

  it('puts the sign before the $: -7.24 => -$7.2k, never $-7.2k', () => {
    expect(moneyK(-7.24)).toBe('-$7.2k');
  });

  it('rounds half up: 0.05 => $0.1k, 7.25 => $7.3k, -7.25 => -$7.3k', () => {
    expect(moneyK(0.05)).toBe('$0.1k');
    expect(moneyK(7.25)).toBe('$7.3k');
    expect(moneyK(-7.25)).toBe('-$7.3k');
  });

  it('rounds the value as written, as Decimal(str(value)) does: 1.45 => $1.5k, 0.04999999999999999 => $0.0k', () => {
    expect(moneyK(1.45)).toBe('$1.5k');
    expect(moneyK(-1.45)).toBe('-$1.5k');
    expect(moneyK(4.35)).toBe('$4.4k');
    // Just under a half stays under: no precision trimming may round it up.
    expect(moneyK(0.04999999999999999)).toBe('$0.0k');
  });

  it('drops the sign of a value that rounds to zero: -0.04 => $0.0k', () => {
    expect(moneyK(-0.04)).toBe('$0.0k');
    expect(moneyK(0)).toBe('$0.0k');
  });
});
