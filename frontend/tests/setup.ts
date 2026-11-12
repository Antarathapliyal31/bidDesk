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
    const proto = Element.prototype as unknown as {
      scrollIntoView?: unknown;
      hasPointerCapture?: unknown;
      setPointerCapture?: unknown;
      releasePointerCapture?: unknown;
    };
    if (typeof proto.scrollIntoView === "undefined") {
      (Element.prototype as unknown as { scrollIntoView: () => void }).scrollIntoView = () => {};
    }
    // Radix Select + Dropdown call these in pointer handlers; jsdom
    // doesn't implement the Pointer Events API. No-op stubs are
    // enough for component tests that just want to open/close
    // Radix popovers.
    if (typeof proto.hasPointerCapture === "undefined") {
      (Element.prototype as unknown as { hasPointerCapture: () => boolean }).hasPointerCapture =
        () => false;
    }
    if (typeof proto.setPointerCapture === "undefined") {
      (Element.prototype as unknown as { setPointerCapture: () => void }).setPointerCapture =
        () => {};
    }
    if (typeof proto.releasePointerCapture === "undefined") {
      (Element.prototype as unknown as { releasePointerCapture: () => void }).releasePointerCapture =
        () => {};
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
