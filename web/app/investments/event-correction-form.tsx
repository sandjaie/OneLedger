"use client";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { useState } from "react";

import { CASH_FIELDS, isCashKind, type CashKind } from "@/app/investments/investment-event-form";
import { errorText, type apiFetch } from "@/lib/api/client";
import type {
  AcquisitionCorrectionCreate,
  BonusActionCorrectionCreate,
  CashLifecycleCorrectionCreate,
  HoldingCashLink,
  HoldingDetail,
  HoldingEventRow,
  PlatformTransferActionCorrectionCreate,
  RightsActionCorrectionCreate,
  SplitActionCorrectionCreate,
  SwitchActionCorrectionCreate,
  SystematicTransferActionCorrectionCreate,
} from "@/types/api";

type Props = {
  event: HoldingEventRow;
  holding: HoldingDetail;
  request: typeof apiFetch;
  onCorrected: () => void;
};

const PURCHASE_KINDS = new Set(["purchase", "recurring_purchase"]);
const PAIR_KIND = {
  switch_out: "switch", switch_in: "switch",
  systematic_transfer_out: "systematic_transfer", systematic_transfer_in: "systematic_transfer",
  platform_transfer_out: "platform_transfer", platform_transfer_in: "platform_transfer",
} as const;
type PairKind = "switch" | "systematic_transfer" | "platform_transfer";

/** V40's own correction trigger refuses correcting any event with an existing
 * `investment_cash_allocation` row of any role, not only a purchase's own `funding` role — so
 * every kind's funded state reads off the holding's own cash links, never `fundingComplete`
 * (which is only meaningful for a purchase's remaining-amount arithmetic, not this refusal). */
export function isFundedEvent(event: HoldingEventRow, cashLinks: Array<HoldingCashLink>): boolean {
  return cashLinks.some((link) => link.investmentEventId === event.id);
}

// Every field a correction form ever edits, across every kind — a plain string draft, matching
// the record-event form's own shape so both forms send exact typed strings, never a computed one.
type Draft = {
  units: string; price: string; grossAmount: string; charges: string; withheldTax: string; netAmount: string;
  unitRatio: string;
  outUnits: string; outPrice: string; outGrossAmount: string;
  inUnits: string; inPrice: string; inGrossAmount: string;
};

function isOutLeg(eventKind: string) {
  return eventKind.endsWith("_out");
}

/** Every editable draft value pre-filled from the event's own stored facts (and, for a paired
 * leg, its counterpart's) — nothing here is ever left for the owner to retype from scratch, and
 * nothing here is computed: every string is one the holding detail already returned. */
function initialDraft(event: HoldingEventRow): Draft {
  const pairKind = (PAIR_KIND as Record<string, PairKind | undefined>)[event.eventKind];
  if (pairKind) {
    const outLeg = isOutLeg(event.eventKind);
    return {
      units: "", price: "", grossAmount: "", charges: "", withheldTax: "", netAmount: "", unitRatio: "",
      outUnits: (outLeg ? event.units : event.counterpartUnits) ?? "",
      outPrice: (outLeg ? event.price : event.counterpartPrice) ?? "",
      outGrossAmount: (outLeg ? event.grossAmount : event.counterpartGrossAmount) ?? "",
      inUnits: (outLeg ? event.counterpartUnits : event.units) ?? "",
      inPrice: (outLeg ? event.counterpartPrice : event.price) ?? "",
      inGrossAmount: (outLeg ? event.counterpartGrossAmount : event.grossAmount) ?? "",
    };
  }
  return {
    units: event.units ?? "", price: event.price ?? "", grossAmount: event.grossAmount ?? "",
    charges: event.charges ?? "", withheldTax: event.withheldTax ?? "", netAmount: event.netAmount ?? "",
    unitRatio: event.unitRatio ?? "",
    outUnits: "", outPrice: "", outGrossAmount: "", inUnits: "", inPrice: "", inGrossAmount: "",
  };
}

type CorrectionRequest = {
  path: string;
  body: AcquisitionCorrectionCreate | CashLifecycleCorrectionCreate | BonusActionCorrectionCreate
    | RightsActionCorrectionCreate | SplitActionCorrectionCreate | SwitchActionCorrectionCreate
    | SystematicTransferActionCorrectionCreate | PlatformTransferActionCorrectionCreate;
};

/** The exact route and body for correcting one active event, sending the owner's edited draft —
 * every field not offered for editing is still a fact the holding detail already returned, never
 * guessed. Returns undefined when the event's own pair data is missing, which disables the
 * action rather than submitting an incomplete correction. */
