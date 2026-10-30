import {
  RATING_BIO_HEADERS,
  RATING_SORT_COLUMNS,
  RATING_STAT_KEYS,
  STAT_COLUMNS,
  orDash,
  potentialText,
  statCell,
  truncateChars,
} from './player-columns';

describe('player-columns (spec #428 F7)', () => {
  it("has Player Ratings' 24 sortable headers in RATING_SORT_KEYS_DISPLAY order", () => {
    expect(RATING_SORT_COLUMNS.map((c) => c.label)).toEqual([
      'Name',
      'Team',
      'Roles',
      'Ovr',
      'PlAw',
      'GmAw',
      'RsAw',
      'Dec',
      'Pos',
      'Sta',
      'Spd',
      'Flx',
      'Adp',
      'Comm',
      'Team',
      'OffSyn',
      'DefSyn',
      'MidSyn',
      'RsupSyn',
      'RsupEff',
      'Acc',
      'Surv',
      'SpcUse',
      'Pot',
    ]);
    expect(RATING_SORT_COLUMNS[3]).toEqual({
      key: 'overall_rating',
      label: 'Ovr',
      full: 'Overall',
    });
  });

  it('takes the 19 rating stats from between Ovr and Pot', () => {
    expect(RATING_STAT_KEYS).toHaveLength(19);
    expect(RATING_STAT_KEYS[0]).toBe('player_awareness');
    expect(RATING_STAT_KEYS.at(-1)).toBe('special_usage');
  });

  it('has the five bio headers after Pot', () => {
    expect(RATING_BIO_HEADERS.map((h) => `${h.label}:${h.full}`)).toEqual([
      'Age:Age',
      'Site:Home Site',
      'Ht:Height',
      'GP:Games Played',
      'Start:Started Playing Age',
    ]);
  });

  it("has Player Stats' 14 stat columns after Name / Team / GP", () => {
    expect(STAT_COLUMNS.map((c) => c.label)).toEqual([
      'Points',
      'MVP',
      'Tags',
      'Tagged',
      'Tag Ratio',
      'Acc%',
      'Survival',
      'Lives',
      'Resup',
      'Missiles',
      'Specials',
      'Follow-up',
      'Reaction',
      'Combo Resup',
    ]);
  });

  it('formats a stat cell to its places and suffix, a missing stat as 0', () => {
    const survival = STAT_COLUMNS.find((c) => c.key === 'survival')!;
    const ratio = STAT_COLUMNS.find((c) => c.key === 'tag_ratio')!;
    expect(statCell({ survival: 612.6 }, survival)).toBe('613s');
    expect(statCell({ tag_ratio: 1.864 }, ratio)).toBe('1.86');
    expect(statCell({}, ratio)).toBe('0.00');
  });

  it('shows Potential to one place, or "—" when absent', () => {
    expect(potentialText(80.56)).toBe('80.6');
    expect(potentialText(null)).toBe('—');
  });

  it('shows "-" for a missing number or an empty string', () => {
    expect(orDash(24)).toBe('24');
    expect(orDash(null)).toBe('-');
    expect(orDash('')).toBe('-');
    expect(orDash(0)).toBe('0');
  });

  it("truncates like Django's truncatechars", () => {
    expect(truncateChars('Laserforce Ventura', 14)).toBe('Laserforce Ve…');
    expect(truncateChars('Ventura', 14)).toBe('Ventura');
    expect(truncateChars('Laserforce Ven', 14)).toBe('Laserforce Ven');
  });
});
