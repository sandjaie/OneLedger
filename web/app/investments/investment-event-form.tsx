"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

import { useEffect, useState } from "react";

import { INVESTMENT_ACCOUNT_TYPES } from "@/lib/accounts/types";
import { errorText, type apiFetch } from "@/lib/api/client";
import type {
  AccountRow,
  AcquisitionPosition,
  BonusActionCreate,
  CashLifecycleCreate,
  PlatformTransferActionCreate,
  PortfolioPositions,
  RightsActionCreate,
  SplitActionCreate,
  SwitchActionCreate,
  SystematicTransferActionCreate,
} from "@/types/api";

/** A holding the owner already has open — enough to record a new event against it without a
 * second security lookup. `holdingId` is optional because the holding page already knows it is
 * recording against its own holding and never needs to pick one. */
export type EventHolding = { holdingId?: string; accountId: string; securityId: string; securityName: string; currency: string };

type Props = {
  request: typeof apiFetch;
  accounts: Array<AccountRow>;
  // Fixed by the holding page; omitted on the portfolio page, which offers a holding picker
  // built from the same `/portfolio/positions` this form loads for the switch/transfer pickers.
  holding?: EventHolding;
  onRecorded: () => void;
};

export type CashKind = "sale" | "redemption" | "maturity" | "cash_distribution" | "interest"
  | "reinvested_distribution" | "charge" | "withheld_tax";
type UnitKind = "bonus" | "rights" | "split" | "switch" | "systematic_transfer" | "platform_transfer";
type Kind = CashKind | UnitKind;
export type CashField = "units" | "price" | "grossAmount" | "charges" | "withheldTax";

const CASH_KINDS: Array<{ value: CashKind; label: string }> = [
  { value: "sale", label: "Sale" },
  { value: "redemption", label: "Redemption" },
  { value: "maturity", label: "Maturity" },
  { value: "cash_distribution", label: "Cash distribution" },
  { value: "interest", label: "Interest" },
  { value: "reinvested_distribution", label: "Reinvested distribution" },
  { value: "charge", label: "Charge" },
  { value: "withheld_tax", label: "Withheld tax" },
];
const UNIT_KINDS: Array<{ value: UnitKind; label: string }> = [
  { value: "bonus", label: "Bonus units" },
  { value: "rights", label: "Rights" },
  { value: "split", label: "Split" },
  { value: "switch", label: "Switch" },
  { value: "systematic_transfer", label: "Systematic transfer" },
  { value: "platform_transfer", label: "Platform transfer" },
];

// Which of the nullable acquisition-shaped fields each cash lifecycle kind sends — derived
// straight from `CashLifecycleCreate.lifecycle_fields_agree` (api/oneledger/investments/schemas.py).
// Every field not listed here is sent as null; `netAmount` is always required and always shown.
export const CASH_FIELDS: Record<CashKind, ReadonlyArray<CashField>> = {
  sale: ["units", "price", "grossAmount", "charges", "withheldTax"],
  redemption: ["units", "price", "grossAmount", "charges", "withheldTax"],
  maturity: ["units", "price", "grossAmount", "charges", "withheldTax"],
  cash_distribution: ["grossAmount", "charges", "withheldTax"],
  interest: ["grossAmount", "charges", "withheldTax"],
  reinvested_distribution: ["units", "price", "grossAmount", "withheldTax"],
  charge: ["charges"],
  withheld_tax: ["withheldTax"],
};
const CASH_FIELD_LABEL: Record<CashField, string> = {
  units: "Units", price: "Event price", grossAmount: "Gross amount", charges: "Charges", withheldTax: "Withheld tax",
};

export function isCashKind(kind: string): kind is CashKind {
  return kind in CASH_FIELDS;
}

type Draft = {
  eventDate: string;
  units: string; price: string; grossAmount: string; charges: string; withheldTax: string; netAmount: string;
  unitRatio: string;
  outUnits: string; outPrice: string; outGrossAmount: string;
  inUnits: string; inPrice: string; inGrossAmount: string;
  inSecurityId: string; inAccountId: string;
};

const EMPTY_DRAFT: Draft = {
  eventDate: "", units: "", price: "", grossAmount: "", charges: "0.0000", withheldTax: "0.0000", netAmount: "",
  unitRatio: "",
  outUnits: "", outPrice: "", outGrossAmount: "", inUnits: "", inPrice: "", inGrossAmount: "",
  inSecurityId: "", inAccountId: "",
};