export function correctionRequest(
  event: HoldingEventRow,
  holding: HoldingDetail,
  draft: Draft,
  idempotencyKey: string,
  correctionReason: string,
): CorrectionRequest | undefined {
  const eventDate = event.eventDate;
  const currency = event.currency;
  const expectedCorrectionState = "uncorrected" as const;

  if (PURCHASE_KINDS.has(event.eventKind)) {
    const body: AcquisitionCorrectionCreate = {
      accountId: holding.accountId,
      securityId: holding.securityId,
      eventDate,
      eventKind: event.eventKind as AcquisitionCorrectionCreate["eventKind"],
      units: draft.units,
      price: draft.price,
      grossAmount: draft.grossAmount,
      charges: draft.charges,
      withheldTax: draft.withheldTax,
      netAmount: draft.netAmount,
      currency,
      idempotencyKey,
      expectedCorrectionState,
      correctionReason,
    };
    return { path: `/investment-events/${event.id}/corrections`, body };
  }

  if (isCashKind(event.eventKind)) {
    const fields = CASH_FIELDS[event.eventKind as CashKind];
    const body: CashLifecycleCorrectionCreate = {
      accountId: holding.accountId,
      securityId: holding.securityId,
      eventDate,
      eventKind: event.eventKind as CashLifecycleCorrectionCreate["eventKind"],
      units: fields.includes("units") ? draft.units : null,
      price: fields.includes("price") ? draft.price : null,
      grossAmount: fields.includes("grossAmount") ? draft.grossAmount : null,
      charges: fields.includes("charges") ? draft.charges : "0.0000",
      withheldTax: fields.includes("withheldTax") ? draft.withheldTax : "0.0000",
      netAmount: draft.netAmount,
      currency,
      idempotencyKey,
      expectedCorrectionState,
      correctionReason,
    };
    return { path: `/investment-events/${event.id}/cash-lifecycle-corrections`, body };
  }

  if (event.eventKind === "bonus") {
    const body: BonusActionCorrectionCreate = {
      kind: "bonus", accountId: holding.accountId, securityId: holding.securityId, eventDate,
      units: draft.units, currency, idempotencyKey, expectedCorrectionState, correctionReason,
    };
    return { path: `/investment-events/${event.id}/unit-action-corrections`, body };
  }
  if (event.eventKind === "rights") {
    const body: RightsActionCorrectionCreate = {
      kind: "rights", accountId: holding.accountId, securityId: holding.securityId, eventDate,
      units: draft.units, price: draft.price, grossAmount: draft.grossAmount, charges: draft.charges,
      withheldTax: draft.withheldTax, netAmount: draft.netAmount, currency,
      idempotencyKey, expectedCorrectionState, correctionReason,
    };
    return { path: `/investment-events/${event.id}/unit-action-corrections`, body };
  }
  if (event.eventKind === "split") {
    const body: SplitActionCorrectionCreate = {
      kind: "split", accountId: holding.accountId, securityId: holding.securityId, eventDate,
      unitRatio: draft.unitRatio, idempotencyKey, expectedCorrectionState, correctionReason,
    };
    return { path: `/investment-events/${event.id}/unit-action-corrections`, body };
  }

  const pairKind = (PAIR_KIND as Record<string, PairKind | undefined>)[event.eventKind];
  if (!pairKind) return undefined;
  const outLeg = isOutLeg(event.eventKind);
  const outLegId = outLeg ? event.id : event.counterpartEventId;
  if (!outLegId) return undefined;

  if (pairKind === "platform_transfer") {
    // The receiving account is not editable: the database keeps each corrected leg on its own account.
    const outAccountId = outLeg ? holding.accountId : event.counterpartAccountId;
    const inAccountId = outLeg ? event.counterpartAccountId : holding.accountId;
    if (!outAccountId || !inAccountId) return undefined;
    const body: PlatformTransferActionCorrectionCreate = {
      kind: "platform_transfer",
      outAccountId, inAccountId, securityId: holding.securityId,
      eventDate, outUnits: draft.outUnits, inUnits: draft.inUnits, currency,
      idempotencyKey, expectedCorrectionState, correctionReason,
    };
    return { path: `/investment-events/${outLegId}/unit-action-corrections`, body };
  }
  const outSecurityId = outLeg ? holding.securityId : event.counterpartSecurityId;
  const inSecurityId = outLeg ? event.counterpartSecurityId : holding.securityId;
  if (!outSecurityId || !inSecurityId) return undefined;
  const body: SwitchActionCorrectionCreate | SystematicTransferActionCorrectionCreate = {
    kind: pairKind, accountId: holding.accountId, outSecurityId, inSecurityId,
    eventDate, outUnits: draft.outUnits, outPrice: draft.outPrice, outGrossAmount: draft.outGrossAmount,
    inUnits: draft.inUnits, inPrice: draft.inPrice, inGrossAmount: draft.inGrossAmount, currency,
    idempotencyKey, expectedCorrectionState, correctionReason,
  };
  return { path: `/investment-events/${outLegId}/unit-action-corrections`, body };
}

