"use client";

import { useState } from "react";

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

// The generated client exposes each unit-action correction variant by name but not their union
// (only the route's own inline request-body type carries it) — restated once here.
type UnitActionCorrectionCreate = BonusActionCorrectionCreate | RightsActionCorrectionCreate
  | SplitActionCorrectionCreate | SwitchActionCorrectionCreate
  | SystematicTransferActionCorrectionCreate | PlatformTransferActionCorrectionCreate;

type Props = {
  event: HoldingEventRow;
  holding: HoldingDetail;
  request: typeof apiFetch;
  onCorrected: () => void;
};

const PURCHASE_KINDS = new Set(["purchase", "recurring_purchase"]);
const CASH_LIFECYCLE_KINDS = new Set([
  "sale", "redemption", "maturity", "cash_distribution", "interest",
  "reinvested_distribution", "charge", "withheld_tax",
]);
const PAIR_KIND = {
  switch_out: "switch", switch_in: "switch",
  systematic_transfer_out: "systematic_transfer", systematic_transfer_in: "systematic_transfer",
  platform_transfer_out: "platform_transfer", platform_transfer_in: "platform_transfer",
} as const;

/** Purchase kinds are "funded" once `fundingComplete`; every other kind's own cash link (role
 * `proceeds`, `cash_distribution` or `fee` — never `funding`) is read straight off the holding's
 * own `cashLinks` rather than restating the server's remaining-amount arithmetic. */
export function isFundedEvent(event: HoldingEventRow, cashLinks: Array<HoldingCashLink>): boolean {
  return PURCHASE_KINDS.has(event.eventKind)
    ? event.fundingComplete
    : cashLinks.some((link) => link.investmentEventId === event.id);
}

type CorrectionRequest = {
  path: string;
  body: AcquisitionCorrectionCreate | CashLifecycleCorrectionCreate | UnitActionCorrectionCreate;
};

/** The exact route and body for correcting one active event, built only from facts the holding
 * detail already returned (Task 2b) — nothing here is recomputed or guessed. Returns undefined
 * when the event's own pair data is missing, which disables the action rather than guessing. */
