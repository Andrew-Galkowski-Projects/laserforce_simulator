import { spacedText } from './spaced-text';

describe('spacedText', () => {
  function el(html: string): HTMLElement {
    const div = document.createElement('div');
    div.innerHTML = html;
    return div;
  }

  it('puts one space between sibling elements, where textContent runs them together', () => {
    const card = el('<h2>Sandbox</h2><p>Quick one-off matches.</p>');
    expect(card.textContent).toBe('SandboxQuick one-off matches.');
    expect(spacedText(card)).toBe('Sandbox Quick one-off matches.');
  });

  it('collapses inner whitespace and drops whitespace-only text nodes', () => {
    expect(spacedText(el('\n  <span> Boom. </span>\n  <button>Retry</button>\n'))).toBe(
      'Boom. Retry',
    );
  });

  it('is empty for a missing element', () => {
    expect(spacedText(null)).toBe('');
    expect(spacedText(undefined)).toBe('');
  });
});