const FIELD_LABEL: Record<string, string> = {
  units: "Units", price: "Event price", grossAmount: "Gross amount", charges: "Charges", withheldTax: "Withheld tax",
  netAmount: "Net cash amount", unitRatio: "Unit ratio",
  outUnits: "Units out", outPrice: "Price out", outGrossAmount: "Gross amount out",
  inUnits: "Units in", inPrice: "Price in", inGrossAmount: "Gross amount in",
};

function ratioFields(event: HoldingEventRow): Array<keyof Draft> {
  if (PURCHASE_KINDS.has(event.eventKind) || event.eventKind === "rights") {
    return ["units", "price", "grossAmount", "charges", "withheldTax", "netAmount"];
  }
  if (isCashKind(event.eventKind)) return [...CASH_FIELDS[event.eventKind as CashKind], "netAmount"];
  if (event.eventKind === "bonus") return ["units"];
  if (event.eventKind === "split") return ["unitRatio"];
  if (event.eventKind.startsWith("platform_transfer")) return ["outUnits", "inUnits"];
  if (event.eventKind in PAIR_KIND) return ["outUnits", "outPrice", "outGrossAmount", "inUnits", "inPrice", "inGrossAmount"];
  return [];
}

export function EventCorrectionForm({ event, holding, request, onCorrected }: Props) {
  const [draft, setDraft] = useState<Draft>(() => initialDraft(event));
  const [reason, setReason] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  const fields = ratioFields(event);

  function invalidate() {
    setIdempotencyKey("");
    setConfirming(false);
    setProblem("");
  }

  function changeField(field: keyof Draft, value: string) {
    setDraft((current) => ({ ...current, [field]: value }));
    invalidate();
  }

  function changeReason(value: string) {
    setReason(value);
    invalidate();
  }

  function review() {
    if (!reason.trim()) return;
    if (!idempotencyKey) setIdempotencyKey(crypto.randomUUID());
    setProblem("");
    setConfirming(true);
  }

  async function submit() {
    const key = idempotencyKey || crypto.randomUUID();
    if (!idempotencyKey) setIdempotencyKey(key);
    const built = correctionRequest(event, holding, draft, key, reason.trim());
    if (!built || busy) return;
    setBusy(true);
    setProblem("");
    try {
      await request(built.path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(built.body),
      });
      onCorrected();
    } catch (error) {
      setProblem(errorText(error, "This correction was not recorded. Retry."));
    } finally {
      setBusy(false);
    }
  }

  const preview = idempotencyKey ? correctionRequest(event, holding, draft, idempotencyKey, reason.trim()) : undefined;

  return (
    <fieldset aria-label={`Correct the ${event.eventKind.replaceAll("_", " ")} on ${event.eventDate}`} className="investment-correction-form">
      <legend>Correct this event</legend>

      {fields.map((field) => (
        <label className="text-field" key={field}>
          <span>{FIELD_LABEL[field]}</span>
          <Input disabled={confirming} inputMode="decimal" onChange={(change) => changeField(field, change.target.value)} required value={draft[field]} />
        </label>
      ))}
      <label className="text-field">
        <span>Correction reason</span>
        <Input disabled={confirming} onChange={(change) => changeReason(change.target.value)} required value={reason} />
      </label>

      {!confirming && (
        <Button variant="outline" disabled={!reason.trim()} onClick={review} type="button">
          Review correction
        </Button>
      )}
      {confirming && (
        <div className="investment-confirmation">
          <p>This appends a new owner-entered event replacing the one above. It cannot be edited afterward.</p>
          {preview && (
            <dl>
              {Object.entries(preview.body)
                .filter(([key]) => (fields as Array<string>).includes(key))
                .map(([key, value]) => <div key={key}><dt>{FIELD_LABEL[key] ?? key}</dt><dd>{String(value)}</dd></div>)}
            </dl>
          )}
          {!preview && <p className="inline-alert" role="alert">This event&rsquo;s pair data is incomplete — correct it from its other leg&rsquo;s holding instead.</p>}
          {problem && <p className="inline-alert" role="alert">{problem}</p>}
          <div className="investment-confirm-actions">
            <Button variant="default" disabled={busy || !preview} onClick={() => void submit()} type="button">
              {busy ? "Recording correction…" : problem ? "Retry correction" : "Confirm correction"}
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => { setConfirming(false); setProblem(""); }} type="button">
              Go back
            </Button>
          </div>
        </div>
      )}
    </fieldset>
  );
}
