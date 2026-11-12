/**
 * SourcePanelContext: opening sets state, closing clears `open`
 * (but keeps the last source/pdfUrl for the exit animation), and
 * a second `openSource` replaces the current pair.
 */
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SourceRef } from "@/api";
import {
  SourcePanelProvider,
  useSourcePanel,
} from "@/features/run/source/SourcePanelContext";

function wrapper({ children }: { children: React.ReactNode }) {
  return <SourcePanelProvider>{children}</SourcePanelProvider>;
}

const SRC_A: SourceRef = { page: 7, quote: "A quote", read_via: "text", verified: true };
const SRC_B: SourceRef = { page: 12, quote: "B quote", read_via: "vision", verified: false };

describe("useSourcePanel", () => {
  it("starts closed with no source", () => {
    const { result } = renderHook(() => useSourcePanel(), { wrapper });
    expect(result.current.open).toBe(false);
    expect(result.current.source).toBeNull();
    expect(result.current.pdfUrl).toBeNull();
  });

  it("openSource(source, pdfUrl) opens and records both", () => {
    const { result } = renderHook(() => useSourcePanel(), { wrapper });
    act(() => result.current.openSource(SRC_A, "/samples/rfp_messy.pdf"));
    expect(result.current.open).toBe(true);
    expect(result.current.source).toEqual(SRC_A);
    expect(result.current.pdfUrl).toBe("/samples/rfp_messy.pdf");
  });

  it("close() flips open to false but retains the last source/pdfUrl (exit animation)", () => {
    const { result } = renderHook(() => useSourcePanel(), { wrapper });
    act(() => result.current.openSource(SRC_A, "/x.pdf"));
    act(() => result.current.close());
    expect(result.current.open).toBe(false);
    expect(result.current.source).toEqual(SRC_A);
    expect(result.current.pdfUrl).toBe("/x.pdf");
  });

  it("a second openSource replaces the current pair (no stacking)", () => {
    const { result } = renderHook(() => useSourcePanel(), { wrapper });
    act(() => result.current.openSource(SRC_A, "/a.pdf"));
    act(() => result.current.openSource(SRC_B, null));
    expect(result.current.source).toEqual(SRC_B);
    expect(result.current.pdfUrl).toBeNull();
  });

  it("throws outside a provider", () => {
    // Suppress the expected console.error noise from React boundary.
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => renderHook(() => useSourcePanel())).toThrow(/SourcePanelProvider/);
    spy.mockRestore();
  });
});
