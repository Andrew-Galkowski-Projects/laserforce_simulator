// The Batch run's score distribution, ported from `batch_simulate.html`'s `buildHistogram`.

/** Each bin's width in points. */
export const SCORE_BIN_SIZE = 5000;

/** Bin labels ("0–5,000") and each Team's count per bin, over one shared range. */
export interface ScoreHistogram {
  labels: string[];
  red: number[];
  blue: number[];
}

const points = (n: number): string => n.toLocaleString('en-US');

/** A score's bin; a negative score (penalties can take a Team below 0) counts in the first. */
const binOf = (score: number): number => Math.max(0, Math.floor(score / SCORE_BIN_SIZE));

/**
 * Both Teams' scores binned by `SCORE_BIN_SIZE`, from 0 up to the bin holding the highest
 * score of either (at least one bin); a score on a bin edge goes to the higher bin. No scores
 * give no bins.
 */
export function scoreHistogram(red: readonly number[], blue: readonly number[]): ScoreHistogram {
  const all = [...red, ...blue];
  if (all.length === 0) return { labels: [], red: [], blue: [] };
  const bins = binOf(Math.max(...all)) + 1;
  const count = (scores: readonly number[]): number[] => {
    const out = new Array<number>(bins).fill(0);
    // No score passes the highest, so none lands past the last bin.
    for (const score of scores) out[binOf(score)] += 1;
    return out;
  };
  const labels = Array.from({ length: bins }, (_, i) => {
    const lo = i * SCORE_BIN_SIZE;
    return `${points(lo)}–${points(lo + SCORE_BIN_SIZE)}`;
  });
  return { labels, red: count(red), blue: count(blue) };
}
