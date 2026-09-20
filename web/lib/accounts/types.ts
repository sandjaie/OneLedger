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

/** The Phase 4.8 display roles. A deterministic presentation grouping derived from
 * `accountType`, in the specification's own order — never a new stored financial fact. */
export const ACCOUNT_ROLES = [
  "everyday",
  "reserve",
  "investment",
  "property",
  "credit",
  "loan",
  "other",
] as const;

export type AccountRole = (typeof ACCOUNT_ROLES)[number];

export const ACCOUNT_ROLE_LABEL: Record<AccountRole, string> = {
  everyday: "Everyday",
  reserve: "Reserve",
  investment: "Investment",
  property: "Property",
  credit: "Credit",
  loan: "Loan",
  other: "Other",
};

const ROLE_BY_TYPE: ReadonlyMap<string, AccountRole> = new Map([
  ...(["cash", "current", "salary"] as const).map((type) => [type, "everyday"] as const),
  ...(["savings", "fixed_deposit", "recurring_deposit"] as const).map((type) => [type, "reserve"] as const),
  ...(["brokerage", "mutual_fund_folio", "retirement", "precious_metal", "crypto"] as const)
    .map((type) => [type, "investment"] as const),
  ...(["property"] as const).map((type) => [type, "property"] as const),
  ...(["credit_card", "payable"] as const).map((type) => [type, "credit"] as const),
  ...(["personal_loan", "home_loan", "auto_loan", "education_loan", "gold_loan", "loan_other"] as const)
    .map((type) => [type, "loan"] as const),
  ...(["other_asset", "receivable"] as const).map((type) => [type, "other"] as const),
]);

export function accountRole(accountType: string): AccountRole {
  return ROLE_BY_TYPE.get(accountType) ?? "other";
}
