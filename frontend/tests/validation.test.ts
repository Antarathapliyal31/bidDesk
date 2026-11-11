// @vitest-environment node
/**
 * Input validation for the New bid page. MAX_TEXT_CHARS is a
 * code-point count (matches Python's `len(s)` on the backend); JS
 * `.length` counts UTF-16 code units, so "🪵" would otherwise look
 * like two characters and over-reject at the limit boundary.
 *
 * `file.size` is faked via `Object.defineProperty` so we don't
 * allocate 20 MB buffers for size-threshold assertions.
 */
import { describe, expect, it } from "vitest";

import { MAX_TEXT_CHARS, MAX_UPLOAD_BYTES, MAX_UPLOAD_MB } from "../src/features/new-bid/limits";
import {
  codePointLength,
  pdfErrorMessage,
  validatePastedText,
  validatePdfFile,
} from "../src/features/new-bid/validation";

function mkFile(name: string, sizeBytes: number): File {
  const file = new File([""], name, { type: "application/pdf" });
  Object.defineProperty(file, "size", { value: sizeBytes, configurable: true });
  return file;
}

describe("codePointLength", () => {
  it("returns 1 for a single emoji (not 2 like .length)", () => {
    expect(codePointLength("🪵")).toBe(1);
    expect("🪵".length).toBe(2);
  });

  it("counts a mix of ASCII and astral chars correctly", () => {
    expect(codePointLength("a🪵b")).toBe(3);
    expect("a🪵b".length).toBe(4);
  });

  it("handles the empty string", () => {
    expect(codePointLength("")).toBe(0);
  });
});

describe("validatePastedText", () => {
  it("rejects a whitespace-only paste as empty", () => {
    expect(validatePastedText("")?.kind).toBe("empty");
    expect(validatePastedText("   \n\t  ")?.kind).toBe("empty");
  });

  it("accepts exactly MAX_TEXT_CHARS ASCII", () => {
    const text = "a".repeat(MAX_TEXT_CHARS);
    expect(codePointLength(text)).toBe(MAX_TEXT_CHARS);
    expect(validatePastedText(text)).toBeNull();
  });

  it("rejects MAX_TEXT_CHARS + 1 ASCII", () => {
    const text = "a".repeat(MAX_TEXT_CHARS + 1);
    const err = validatePastedText(text);
    expect(err?.kind).toBe("too_long");
    expect(err?.kind === "too_long" ? err.count : undefined).toBe(MAX_TEXT_CHARS + 1);
  });

  it("accepts an emoji-only paste exactly at the code-point boundary", () => {
    // UTF-16 code units are 2 × MAX_TEXT_CHARS, but we count code
    // points — the server would accept this, so we must too.
    const text = "🪵".repeat(MAX_TEXT_CHARS);
    expect(codePointLength(text)).toBe(MAX_TEXT_CHARS);
    expect(text.length).toBe(MAX_TEXT_CHARS * 2);
    expect(validatePastedText(text)).toBeNull();
  });

  it("rejects when code-point length exceeds MAX_TEXT_CHARS by one", () => {
    const text = "🪵".repeat(MAX_TEXT_CHARS + 1);
    const err = validatePastedText(text);
    expect(err?.kind).toBe("too_long");
    expect(err?.kind === "too_long" ? err.count : undefined).toBe(MAX_TEXT_CHARS + 1);
  });

  it("accepts a normal materials-list paste", () => {
    expect(validatePastedText("2x6x16 PT, 40 pcs\nOSB 7/16, 12 SHT\n")).toBeNull();
  });
});

describe("validatePdfFile", () => {
  it("rejects non-PDF extensions", () => {
    expect(validatePdfFile(mkFile("rfp.docx", 1024))?.kind).toBe("wrong_extension");
  });

  it("rejects a double-extension filename ending in non-pdf", () => {
    expect(validatePdfFile(mkFile("rfp.pdf.docx", 1024))?.kind).toBe("wrong_extension");
  });

  it("rejects a filename with no extension", () => {
    expect(validatePdfFile(mkFile("rfp", 1024))?.kind).toBe("wrong_extension");
  });

  it("accepts an uppercase .PDF extension", () => {
    expect(validatePdfFile(mkFile("RFP.PDF", 1024))).toBeNull();
  });

  it("rejects a zero-byte PDF as empty", () => {
    expect(validatePdfFile(mkFile("rfp.pdf", 0))?.kind).toBe("empty");
    expect(pdfErrorMessage({ kind: "empty" })).toMatch(/empty/i);
  });

  it("accepts a file of exactly MAX_UPLOAD_BYTES", () => {
    expect(validatePdfFile(mkFile("boundary.pdf", MAX_UPLOAD_BYTES))).toBeNull();
  });

  it("rejects a file one byte over MAX_UPLOAD_BYTES", () => {
    const err = validatePdfFile(mkFile("boundary.pdf", MAX_UPLOAD_BYTES + 1));
    expect(err?.kind).toBe("too_large");
    // The over-the-wire size is 20.0000009…; ceil to 0.1 ⇒ 20.1.
    expect(err?.kind === "too_large" ? err.sizeMb : undefined).toBe(MAX_UPLOAD_MB + 0.1);
  });

  it("rounds the reported size UP to 0.1 MB (20.04 MB → '20.1 MB')", () => {
    const bytes = Math.round(20.04 * 1024 * 1024);
    const err = validatePdfFile(mkFile("huge.pdf", bytes));
    expect(err?.kind).toBe("too_large");
    expect(err?.kind === "too_large" ? err.sizeMb : undefined).toBe(20.1);
    expect(pdfErrorMessage({ kind: "too_large", sizeMb: 20.1 })).toContain("20.1 MB");
  });

  it("accepts a well-sized PDF", () => {
    expect(validatePdfFile(mkFile("ok.pdf", 1024 * 1024))).toBeNull();
  });
});
