import { PhaseRow, newPhaseRow, phaseWire } from './phase-wire';

// Every expected string is what `create_advanced.html`'s `serialize()` (:442-496) writes into
// the hidden `phases` field for the same rows; the line each rule comes from is cited per case.
describe('phaseWire', () => {
  const row = (overrides: Partial<PhaseRow> = {}): PhaseRow => ({ ...newPhaseRow(), ...overrides });

  it('starts a row as the template seeds one: a single round-robin (:505-508, :214-439)', () => {
    expect(phaseWire([newPhaseRow()])).toBe('round_robin:single_round_robin');
  });

  it.each<[string, PhaseRow[], string]>([
    // :453-456 "round_robin:" + the schedule-format select's value.
    [
      'a double round-robin',
      [row({ scheduleFormat: 'double_round_robin' })],
      'round_robin:double_round_robin',
    ],
    // :457-459 a bare token, whatever the hidden sub-config holds.
    [
      'a member night',
      [row({ type: 'member_night', scheduleFormat: 'double_round_robin' })],
      'member_night',
    ],
    // :463-492 the 11-field token with the default tournament sub-config (cut 0, best of 1, 0/0
    // advancers outside RR -> DE, swiss 0).
    [
      'a default tournament',
      [row({ type: 'tournament' })],
      'tournament:standings:0:single_elimination:1:1:1:1:0:0:0',
    ],
    // :479-485 the wb/lb combo splits only for round_robin_double_elim.
    [
      'a full RR -> DE tournament token',
      [
        row({
          type: 'tournament',
          mode: 'strength',
          cut: 8,
          format: 'round_robin_double_elim',
          finalSl: 5,
          semiSl: 3,
          quarterSl: 3,
          earlierSl: 1,
          combo: '8/4',
          swissRounds: 2,
        }),
      ],
      'tournament:strength:8:round_robin_double_elim:5:3:3:1:8:4:2',
    ],
    // :477-478 wb / lb stay "0" for any other format, even with a combo picked earlier.
    [
      'a combo left over from RR -> DE on a Swiss tournament',
      [
        row({
          type: 'tournament',
          mode: 'unseeded',
          format: 'swiss',
          combo: '16/8',
          swissRounds: 5,
        }),
      ],
      'tournament:unseeded:0:swiss:1:1:1:1:0:0:5',
    ],
    // :466 and :487 an emptied number input sends "0".
    [
      'emptied cut and Swiss-rounds inputs',
      [row({ type: 'tournament', format: 'swiss', cut: null, swissRounds: null })],
      'tournament:standings:0:swiss:1:1:1:1:0:0:0',
    ],
    // :495 tokens joined by commas, in row order.
    [
      'three rows in order',
      [
        row(),
        row({ type: 'member_night' }),
        row({ type: 'tournament', format: 'double_elimination' }),
      ],
      'round_robin:single_round_robin,member_night,' +
        'tournament:standings:0:double_elimination:1:1:1:1:0:0:0',
    ],
    // :446-495 no rows: an empty string (the Form then falls back to one round-robin).
    ['no rows', [], ''],
  ])('serialises %s', (_name, rows, wire) => {
    expect(phaseWire(rows)).toBe(wire);
  });
});
