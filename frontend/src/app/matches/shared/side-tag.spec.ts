import { sideTag } from './side-tag';

describe('sideTag', () => {
  it('tags Red as R and Blue as B', () => {
    expect(sideTag('red')).toBe('R');
    expect(sideTag('blue')).toBe('B');
  });
});
