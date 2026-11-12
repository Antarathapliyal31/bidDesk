// @vitest-environment node
/**
 * Pure quote-highlight helpers. The `customTextRenderer` in
 * PdfViewerImpl wires these together; the logic lives here so it
 * can be unit tested without pdfjs.
 */
import { describe, expect, it } from "vitest";

import { escapeHtml, matchesQuote, normalizeQuote } from "@/lib/quote";

describe("normalizeQuote", () => {
  it("collapses runs of whitespace to single spaces and trims", () => {
    expect(normalizeQuote("  2x4  x\t8' SPF  #2  ")).toBe("2x4 x 8' spf #2");
  });

  it("lowercases", () => {
    expect(normalizeQuote("ABC def")).toBe("abc def");
  });

  it("keeps punctuation", () => {
    expect(normalizeQuote("SPF#2, 8'")).toBe("spf#2, 8'");
  });

  it("empty string → empty string", () => {
    expect(normalizeQuote("")).toBe("");
  });
});

describe("matchesQuote", () => {
  const q = normalizeQuote("2x4 x 8' SPF #2, 100 pcs");

  it("returns true for a mid-quote substring (case/whitespace-insensitive)", () => {
    expect(matchesQuote("SPF #2", q)).toBe(true);
    expect(matchesQuote("  spf  #2 ", q)).toBe(true);
    expect(matchesQuote("100 PCS", q)).toBe(true);
  });

  it("returns false when the item isn't in the quote", () => {
    expect(matchesQuote("yellow pine", q)).toBe(false);
  });

  it("filters out items shorter than minLen (default 3) to avoid noise matches", () => {
    expect(matchesQuote("x", q)).toBe(false);
    expect(matchesQuote("# ", q)).toBe(false);
    expect(matchesQuote(",", q)).toBe(false);
  });

  it("honours a caller-supplied minLen", () => {
    expect(matchesQuote("x 8", q, 5)).toBe(false);
    expect(matchesQuote("x 8", q, 3)).toBe(true);
  });

  it("returns false when the quote is empty (nothing to match against)", () => {
    expect(matchesQuote("anything", "")).toBe(false);
  });
});

describe("escapeHtml", () => {
  it("escapes the five dangerous characters", () => {
    expect(escapeHtml(`<a href="x">Tom & Jerry's</a>`)).toBe(
      "&lt;a href=&quot;x&quot;&gt;Tom &amp; Jerry&#39;s&lt;/a&gt;",
    );
  });

  it("is a no-op for text with none of the five chars", () => {
    expect(escapeHtml("2x4 x 8 SPF #2")).toBe("2x4 x 8 SPF #2");
  });
});
