/**
 * PdfDropzone behavior (SPEC §11.4 "invalid file" state + SPEC §6.1):
 *   - invalid file surfaces an inline error AND clears the <input>
 *     so the same bad file can be re-picked.
 *   - valid pick focuses the Remove button (so Tab moves forward).
 *   - Remove returns focus to the file input.
 *   - dropping more than one file surfaces a clear error.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { PdfDropzone } from "../src/features/new-bid/PdfDropzone";

function Harness({ initial = null }: { initial?: File | null }) {
  const [file, setFile] = useState<File | null>(initial);
  return <PdfDropzone file={file} onChange={setFile} />;
}

function mkPdf(name: string, sizeBytes: number): File {
  const f = new File([""], name, { type: "application/pdf" });
  Object.defineProperty(f, "size", { value: sizeBytes, configurable: true });
  return f;
}

function fileInput(): HTMLInputElement {
  // The sr-only <input type="file"> doesn't have an accessible name;
  // we query by selector within the current container.
  const input = document.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error("file input not found");
  return input;
}

describe("PdfDropzone", () => {
  it("rejects a non-PDF and clears the input value so the same bad file can be re-tried", () => {
    render(<Harness />);
    const input = fileInput();
    const docx = new File(["x"], "rfp.docx", { type: "application/vnd.ms-word" });
    // Use fireEvent.change directly — user-event filters by the
    // input's `accept` attribute, which would silently drop our bad
    // file before the component ever sees it.
    fireEvent.change(input, { target: { files: [docx] } });
    expect(screen.getByRole("alert")).toHaveTextContent(/only pdf/i);
    expect(input.value).toBe("");
  });

  it("rejects an oversize PDF and reports the limit", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const huge = mkPdf("huge.pdf", 21 * 1024 * 1024);
    await user.upload(fileInput(), huge);
    expect(screen.getByRole("alert")).toHaveTextContent(/21\.?\d? MB/i);
    expect(screen.getByRole("alert")).toHaveTextContent(/20 MB/);
  });

  it("rejects a 2-file drop with 'Drop one PDF at a time'", () => {
    render(<Harness />);
    const a = mkPdf("a.pdf", 1024);
    const b = mkPdf("b.pdf", 1024);
    const dropTarget = fileInput().closest("label");
    expect(dropTarget).not.toBeNull();
    fireEvent.drop(dropTarget as HTMLElement, {
      dataTransfer: {
        files: [a, b],
      },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(/one PDF at a time/i);
  });

  it("focuses the Remove button after a valid pick", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const f = mkPdf("rfp.pdf", 1024);
    await user.upload(fileInput(), f);
    const removeBtn = await screen.findByRole("button", { name: `Remove rfp.pdf` });
    await waitFor(() => expect(document.activeElement).toBe(removeBtn));
  });

  it("returns focus to the file input after Remove", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const f = mkPdf("rfp.pdf", 1024);
    await user.upload(fileInput(), f);
    const removeBtn = await screen.findByRole("button", { name: `Remove rfp.pdf` });
    await user.click(removeBtn);
    await waitFor(() => expect(document.activeElement).toBe(fileInput()));
  });
});