function draftComplete(kind: Kind, draft: Draft): boolean {
  if (!draft.eventDate) return false;
  if (isCashKind(kind)) return Boolean(draft.netAmount.trim()) && CASH_FIELDS[kind].every((field) => draft[field].trim());
  if (kind === "bonus") return Boolean(draft.units.trim());
  if (kind === "rights") return ["units", "price", "grossAmount", "charges", "withheldTax", "netAmount"].every((f) => draft[f as keyof Draft].trim());
  if (kind === "split") return Boolean(draft.unitRatio.trim());
  if (kind === "platform_transfer") return Boolean(draft.inAccountId && draft.outUnits.trim() && draft.inUnits.trim());
  return Boolean(draft.inSecurityId && ["outUnits", "outPrice", "outGrossAmount", "inUnits", "inPrice", "inGrossAmount"].every((f) => draft[f as keyof Draft].trim()));
}

type BuiltRequest = {
  path: string;
  body: CashLifecycleCreate | BonusActionCreate | RightsActionCreate | SplitActionCreate
    | SwitchActionCreate | SystematicTransferActionCreate | PlatformTransferActionCreate;
};

function buildRequest(kind: Kind, holding: EventHolding, draft: Draft, idempotencyKey: string): BuiltRequest {
  if (isCashKind(kind)) {
    const fields = CASH_FIELDS[kind];
    const body: CashLifecycleCreate = {
      accountId: holding.accountId,
      securityId: holding.securityId,
      eventDate: draft.eventDate,
      eventKind: kind,
      units: fields.includes("units") ? draft.units : null,
      price: fields.includes("price") ? draft.price : null,
      grossAmount: fields.includes("grossAmount") ? draft.grossAmount : null,
      charges: draft.charges,
      withheldTax: draft.withheldTax,
      netAmount: draft.netAmount,
      currency: holding.currency,
      idempotencyKey,
    };
    return { path: "/investment-events/cash-lifecycle", body };
  }
  if (kind === "bonus") {
    const body: BonusActionCreate = {
      kind: "bonus", accountId: holding.accountId, securityId: holding.securityId, eventDate: draft.eventDate,
      units: draft.units, currency: holding.currency, idempotencyKey,
    };
    return { path: "/investment-events/unit-actions", body };
  }
  if (kind === "rights") {
    const body: RightsActionCreate = {
      kind: "rights", accountId: holding.accountId, securityId: holding.securityId, eventDate: draft.eventDate,
      units: draft.units, price: draft.price, grossAmount: draft.grossAmount, charges: draft.charges,
      withheldTax: draft.withheldTax, netAmount: draft.netAmount, currency: holding.currency, idempotencyKey,
    };
    return { path: "/investment-events/unit-actions", body };
  }
  if (kind === "split") {
    const body: SplitActionCreate = {
      kind: "split", accountId: holding.accountId, securityId: holding.securityId, eventDate: draft.eventDate,
      unitRatio: draft.unitRatio, idempotencyKey,
    };
    return { path: "/investment-events/unit-actions", body };
  }
  if (kind === "platform_transfer") {
    const body: PlatformTransferActionCreate = {
      kind: "platform_transfer", outAccountId: holding.accountId, inAccountId: draft.inAccountId,
      securityId: holding.securityId, eventDate: draft.eventDate,
      outUnits: draft.outUnits, inUnits: draft.inUnits, currency: holding.currency, idempotencyKey,
    };
    return { path: "/investment-events/unit-actions", body };
  }
  const shared = {
    accountId: holding.accountId, outSecurityId: holding.securityId, inSecurityId: draft.inSecurityId,
    eventDate: draft.eventDate, outUnits: draft.outUnits, outPrice: draft.outPrice, outGrossAmount: draft.outGrossAmount,
    inUnits: draft.inUnits, inPrice: draft.inPrice, inGrossAmount: draft.inGrossAmount,
    currency: holding.currency, idempotencyKey,
  };
  const body: SwitchActionCreate | SystematicTransferActionCreate = kind === "switch"
    ? { kind: "switch", ...shared }
    : { kind: "systematic_transfer", ...shared };
  return { path: "/investment-events/unit-actions", body };
}

