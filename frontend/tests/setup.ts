import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom doesn't implement these APIs; cmdk and some Radix primitives
// touch them in effect hooks. Stub with no-op implementations so our
// component tests can mount them.
if (typeof globalThis.ResizeObserver === "undefined") {
  class ResizeObserverStub {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  }
  (globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver =
    ResizeObserverStub;
}

// Tests that opt into `// @vitest-environment node` have no DOM
// globals. Keep everything browser-only inside this block so those
// test files load cleanly.
if (typeof window !== "undefined") {
  if (typeof globalThis.Element !== "undefined") {
    const proto = Element.prototype as unknown as { scrollIntoView?: unknown };
    if (typeof proto.scrollIntoView === "undefined") {
      (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = () => {};
    }
  }

  // Guarded window.matchMedia — jsdom omits it; some code paths
  // (startThemeSync, media-query hooks) call it unconditionally.
  if (typeof window.matchMedia === "undefined") {
    const makeMql = (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        // Legacy listener API still referenced by some libraries.
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }) as unknown as MediaQueryList;
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      configurable: true,
      value: (query: string) => makeMql(query),
    });
  }

  // Keep test isolation tight: unmount components, drop persisted
  // shortcut-toggle state, and reset the history URL between tests.
  afterEach(() => {
    cleanup();
    try {
      window.localStorage.clear();
    } catch {
      /* ignore */
    }
    history.replaceState({}, "", "/");
  });
}
