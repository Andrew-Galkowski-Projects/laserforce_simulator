// Spec helper. Imported only by *.spec.ts files.

/**
 * An element's rendered text: its text nodes, whitespace collapsed, joined by one space. jsdom's
 * `textContent` runs sibling elements together ("SandboxQuick one-off..."), and jsdom has no
 * `innerText`. `''` for a missing element.
 */
export function spacedText(el: Element | null | undefined): string {
  if (!el) return '';
  const walker = el.ownerDocument.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const parts: string[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const part = node.textContent?.replace(/\s+/g, ' ').trim();
    if (part) parts.push(part);
  }
  return parts.join(' ');
}