export function InvestmentEventForm({ request, accounts, holding, onRecorded }: Props) {
  const [positions, setPositions] = useState<Array<AcquisitionPosition>>([]);
  const [positionsState, setPositionsState] = useState<"loading" | "ready" | "error">("loading");
  const [positionsAttempt, setPositionsAttempt] = useState(0);
  const [selectedHoldingId, setSelectedHoldingId] = useState("");
  const [kind, setKind] = useState<Kind>("sale");
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [feedback, setFeedback] = useState("");

  useEffect(() => {
    let current = true;
    request<PortfolioPositions>("/portfolio/positions")
      .then((next) => {
        if (!current) return;
        setPositions(next.positions.filter((p) => !p.closed));
        setPositionsState("ready");
      })
      .catch(() => {
        if (current) setPositionsState("error");
      });
    return () => { current = false; };
  }, [request, positionsAttempt]);

  const pickedPosition = positions.find((p) => p.holdingId === selectedHoldingId);
  const selected: EventHolding | undefined = holding
    ?? (pickedPosition && {
      holdingId: pickedPosition.holdingId, accountId: pickedPosition.accountId,
      securityId: pickedPosition.securityId, securityName: pickedPosition.securityName, currency: pickedPosition.currency,
    });

  function resetDraft(nextKind: Kind) {
    setKind(nextKind);
    setDraft(EMPTY_DRAFT);
    setIdempotencyKey("");
    setConfirming(false);
    setProblem("");
  }

  function changeDraft<K extends keyof Draft>(field: K, value: Draft[K]) {
    setDraft((current) => ({ ...current, [field]: value }));
    setIdempotencyKey("");
    setConfirming(false);
    setProblem("");
  }

  function review() {
    if (!selected || !draftComplete(kind, draft)) return;
    if (!idempotencyKey) setIdempotencyKey(crypto.randomUUID());
    setProblem("");
    setConfirming(true);
  }

  async function submit() {
    if (!selected || busy) return;
    const key = idempotencyKey || crypto.randomUUID();
    if (!idempotencyKey) setIdempotencyKey(key);
    const built = buildRequest(kind, selected, draft, key);
    setBusy(true);
    setProblem("");
    try {
      await request(built.path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(built.body),
      });
      setFeedback("Event recorded.");
      resetDraft(kind);
      setPositionsAttempt((value) => value + 1);
      onRecorded();
    } catch (error) {
      setProblem(errorText(error, "This event was not recorded. Retry."));
    } finally {
      setBusy(false);
    }
  }

  const investmentAccounts = accounts.filter((a) => a.isActive && INVESTMENT_ACCOUNT_TYPES.has(a.accountType));
  const otherSecurities = selected ? positions.filter((p) => p.accountId === selected.accountId && p.securityId !== selected.securityId) : [];
  const otherAccounts = selected ? investmentAccounts.filter((a) => a.id !== selected.accountId) : [];
  const fields = isCashKind(kind) ? CASH_FIELDS[kind] : [];

  return (
    <form className="investment-record-event investment-context-workflow" onSubmit={(e) => { e.preventDefault(); review(); }}>
      <h3>Record an event</h3>
      {feedback && <p className="investment-feedback" role="status">{feedback}</p>}
      {positionsState === "error" && (
        <p className="inline-alert" role="alert">
          Open holdings could not be loaded.{" "}
          <Button
            variant="outline"
            onClick={() => { setPositionsState("loading"); setPositionsAttempt((value) => value + 1); }}
            type="button"
          >Try again</Button>
        </p>
      )}

      {!holding && (
        <label className="select-field">
          <span>Holding</span>
          <NativeSelect disabled={confirming} onChange={(e) => { setSelectedHoldingId(e.target.value); resetDraft(kind); }} value={selectedHoldingId}>
            <option value="">Choose an open holding</option>
            {positions.map((p) => <option key={p.holdingId} value={p.holdingId}>{p.securityName} · {p.currency}</option>)}
          </NativeSelect>
        </label>
      )}

      {selected && (
        <>
          <label className="select-field">
            <span>Event kind</span>
            <NativeSelect disabled={confirming} onChange={(e) => resetDraft(e.target.value as Kind)} value={kind}>
              <optgroup label="Cash lifecycle">
                {CASH_KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
              </optgroup>
              <optgroup label="Unit action">
                {UNIT_KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
              </optgroup>
            </NativeSelect>
          </label>

          <label className="date-field"><span>Event date</span><Input disabled={confirming} onChange={(e) => changeDraft("eventDate", e.target.value)} required type="date" value={draft.eventDate} /></label>

          {isCashKind(kind) && (
            <>
              {fields.map((field) => (
                <label className="text-field" key={field}>
                  <span>{CASH_FIELD_LABEL[field]} ({selected.currency})</span>
                  <Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft(field, e.target.value)} required value={draft[field]} />
                </label>
              ))}
              <label className="text-field"><span>Net cash amount ({selected.currency})</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("netAmount", e.target.value)} required value={draft.netAmount} /></label>
            </>
          )}

          {kind === "bonus" && (
            <label className="text-field"><span>Bonus units</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("units", e.target.value)} required value={draft.units} /></label>
          )}

          {kind === "rights" && (
            <>
              <label className="text-field"><span>Units</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("units", e.target.value)} required value={draft.units} /></label>
              <label className="text-field"><span>Price</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("price", e.target.value)} required value={draft.price} /></label>
              <label className="text-field"><span>Gross amount ({selected.currency})</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("grossAmount", e.target.value)} required value={draft.grossAmount} /></label>
              <label className="text-field"><span>Charges ({selected.currency})</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("charges", e.target.value)} required value={draft.charges} /></label>
              <label className="text-field"><span>Withheld tax ({selected.currency})</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("withheldTax", e.target.value)} required value={draft.withheldTax} /></label>
              <label className="text-field"><span>Net cash amount ({selected.currency})</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("netAmount", e.target.value)} required value={draft.netAmount} /></label>
            </>
          )}

          {kind === "split" && (
            <label className="text-field"><span>Unit ratio</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("unitRatio", e.target.value)} required value={draft.unitRatio} /></label>
          )}

          {(kind === "switch" || kind === "systematic_transfer") && (
            <>
              <label className="select-field">
                <span>Security switched into</span>
                <NativeSelect disabled={confirming} onChange={(e) => changeDraft("inSecurityId", e.target.value)} value={draft.inSecurityId}>
                  <option value="">Choose another security held in this account</option>
                  {otherSecurities.map((p) => <option key={p.securityId} value={p.securityId}>{p.securityName}</option>)}
                </NativeSelect>
              </label>
              {otherSecurities.length === 0 && (
                <p className="investment-state">No other security is held in this account yet. Switching into a brand-new security is not offered here.</p>
              )}
              <label className="text-field"><span>Units out</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("outUnits", e.target.value)} required value={draft.outUnits} /></label>
              <label className="text-field"><span>Price out</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("outPrice", e.target.value)} required value={draft.outPrice} /></label>
              <label className="text-field"><span>Gross amount out ({selected.currency})</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("outGrossAmount", e.target.value)} required value={draft.outGrossAmount} /></label>
              <label className="text-field"><span>Units in</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("inUnits", e.target.value)} required value={draft.inUnits} /></label>
              <label className="text-field"><span>Price in</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("inPrice", e.target.value)} required value={draft.inPrice} /></label>
              <label className="text-field"><span>Gross amount in ({selected.currency})</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("inGrossAmount", e.target.value)} required value={draft.inGrossAmount} /></label>
            </>
          )}

          {kind === "platform_transfer" && (
            <>
              <label className="select-field">
                <span>Account transferred into</span>
                <NativeSelect disabled={confirming} onChange={(e) => changeDraft("inAccountId", e.target.value)} value={draft.inAccountId}>
                  <option value="">Choose the receiving account</option>
                  {otherAccounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </NativeSelect>
              </label>
              <label className="text-field"><span>Units out</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("outUnits", e.target.value)} required value={draft.outUnits} /></label>
              <label className="text-field"><span>Units in</span><Input disabled={confirming} inputMode="decimal" onChange={(e) => changeDraft("inUnits", e.target.value)} required value={draft.inUnits} /></label>
            </>
          )}

          {!confirming && (
            <Button variant="default" disabled={!draftComplete(kind, draft)} type="submit">Review event</Button>
          )}

          {confirming && (
            <div className="investment-confirmation">
              <p>This appends a new owner-entered event with the exact strings above.</p>
              {problem && <p className="inline-alert" role="alert">{problem}</p>}
              <div className="investment-confirm-actions">
                <Button variant="default" disabled={busy} onClick={() => void submit()} type="button">
                  {busy ? "Recording event…" : problem ? "Retry event" : "Confirm event"}
                </Button>
                <Button variant="outline" disabled={busy} onClick={() => { setConfirming(false); setProblem(""); }} type="button">Go back</Button>
              </div>
            </div>
          )}
        </>
      )}
    </form>
  );
}
