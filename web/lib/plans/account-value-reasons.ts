/** `ResourceAccountRow.reason` / `EligibleAccountRow.reason` (api/oneledger/resources.py's
 * `NativeValue.reason`): the server sends a short code, the web owns the sentence — goals.tsx
 * and emergency-reserve.tsx both print an account's value from the same three codes. */
const ACCOUNT_VALUE_REASON_TEXT: Record<string, string> = {
  missing_snapshot: "no valuation snapshot is recorded for this account",
  missing_price: "a price is missing for one of this account's holdings",
  mixed_currency: "this account holds a position in another currency",
};

export function accountValueReasonText(reason: string): string {
  return ACCOUNT_VALUE_REASON_TEXT[reason] ?? reason;
}

/** goals.tsx and emergency-reserve.tsx each fold a reason-code list into one sentence over their
 * own lookup map (progress reasons, coverage reasons) — this is that shared fold. */
export function reasonsText(reasons: Array<string>, textFor: Record<string, string>): string {
  return reasons.map((reason) => textFor[reason] ?? reason).join("; ");
}
