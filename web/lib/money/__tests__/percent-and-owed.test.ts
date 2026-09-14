import { describe, expect, it } from "vitest";

import { formatOwed, formatPercent, moneyText } from "@/lib/money/format";

const row = { code: "XIN", symbol: "₹", decimals: 2, numberFormat: "indian" as const };

describe("formatPercent", () => {
  it("rounds the API's percentage on its digits, half away from zero", () => {
    expect(formatPercent("87.6550")).toBe("87.66%");
    expect(formatPercent("22.5000")).toBe("22.50%");
    expect(formatPercent("0.0049")).toBe("0.00%");
    expect(formatPercent("-12.3450")).toBe("-12.35%");
    expect(formatPercent("100.0000")).toBe("100.00%");
  });
});

describe("formatOwed", () => {
  it("shows a negative balance as what is owed, and leaves a positive one as it is", () => {
    expect(formatOwed("-4500.0000", row)).toBe("You owe ₹4,500.00");
    expect(formatOwed("4500.0000", row)).toBe("₹4,500.00");
  });
});

describe("moneyText", () => {
  it("formats with the code's own catalogue row and prints an unknown code raw", () => {
    expect(moneyText("-4500.0000", "XIN", [row])).toBe("-₹4,500.00");
    expect(moneyText("-4500.1234", "XZZ", [row])).toBe("-4500.1234 XZZ");
    expect(moneyText("12.5000", undefined, [row])).toBe("12.5000");
  });
});
