// Spec-only: jsdom has no canvas and no ResizeObserver, both of which Chart.js needs. These stubs
// let a real `Chart` be built (every drawing call is a no-op) so a spec can read it back.

/** A 2D context for `canvas` whose every method is a no-op (`measureText` measures 0). */
function noOpContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const store: Record<string | symbol, unknown> = {};
  return new Proxy(store, {
    get(target, prop) {
      if (prop === 'canvas') return canvas;
      if (prop === 'measureText') return () => ({ width: 0 });
      if (prop in target) return target[prop];
      return () => undefined;
    },
    set(target, prop, value) {
      target[prop] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
}

/** A `ResizeObserver` that never reports. */
class SilentResizeObserver {
  observe(): void {
    return;
  }
  unobserve(): void {
    return;
  }
  disconnect(): void {
    return;
  }
}

/**
 * Makes `getContext('2d')` return a no-op context and installs a silent `ResizeObserver`
 * when jsdom has none. Returns a function that undoes both.
 */
export function stubCanvasContext(): () => void {
  const original = HTMLCanvasElement.prototype.getContext;
  const hadResizeObserver = 'ResizeObserver' in window;
  HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement) {
    return noOpContext(this);
  } as unknown as typeof original;
  if (!hadResizeObserver) {
    Object.defineProperty(window, 'ResizeObserver', {
      configurable: true,
      writable: true,
      value: SilentResizeObserver,
    });
  }
  return () => {
    HTMLCanvasElement.prototype.getContext = original;
    if (!hadResizeObserver) delete (window as { ResizeObserver?: unknown }).ResizeObserver;
  };
}
