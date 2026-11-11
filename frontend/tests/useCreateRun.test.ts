// @vitest-environment node
/**
 * `toCreateRunFormData` is the only piece of `useCreateRun` that
 * ships over the wire. The Phase-5 fetch call reuses it verbatim.
 */
import { describe, expect, it } from "vitest";

import { toCreateRunFormData } from "../src/api/useCreateRun";

describe("toCreateRunFormData", () => {
  it("serializes the PDF variant: file, branch_id, faults as JSON", () => {
    const file = new File([new Uint8Array(4)], "rfp.pdf", { type: "application/pdf" });
    const fd = toCreateRunFormData({
      file,
      branch_id: "BR-NJ-01",
      faults: ["pricing_timeout_once"],
    });
    expect(fd.get("file")).toBeInstanceOf(File);
    expect((fd.get("file") as File).name).toBe("rfp.pdf");
    expect(fd.get("branch_id")).toBe("BR-NJ-01");
    expect(fd.get("faults")).toBe('["pricing_timeout_once"]');
    expect(fd.get("text")).toBeNull();
  });

  it("serializes the paste variant without a 'faults' key when the list is empty", () => {
    // SPEC §10.6 + API_CONTRACT: "faults absent or [] is always
    // accepted; non-empty requires demo_faults_enabled." Sending "[]"
    // when the flag is off would risk a spurious server rejection,
    // so we omit the key instead.
    const fd = toCreateRunFormData({
      text: "2x6x16 PT #2, 40 pcs",
      branch_id: "BR-NY-01",
      faults: [],
    });
    expect(fd.get("text")).toBe("2x6x16 PT #2, 40 pcs");
    expect(fd.get("file")).toBeNull();
    expect(fd.get("branch_id")).toBe("BR-NY-01");
    expect(fd.get("faults")).toBeNull();
    expect(fd.has("faults")).toBe(false);
  });

  it("serializes multiple faults in a single JSON array", () => {
    const fd = toCreateRunFormData({
      text: "x",
      branch_id: "BR-PA-01",
      faults: ["pricing_timeout_once", "analyst_malformed_once"],
    });
    expect(JSON.parse(fd.get("faults") as string)).toEqual([
      "pricing_timeout_once",
      "analyst_malformed_once",
    ]);
  });
});
