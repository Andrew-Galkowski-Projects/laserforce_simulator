import { readToken, resolveColor } from './read-token';

describe('readToken', () => {
  afterEach(() => document.documentElement.style.removeProperty('--lf-test-token'));

  it("reads a token's value on the document root, trimmed", () => {
    document.documentElement.style.setProperty('--lf-test-token', '  rgb(1, 2, 3) ');
    expect(readToken('--lf-test-token')).toBe('rgb(1, 2, 3)');
  });

  it('reads an unset token as an empty string', () => {
    expect(readToken('--lf-test-token')).toBe('');
  });
});

describe('resolveColor', () => {
  it('returns the colour as the browser computes it', () => {
    const host = document.createElement('div');
    document.body.appendChild(host);
    expect(resolveColor('rgb(10, 20, 30)', host)).toBe('rgb(10, 20, 30)');
    host.remove();
  });

  it('returns a colour the browser cannot compute as given, and leaves no probe behind', () => {
    const host = document.createElement('div');
    expect(resolveColor('not-a-colour', host)).toBe('not-a-colour');
    expect(host.childElementCount).toBe(0);
  });
});
