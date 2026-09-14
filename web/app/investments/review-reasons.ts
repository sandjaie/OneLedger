import type { ReviewReason } from "@/types/api";

const REASON_TEXT: Record<ReviewReason, string> = {
  unsupported_format: "This statement's layout is not supported yet.",
  unknown_security: "The security referenced here is not in your catalogue yet.",
  ambiguous_account: "More than one of your accounts could match this statement.",
  ambiguous_identifier: "This statement's account identifier matches more than one account.",
  unrecognized_event: "This row's transaction type is not recognized.",
  duplicate_conflict: "This event conflicts with one already recorded.",
  unit_mismatch: "The reported units do not reconcile with the statement.",
  cost_mismatch: "The reported cost does not reconcile with the statement.",
  missing_price: "No price is recorded for this date.",
  unmatched_cash: "This cash movement has no matching event.",
  incomplete_lot_allocation: "This disposal is not fully allocated to existing lots.",
  stale_valuation: "The recorded valuation is older than expected.",
};

export function reasonText(reason: ReviewReason) {
  return REASON_TEXT[reason] ?? reason.replaceAll("_", " ");
}
