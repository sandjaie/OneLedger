import { describe, expect, it } from "vitest";

import { type Currency, formatMoney } from "@/lib/money/format";

function currency(overrides: Partial<Currency> = {}): Currency {
  return {
    code: "XUS",
    symbol: "$",
    decimals: 2,
    numberFormat: "international",
    ...overrides,
  };
}

describe("formatMoney", () => {
  it("groups Indian and international values without losing large decimal strings", () => {
    expect(
      formatMoney(
        "13642180",
        currency({ code: "XIN", symbol: "₹", numberFormat: "indian" }),
      ),
    ).toBe("₹1,36,42,180.00");
    expect(formatMoney("9007199254740993.12", currency())).toBe(
      "$9,007,199,254,740,993.12",
    );
  });

  it("uses currency decimals and word-like symbol spacing", () => {
    expect(formatMoney("5982530", currency({ code: "XAE", symbol: "AED" }))).toBe(
      "AED 5,982,530.00",
    );
    expect(
      formatMoney(
        "1234.5",
        currency({ code: "XKD", symbol: "KD", decimals: 3 }),
      ),
    ).toBe("KD 1,234.500");
    expect(
      formatMoney("999.5", currency({ code: "XNO", symbol: "ZED", decimals: 0 })),
    ).toBe("ZED 1,000");
  });

  it("rounds half up and suppresses rounded negative zero", () => {
    expect(formatMoney("1.235", currency())).toBe("$1.24");
    expect(formatMoney("1.234", currency())).toBe("$1.23");
    expect(formatMoney("-310", currency({ code: "XAE", symbol: "AED" }))).toBe(
      "-AED 310.00",
    );
    expect(formatMoney("-0.004", currency())).toBe("$0.00");
    expect(formatMoney("-0.005", currency())).toBe("-$0.01");
  });

  it("abbreviates configured grouping styles with two display decimals", () => {
    expect(
      formatMoney(
        "13642180",
        currency({ code: "XIN", symbol: "₹", numberFormat: "indian" }),
        { abbreviate: true },
      ),
    ).toBe("₹1.36 Cr");
    expect(
      formatMoney(
        "4210000",
        currency({ code: "XIN", symbol: "₹", numberFormat: "indian" }),
        { abbreviate: true },
      ),
    ).toBe("₹42.10 L");
    expect(
      formatMoney("5982530", currency({ code: "XAE", symbol: "AED" }), {
        abbreviate: true,
      }),
    ).toBe("AED 5.98 M");
  });

  it("rejects non-string, nonfinite, and malformed decimal input", () => {
    for (const value of [
      12,
      "",
      " ",
      "NaN",
      "Infinity",
      "1,234",
      "1.2.3",
      "1e3",
      ".5",
      "5.",
    ]) {
      expect(() => formatMoney(value as string, currency())).toThrow(
        "finite decimal string",
      );
    }
  });
});
