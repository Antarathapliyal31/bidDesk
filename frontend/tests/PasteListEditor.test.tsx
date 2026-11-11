/**
 * PasteListEditor (SPEC §11.4):
 *   - the textarea has an accessible name "Materials list".
 *   - over the limit → aria-invalid="true" and the alert text is
 *     FIXED across keystrokes so assistive tech doesn't re-announce
 *     a new phrasing per character.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { MAX_TEXT_CHARS } from "../src/features/new-bid/limits";
import { PasteListEditor } from "../src/features/new-bid/PasteListEditor";

function Harness({ initial = "" }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  return <PasteListEditor value={value} onChange={setValue} />;
}

describe("PasteListEditor", () => {
  it("is found by role=textbox with name 'Materials list'", () => {
    render(<Harness />);
    expect(screen.getByRole("textbox", { name: /materials list/i })).toBeInTheDocument();
  });

  it("is not aria-invalid for an in-bounds paste", () => {
    render(<Harness initial={"a".repeat(100)} />);
    const ta = screen.getByRole("textbox", { name: /materials list/i });
    // aria-invalid attribute is absent (undefined) when valid.
    expect(ta.getAttribute("aria-invalid")).toBeNull();
  });

  it("over the limit → aria-invalid='true' and the alert text is stable across keystrokes", () => {
    render(<Harness initial={"a".repeat(MAX_TEXT_CHARS + 1)} />);
    const ta = screen.getByRole("textbox", { name: /materials list/i });
    expect(ta).toHaveAttribute("aria-invalid", "true");
    const firstAlert = screen.getByRole("alert").textContent;
    expect(firstAlert).toMatch(/over the .*-character limit/i);
    // Type more characters; the alert phrasing stays identical.
    fireEvent.change(ta, { target: { value: "a".repeat(MAX_TEXT_CHARS + 50) } });
    expect(screen.getByRole("alert").textContent).toBe(firstAlert);
  });
});
