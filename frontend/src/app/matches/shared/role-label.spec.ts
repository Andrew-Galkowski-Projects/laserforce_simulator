import { roleLabel } from './role-label';

describe('roleLabel', () => {
  it('reads each role as its short label', () => {
    expect(roleLabel('commander')).toBe('CMD');
    expect(roleLabel('heavy')).toBe('HVY');
    expect(roleLabel('scout')).toBe('SCT');
    expect(roleLabel('ammo')).toBe('AMO');
    expect(roleLabel('medic')).toBe('MED');
  });

  it('matches the role case-insensitively', () => {
    expect(roleLabel('Commander')).toBe('CMD');
    expect(roleLabel('MEDIC')).toBe('MED');
  });

  it('falls back to the upper-cased first three letters of an unknown role', () => {
    expect(roleLabel('sniper')).toBe('SNI');
  });
});
