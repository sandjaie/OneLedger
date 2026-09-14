import type { HouseholdCurrency } from "@/types/api";

export type Currency = HouseholdCurrency;

type ParsedDecimal = {
  negative: boolean;
  digits: bigint;
  scale: number;
};

type DisplayParts = {
  negative: boolean;
  whole: string;
  fraction: string;
};

const DECIMAL_PATTERN = /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;
const WORD_SYMBOL_PATTERN = /^[A-Za-z]+$/;
const ABBREVIATION_DECIMALS = 2;
const ZERO = BigInt(0);
const ONE = BigInt(1);
const TWO = BigInt(2);
const TEN = BigInt(10);
const ONE_HUNDRED_THOUSAND = BigInt(100_000);
const ONE_MILLION = BigInt(1_000_000);
const TEN_MILLION = BigInt(10_000_000);

export function formatMoney(
  value: string,
  currency: Currency,
  opts: { abbreviate?: boolean } = {},
): string {
  const parsed = parseDecimal(value);
  const abbreviated = abbreviationUnit(parsed, currency);
  const parts =
    opts.abbreviate && abbreviated !== undefined
      ? roundScaled(parsed, abbreviated.units, ABBREVIATION_DECIMALS)
      : roundToDecimals(parsed, currency.decimals);
  const negative = parts.negative ? "-" : "";
  const prefix = `${negative}${currency.symbol}${symbolGap(currency.symbol)}`;

  if (opts.abbreviate && abbreviated !== undefined) {
    return `${prefix}${parts.whole}.${parts.fraction} ${abbreviated.suffix}`;
  }

  const grouped = groupWhole(parts.whole, currency.numberFormat);
  return `${prefix}${grouped}${parts.fraction ? `.${parts.fraction}` : ""}`;
}

function parseDecimal(value: string): ParsedDecimal {
  if (typeof value !== "string" || !DECIMAL_PATTERN.test(value)) {
    throw new TypeError("money value must be a finite decimal string");
  }

  const negative = value.startsWith("-");
  const unsigned = negative ? value.slice(1) : value;
  const [whole, fraction = ""] = unsigned.split(".");
  return {
    negative,
    digits: BigInt(`${whole}${fraction}`),
    scale: fraction.length,
  };
}

function roundToDecimals(parsed: ParsedDecimal, decimals: number): DisplayParts {
  const rounded = roundRatio(
    parsed.digits,
    TEN ** BigInt(Math.max(parsed.scale - decimals, 0)),
  );
  const scaled =
    parsed.scale < decimals
      ? rounded * TEN ** BigInt(decimals - parsed.scale)
      : rounded;

  return displayParts(scaled, decimals, parsed.negative);
}

function roundScaled(
  parsed: ParsedDecimal,
  units: bigint,
  decimals: number,
): DisplayParts {
  const numerator = parsed.digits * TEN ** BigInt(decimals);
  const denominator = TEN ** BigInt(parsed.scale) * units;
  return displayParts(roundRatio(numerator, denominator), decimals, parsed.negative);
}

function roundRatio(numerator: bigint, denominator: bigint): bigint {
  const quotient = numerator / denominator;
  const remainder = numerator % denominator;
  return remainder * TWO >= denominator ? quotient + ONE : quotient;
}

function displayParts(value: bigint, decimals: number, negative: boolean): DisplayParts {
  const sign = negative && value !== ZERO;
  if (decimals === 0) {
    return { negative: sign, whole: value.toString(), fraction: "" };
  }

  const padded = value.toString().padStart(decimals + 1, "0");
  return {
    negative: sign,
    whole: padded.slice(0, -decimals),
    fraction: padded.slice(-decimals),
  };
}

function groupWhole(whole: string, format: Currency["numberFormat"]): string {
  if (format === "indian") {
    const finalGroup = whole.slice(-3);
    const leading = whole.slice(0, -3);
    if (!leading) {
      return finalGroup;
    }
    return `${leading.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${finalGroup}`;
  }

  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function abbreviationUnit(
  parsed: ParsedDecimal,
  currency: Currency,
): { units: bigint; suffix: string } | undefined {
  const major = integerPart(parsed);

  if (currency.numberFormat === "indian") {
    if (major >= TEN_MILLION) {
      return { units: TEN_MILLION, suffix: "Cr" };
    }
    if (major >= ONE_HUNDRED_THOUSAND) {
      return { units: ONE_HUNDRED_THOUSAND, suffix: "L" };
    }
    return undefined;
  }

  if (major >= ONE_MILLION) {
    return { units: ONE_MILLION, suffix: "M" };
  }
  return undefined;
}

function integerPart(parsed: ParsedDecimal): bigint {
  if (parsed.scale === 0) {
    return parsed.digits;
  }
  return parsed.digits / TEN ** BigInt(parsed.scale);
}

function symbolGap(symbol: string): string {
  return WORD_SYMBOL_PATTERN.test(symbol) ? " " : "";
}

/** A percentage the API computed — `87.6550` is 87.655% — rounded for display on its digits, the
 * way `formatMoney` rounds, so no float ever touches it. */
export function formatPercent(value: string, decimals = 2): string {
  const parts = roundToDecimals(parseDecimal(value), decimals);
  return `${parts.negative ? "-" : ""}${parts.whole}${parts.fraction ? `.${parts.fraction}` : ""}%`;
}

/** An amount in the currency its code names. A code the catalogue does not hold prints raw with
 * that code — never with invented decimals or another currency's symbol (ADR-0012). */
export function moneyText(
  value: string,
  code: string | undefined,
  currencies: ReadonlyArray<Currency>,
): string {
  const currency = currencies.find((row) => row.code === code);
  if (currency) return formatMoney(value, currency);
  return code ? `${value} ${code}` : value;
}

/** A liability's balance — negative in the ledger — shown as what is owed (ADR-0010). The value
 * stays signed on the wire; display flips a sign only to show what is owed, here and in the
 * account group totals' " owed" suffix (`accounts-screen.tsx`). */
export function formatOwed(value: string, currency: Currency): string {
  return value.startsWith("-")
    ? `You owe ${formatMoney(value.slice(1), currency)}`
    : formatMoney(value, currency);
}
