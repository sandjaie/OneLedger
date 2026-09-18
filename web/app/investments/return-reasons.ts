export const XIRR_REASON_TEXT: Record<string, string> = {
  no_sign_change: "No sign change across the cash flows.",
  multiple_roots: "More than one rate solves the cash flows.",
  missing_terminal_value: "No current or realised value is recorded.",
  incomplete_history: "History includes a switch, transfer or split.",
  missing_price: "A dated price is missing.",
  blocked_action: "An unrecognized event blocks this calculation.",
  not_converged: "The calculation did not converge.",
};

export const DIETZ_REASON_TEXT: Record<string, string> = {
  no_denominator: "Opening value and weighted contribution are zero.",
  missing_price: "A dated price is missing.",
};
