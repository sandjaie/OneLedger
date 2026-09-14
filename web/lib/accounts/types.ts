// Account and institution vocabularies the API accepts. The generated types only say `string`.
export const INSTITUTION_TYPES = [
  "bank",
  "card_issuer",
  "broker",
  "fund_house",
  "lender",
  "govt",
  "insurer",
  "employer",
  "exchange_house",
  "other",
] as const;

export const ACCOUNT_TYPES = [
  "cash",
  "current",
  "savings",
  "salary",
  "fixed_deposit",
  "recurring_deposit",
  "credit_card",
  "personal_loan",
  "home_loan",
  "auto_loan",
  "education_loan",
  "gold_loan",
  "loan_other",
  "brokerage",
  "mutual_fund_folio",
  "retirement",
  "property",
  "precious_metal",
  "crypto",
  "other_asset",
  "receivable",
  "payable",
] as const;

export const LIABILITY_TYPES: ReadonlySet<string> = new Set([
  "credit_card",
  "personal_loan",
  "home_loan",
  "auto_loan",
  "education_loan",
  "gold_loan",
  "loan_other",
  "payable",
]);

// Holding-account types an investment purchase or import can resolve onto.
export const INVESTMENT_ACCOUNT_TYPES: ReadonlySet<string> = new Set(["brokerage", "mutual_fund_folio"]);
