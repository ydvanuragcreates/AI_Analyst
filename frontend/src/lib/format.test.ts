import { describe, expect, it } from "vitest";

import { formatFileSize, formatNumber, formatPercent } from "@/lib/format";

describe("formatNumber", () => {
  it("adds thousands separators", () => {
    expect(formatNumber(125420)).toBe("125,420");
  });
});

describe("formatPercent", () => {
  it("formats a positive value without a sign by default", () => {
    expect(formatPercent(8.4)).toBe("8.4%");
  });

  it("adds a '+' sign for positive values when signed=true", () => {
    expect(formatPercent(27, { signed: true })).toBe("+27.0%");
  });

  it("never adds a '+' sign for negative values", () => {
    expect(formatPercent(-8.4, { signed: true })).toBe("-8.4%");
  });
});

describe("formatFileSize", () => {
  it("stays in bytes under 1024", () => {
    expect(formatFileSize(512)).toBe("512 B");
  });

  it("converts to KB/MB appropriately", () => {
    expect(formatFileSize(2048)).toBe("2.0 KB");
    expect(formatFileSize(5 * 1024 * 1024)).toBe("5.0 MB");
  });
});
