// @vitest-environment node
import { describe, expect, it } from "vitest";

import { formatTaxRate } from "../src/lib/format";

describe("formatTaxRate", () => {
  it("formats 66250 ppm as 6.625%", () => {
    expect(formatTaxRate(66250)).toBe("6.625%");
  });

  it("drops the decimal entirely for whole percentages (60000 → '6%')", () => {
    expect(formatTaxRate(60000)).toBe("6%");
  });

  it("formats 86250 ppm as 8.625%", () => {
    expect(formatTaxRate(86250)).toBe("8.625%");
  });

  it("keeps four significant digits when needed (70625 → '7.0625%')", () => {
    expect(formatTaxRate(70625)).toBe("7.0625%");
  });

  it("handles zero", () => {
    expect(formatTaxRate(0)).toBe("0%");
  });
});