export function correctionRequest(
  event: HoldingEventRow,
  holding: HoldingDetail,
  idempotencyKey: string,
  correctionReason: string,
): CorrectionRequest | undefined {
  const eventDate = event.eventDate;
  const currency = event.currency;
  const expectedCorrectionState = "uncorrected" as const;

  if (PURCHASE_KINDS.has(event.eventKind)) {
    if (event.units === null || event.price === null || event.grossAmount === null
      || event.charges === null || event.withheldTax === null || event.netAmount === null) return undefined;
    const body: AcquisitionCorrectionCreate = {
      accountId: holding.accountId,
      securityId: holding.securityId,
      eventDate,
      eventKind: event.eventKind as AcquisitionCorrectionCreate["eventKind"],
      units: event.units,
      price: event.price,
      grossAmount: event.grossAmount,
      charges: event.charges,
      withheldTax: event.withheldTax,
      netAmount: event.netAmount,
      currency,
      idempotencyKey,
      expectedCorrectionState,
      correctionReason,
    };
    return { path: `/investment-events/${event.id}/corrections`, body };
  }

  if (CASH_LIFECYCLE_KINDS.has(event.eventKind)) {
    if (event.charges === null || event.withheldTax === null || event.netAmount === null) return undefined;
    const body: CashLifecycleCorrectionCreate = {
      accountId: holding.accountId,
      securityId: holding.securityId,
      eventDate,
      eventKind: event.eventKind as CashLifecycleCorrectionCreate["eventKind"],
      units: event.units,
      price: event.price,
      grossAmount: event.grossAmount,
      charges: event.charges,
      withheldTax: event.withheldTax,
      netAmount: event.netAmount,
      currency,
      idempotencyKey,
      expectedCorrectionState,
      correctionReason,
    };
    return { path: `/investment-events/${event.id}/cash-lifecycle-corrections`, body };
  }

  if (event.eventKind === "bonus") {
    if (event.units === null) return undefined;
    const body: UnitActionCorrectionCreate = {
      kind: "bonus", accountId: holding.accountId, securityId: holding.securityId, eventDate,
      units: event.units, currency, idempotencyKey, expectedCorrectionState, correctionReason,
    };
    return { path: `/investment-events/${event.id}/unit-action-corrections`, body };
  }
  if (event.eventKind === "rights") {
    if (event.units === null || event.price === null || event.grossAmount === null
      || event.charges === null || event.withheldTax === null || event.netAmount === null) return undefined;
    const body: UnitActionCorrectionCreate = {
      kind: "rights", accountId: holding.accountId, securityId: holding.securityId, eventDate,
      units: event.units, price: event.price, grossAmount: event.grossAmount, charges: event.charges,
      withheldTax: event.withheldTax, netAmount: event.netAmount, currency,
      idempotencyKey, expectedCorrectionState, correctionReason,
    };
    return { path: `/investment-events/${event.id}/unit-action-corrections`, body };
  }
  if (event.eventKind === "split") {
    if (event.unitRatio === null) return undefined;
    const body: UnitActionCorrectionCreate = {
      kind: "split", accountId: holding.accountId, securityId: holding.securityId, eventDate,
      unitRatio: event.unitRatio, idempotencyKey, expectedCorrectionState, correctionReason,
    };
    return { path: `/investment-events/${event.id}/unit-action-corrections`, body };
  }

  const pairKind = (PAIR_KIND as Record<string, "switch" | "systematic_transfer" | "platform_transfer" | undefined>)[event.eventKind];
  if (!pairKind) return undefined;
  const isOutLeg = event.eventKind.endsWith("_out");
  const outLegId = isOutLeg ? event.id : event.counterpartEventId;
  if (!outLegId) return undefined;
  const out = isOutLeg
    ? { accountId: holding.accountId, securityId: holding.securityId, units: event.units, price: event.price, grossAmount: event.grossAmount }
    : { accountId: event.counterpartAccountId, securityId: event.counterpartSecurityId, units: event.counterpartUnits, price: event.counterpartPrice, grossAmount: event.counterpartGrossAmount };
  const inLeg = isOutLeg
    ? { accountId: event.counterpartAccountId, securityId: event.counterpartSecurityId, units: event.counterpartUnits, price: event.counterpartPrice, grossAmount: event.counterpartGrossAmount }
    : { accountId: holding.accountId, securityId: holding.securityId, units: event.units, price: event.price, grossAmount: event.grossAmount };

  if (pairKind === "platform_transfer") {
    if (out.accountId === null || inLeg.accountId === null || out.securityId === null
      || out.units === null || inLeg.units === null) return undefined;
    const body: UnitActionCorrectionCreate = {
      kind: "platform_transfer",
      outAccountId: out.accountId, inAccountId: inLeg.accountId, securityId: out.securityId,
      eventDate, outUnits: out.units, inUnits: inLeg.units, currency,
      idempotencyKey, expectedCorrectionState, correctionReason,
    };
    return { path: `/investment-events/${outLegId}/unit-action-corrections`, body };
  }
  if (out.accountId === null || out.securityId === null || inLeg.securityId === null
    || out.units === null || out.price === null || out.grossAmount === null
    || inLeg.units === null || inLeg.price === null || inLeg.grossAmount === null) return undefined;
  const body: UnitActionCorrectionCreate = {
    kind: pairKind, accountId: out.accountId, outSecurityId: out.securityId, inSecurityId: inLeg.securityId,
    eventDate, outUnits: out.units, outPrice: out.price, outGrossAmount: out.grossAmount,
    inUnits: inLeg.units, inPrice: inLeg.price, inGrossAmount: inLeg.grossAmount, currency,
    idempotencyKey, expectedCorrectionState, correctionReason,
  };
  return { path: `/investment-events/${outLegId}/unit-action-corrections`, body };
}

export function EventCorrectionForm({ event, holding, request, onCorrected }: Props) {
  const [reason, setReason] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  function changeReason(value: string) {
    setReason(value);
    setIdempotencyKey("");
    setConfirming(false);
    setProblem("");
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
    const built = correctionRequest(event, holding, key, reason.trim());
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

  const preview = idempotencyKey ? correctionRequest(event, holding, idempotencyKey, reason.trim()) : undefined;

  return (
    <fieldset aria-label={`Correct the ${event.eventKind.replaceAll("_", " ")} on ${event.eventDate}`} className="investment-correction-form">
      <legend>Correct this event</legend>
      <label className="text-field">
        <span>Correction reason</span>
        <input onChange={(change) => changeReason(change.target.value)} required value={reason} />
      </label>
      {!confirming && (
        <button className="text-action" disabled={!reason.trim()} onClick={review} type="button">
          Review correction
        </button>
      )}
      {confirming && (
        <div className="investment-confirmation">
          <p>This appends a new owner-entered event replacing the one above. It cannot be edited afterward.</p>
          {!preview && <p className="inline-alert" role="alert">This event&rsquo;s pair data is incomplete — correct it from its other leg&rsquo;s holding instead.</p>}
          {problem && <p className="inline-alert" role="alert">{problem}</p>}
          <div className="investment-confirm-actions">
            <button className="primary-action" disabled={busy || !preview} onClick={() => void submit()} type="button">
              {busy ? "Recording correction…" : problem ? "Retry correction" : "Confirm correction"}
            </button>
            <button className="text-action" disabled={busy} onClick={() => { setConfirming(false); setProblem(""); }} type="button">
              Go back
            </button>
          </div>
        </div>
      )}
    </fieldset>
  );
}
