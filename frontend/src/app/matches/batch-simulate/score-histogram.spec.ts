import { scoreHistogram } from './score-histogram';

describe('scoreHistogram', () => {
  it('draws nothing for no scores', () => {
    expect(scoreHistogram([], [])).toEqual({ labels: [], red: [], blue: [] });
  });

  it('puts every score below 5,000 in one bin', () => {
    expect(scoreHistogram([0, 4999], [1200])).toEqual({
      labels: ['0–5,000'],
      red: [2],
      blue: [1],
    });
  });

  it('puts a score on a bin edge in the higher bin, and the top score in the last bin', () => {
    expect(scoreHistogram([5000, 12000], [4999])).toEqual({
      labels: ['0–5,000', '5,000–10,000', '10,000–15,000'],
      red: [0, 1, 1],
      blue: [1, 0, 0],
    });
  });

  it('bins both Teams over the same range, set by the highest score of either', () => {
    const hist = scoreHistogram([1000], [21000, 26000]);
    expect(hist.labels).toHaveLength(6);
    expect(hist.labels[5]).toBe('25,000–30,000');
    expect(hist.red).toEqual([1, 0, 0, 0, 0, 0]);
    expect(hist.blue).toEqual([0, 0, 0, 0, 1, 1]);
  });

  it('counts a negative score (penalties) in the first bin', () => {
    expect(scoreHistogram([-300, 6000], [2000])).toEqual({
      labels: ['0–5,000', '5,000–10,000'],
      red: [1, 1],
      blue: [1, 0],
    });
  });

  it('keeps one bin when every score is negative', () => {
    expect(scoreHistogram([-6000], [-12000])).toEqual({
      labels: ['0–5,000'],
      red: [1],
      blue: [1],
    });
  });

  it('bins one Team alone when the other has no scores', () => {
    expect(scoreHistogram([], [7000])).toEqual({
      labels: ['0–5,000', '5,000–10,000'],
      red: [0, 0],
      blue: [0, 1],
    });
  });
});
