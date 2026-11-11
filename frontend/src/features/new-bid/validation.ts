import { MAX_TEXT_CHARS, MAX_UPLOAD_BYTES, MAX_UPLOAD_MB } from "./limits";

/**
 * Cheap, client-side input validation (SPEC §6.1 "ingest guardrails"
 * covers the real server-side checks). We check extension + size +
 * emptiness; deeper checks (encrypted / too many pages) land on the
 * server, which returns the honest reason.
 */
export type PdfValidationError =
  | { kind: "wrong_extension" }
  | { kind: "too_large"; sizeMb: number }
  | { kind: "empty" };

export function validatePdfFile(file: File): PdfValidationError | null {
  const name = file.name.toLowerCase();
  if (!name.endsWith(".pdf")) return { kind: "wrong_extension" };
  if (file.size === 0) return { kind: "empty" };
  if (file.size > MAX_UPLOAD_BYTES) {
    // Round UP to 0.1 MB so a 20.04 MB file shows "20.1 MB", never
    // "20.0 MB" — "the max is 20 MB" against a reported "20.0 MB"
    // reads like a false positive.
    const sizeMb = Math.ceil((file.size / (1024 * 1024)) * 10) / 10;
    return { kind: "too_large", sizeMb };
  }
  return null;
}

export function pdfErrorMessage(err: PdfValidationError): string {
  switch (err.kind) {
    case "wrong_extension":
      return "Only PDF files are accepted.";
    case "too_large":
      return `File is ${err.sizeMb} MB — the maximum is ${MAX_UPLOAD_MB} MB.`;
    case "empty":
      return "This file is empty.";
  }
}

/**
 * Optional magic-byte check. A real PDF begins with the five ASCII
 * bytes `%PDF-` (SPEC §6.1 says "check magic bytes start with `%PDF-`,
 * not the extension"). We do the cheap extension check synchronously;
 * callers that want the stronger check can `await` this before upload.
 */
export async function hasPdfMagicBytes(file: File): Promise<boolean> {
  if (file.size < 5) return false;
  const head = await file.slice(0, 5).arrayBuffer();
  const bytes = new Uint8Array(head);
  return (
    bytes[0] === 0x25 && // %
    bytes[1] === 0x50 && // P
    bytes[2] === 0x44 && // D
    bytes[3] === 0x46 && // F
    bytes[4] === 0x2d //   -
  );
}

/**
 * Code-point length (Python's `len(s)`). JS `.length` counts UTF-16
 * code units, so "🪵".length === 2 — two surrogates for one emoji.
 * The backend checks `len(text) <= MAX_TEXT_CHARS`, so we must count
 * the same way or we'll reject text the server would accept.
 */
export function codePointLength(text: string): number {
  // Array.from iterates by code point, not UTF-16 code unit.
  // O(n) and safe for very long strings — the cap is 50k anyway.
  return Array.from(text).length;
}

export type TextValidationError =
  | { kind: "too_long"; count: number }
  | { kind: "empty" };

export function validatePastedText(text: string): TextValidationError | null {
  if (text.trim() === "") return { kind: "empty" };
  const count = codePointLength(text);
  if (count > MAX_TEXT_CHARS) return { kind: "too_long", count };
  return null;
}

export function textErrorMessage(err: TextValidationError): string {
  switch (err.kind) {
    case "empty":
      return "Paste something to get started.";
    case "too_long":
      return `${err.count.toLocaleString()} characters — the maximum is ${MAX_TEXT_CHARS.toLocaleString()}.`;
  }
}
