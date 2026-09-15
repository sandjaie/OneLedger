// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { correctionRequest, EventCorrectionForm, isFundedEvent } from "@/app/investments/event-correction-form";
import type { apiFetch } from "@/lib/api/client";
import type { HoldingCashLink, HoldingDetail, HoldingEventRow } from "@/types/api";

type Request = typeof apiFetch;

const HOLDING: HoldingDetail = {
  holdingId: "00000000-0000-4000-8000-000000000050",
  accountId: "00000000-0000-4000-8000-000000000010",
  securityId: "00000000-0000-4000-8000-000000000040",
  securityName: "Synthetic Growth Fund",
  assetClass: "equity",
  currency: "XAA",
  units: "10.00000000",
  openedOn: "2026-01-05",
  closedOn: null,
  events: [],
  eventsTotal: 0,
  cashLinks: [],
  prices: [],
  openReviewReasons: [],
};

function event(overrides: Partial<HoldingEventRow> = {}): HoldingEventRow {
  return {
    id: "00000000-0000-4000-8000-000000000060",
    eventDate: "2026-02-01",
    eventKind: "sale",
    units: "-1.00000000",
    unitRatio: null,
    price: "6.000000",
    grossAmount: "6.0000",
    charges: "0.0000",
    withheldTax: "0.0000",
    netAmount: "6.0000",
    currency: "XAA",
    correctsEventId: null,
    counterpartEventId: null,
    correctionState: "active",
    fundingComplete: true,
    counterpartAccountId: null,
    counterpartSecurityId: null,
    counterpartUnits: null,
    counterpartPrice: null,
    counterpartGrossAmount: null,
    lotAllocationOpen: false,
    ...overrides,
  };
}

const SALE_DRAFT = { units: "-1.00000000", price: "6.000000", grossAmount: "6.0000", charges: "0.0000", withheldTax: "0.0000", netAmount: "6.0000", unitRatio: "", outUnits: "", outPrice: "", outGrossAmount: "", inUnits: "", inPrice: "", inGrossAmount: "" };

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(cleanup);

describe("correctionRequest", () => {
  it("sends the owner's edited draft, not the event's original stored values", () => {
    const built = correctionRequest(
      event({ eventKind: "sale" }), HOLDING,
      { ...SALE_DRAFT, units: "-2.00000000", grossAmount: "12.0000", netAmount: "12.0000" },
      "key-1", "Mistyped units",
    );
    expect(built?.path).toBe("/investment-events/00000000-0000-4000-8000-000000000060/cash-lifecycle-corrections");
    expect(built?.body).toEqual({
      accountId: HOLDING.accountId, securityId: HOLDING.securityId, eventDate: "2026-02-01", eventKind: "sale",
      units: "-2.00000000", price: "6.000000", grossAmount: "12.0000", charges: "0.0000", withheldTax: "0.0000",
      netAmount: "12.0000", currency: "XAA", idempotencyKey: "key-1",
      expectedCorrectionState: "uncorrected", correctionReason: "Mistyped units",
    });
  });

  it("posts an acquisition correction to the acquisitions route", () => {
    const built = correctionRequest(
      event({ eventKind: "purchase", units: "4.00000000", grossAmount: "20.0000", netAmount: "-21.0000" }),
      HOLDING, { ...SALE_DRAFT, units: "4.00000000", grossAmount: "20.0000", netAmount: "-21.0000" }, "key-2", "Wrong units",
    );
    expect(built?.path).toBe("/investment-events/00000000-0000-4000-8000-000000000060/corrections");
    expect(built?.body).toMatchObject({ eventKind: "purchase", units: "4.00000000", correctionReason: "Wrong units" });
  });

  it("posts a split correction with only its own edited unitRatio field", () => {
    const built = correctionRequest(
      event({ eventKind: "split", units: null, price: null, grossAmount: null, netAmount: null, unitRatio: "2" }),
      HOLDING, { ...SALE_DRAFT, unitRatio: "3" }, "key-3", "Wrong ratio",
    );
    expect(built?.path).toBe("/investment-events/00000000-0000-4000-8000-000000000060/unit-action-corrections");
    expect(built?.body).toEqual({
      kind: "split", accountId: HOLDING.accountId, securityId: HOLDING.securityId, eventDate: "2026-02-01",
      unitRatio: "3", idempotencyKey: "key-3", expectedCorrectionState: "uncorrected", correctionReason: "Wrong ratio",
    });
  });

  it("corrects a switch's out leg through its own id, sending an edited in-leg field", () => {
    const outLeg = event({
      id: "out-leg", eventKind: "switch_out", units: "-4.00000000", price: "10.000000", grossAmount: "40.0000",
      netAmount: null, counterpartEventId: "in-leg",
      counterpartAccountId: HOLDING.accountId, counterpartSecurityId: "security-2",
      counterpartUnits: "8.00000000", counterpartPrice: "5.000000", counterpartGrossAmount: "40.0000",
    });

    const built = correctionRequest(
      outLeg, HOLDING,
      { ...SALE_DRAFT, outUnits: "-4.00000000", outPrice: "10.000000", outGrossAmount: "40.0000", inUnits: "9.00000000", inPrice: "5.000000", inGrossAmount: "45.0000" },
      "key-4", "Mistyped in units",
    );

    expect(built?.path).toBe("/investment-events/out-leg/unit-action-corrections");
    expect(built?.body).toMatchObject({
      kind: "switch", accountId: HOLDING.accountId,
      outSecurityId: HOLDING.securityId, inSecurityId: "security-2",
      outUnits: "-4.00000000", outPrice: "10.000000", outGrossAmount: "40.0000",
      inUnits: "9.00000000", inPrice: "5.000000", inGrossAmount: "45.0000",
    });
  });

  it("corrects a switch's in leg through the out leg's id, not its own", () => {
    const inLeg = event({
      id: "in-leg", eventKind: "switch_in", units: "8.00000000", price: "5.000000", grossAmount: "40.0000",
      netAmount: null, counterpartEventId: "out-leg",
      counterpartAccountId: HOLDING.accountId, counterpartSecurityId: "security-1",
      counterpartUnits: "-4.00000000", counterpartPrice: "10.000000", counterpartGrossAmount: "40.0000",
    });
    const holdingForInLeg: HoldingDetail = { ...HOLDING, securityId: "security-2" };

    const built = correctionRequest(
      inLeg, holdingForInLeg,
      { ...SALE_DRAFT, outUnits: "-4.00000000", outPrice: "10.000000", outGrossAmount: "40.0000", inUnits: "8.00000000", inPrice: "5.000000", inGrossAmount: "40.0000" },
      "key-5", "reason",
    );

    expect(built?.path).toBe("/investment-events/out-leg/unit-action-corrections");
    expect(built?.body).toMatchObject({ kind: "switch", outSecurityId: "security-1", inSecurityId: "security-2" });
  });

  it("corrects a platform transfer's out leg on its stored receiving account", () => {
    const outLeg = event({
      id: "out-leg", eventKind: "platform_transfer_out", units: "-6.00000000", price: null, grossAmount: null,
      netAmount: null, counterpartEventId: "in-leg",
      counterpartAccountId: "account-2", counterpartSecurityId: HOLDING.securityId,
      counterpartUnits: "6.00000000", counterpartPrice: null, counterpartGrossAmount: null,
    });

    const built = correctionRequest(
      outLeg, HOLDING,
      { ...SALE_DRAFT, outUnits: "-6.00000000", inUnits: "6.00000000" },
      "key-6", "Wrong account",
    );

    expect(built?.body).toMatchObject({
      kind: "platform_transfer", outAccountId: HOLDING.accountId, inAccountId: "account-2",
      securityId: HOLDING.securityId, outUnits: "-6.00000000", inUnits: "6.00000000",
    });
  });

  it("returns undefined when a paired leg's own counterpart data is missing", () => {
    const orphaned = event({ eventKind: "switch_out", counterpartEventId: null });
    expect(correctionRequest(orphaned, HOLDING, SALE_DRAFT, "key-7", "reason")).toBeUndefined();
  });
});

describe("isFundedEvent", () => {
  it("is true for any kind, including a purchase, with an existing cash link of any role", () => {
    const links: Array<HoldingCashLink> = [
      { investmentEventId: "purchase-1", cashTransactionId: "cash-1", role: "funding", allocatedAmount: "10.0000", currency: "XAA" },
      { investmentEventId: "sale-1", cashTransactionId: "cash-2", role: "proceeds", allocatedAmount: "6.0000", currency: "XAA" },
    ];
    // A purchase that is only partly funded (`fundingComplete: false`) still refuses correction
    // once any allocation exists — the V40 trigger, not the remaining-amount arithmetic, decides.
    expect(isFundedEvent(event({ id: "purchase-1", eventKind: "purchase", fundingComplete: false }), links)).toBe(true);
    expect(isFundedEvent(event({ id: "sale-1", eventKind: "sale" }), links)).toBe(true);
    expect(isFundedEvent(event({ id: "sale-2", eventKind: "sale" }), links)).toBe(false);
  });
});

describe("EventCorrectionForm", () => {
  it("pre-fills the event's own units, lets the owner edit them, and posts the edited string with a new key; an unchanged retry reuses it", async () => {
    const posted: Array<{ body: string }> = [];
    let attempts = 0;
    const request = requestFrom((path, init) => {
      if (path === "/investment-events/00000000-0000-4000-8000-000000000060/cash-lifecycle-corrections" && init?.method === "POST") {
        posted.push({ body: String(init.body) });
        attempts += 1;
        if (attempts < 2) throw new Error("network hiccup");
        return event();
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });
    const onCorrected = vi.fn();

    render(<EventCorrectionForm event={event()} holding={HOLDING} onCorrected={onCorrected} request={request} />);

    expect(screen.getByLabelText("Units")).toHaveProperty("value", "-1.00000000");
    fireEvent.change(screen.getByLabelText("Units"), { target: { value: "-2.00000000" } });
    fireEvent.change(screen.getByLabelText("Correction reason"), { target: { value: "Mistyped units" } });
    fireEvent.click(screen.getByRole("button", { name: "Review correction" }));
    expect(await screen.findByText("-2.00000000")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Confirm correction" }));

    await screen.findByRole("button", { name: "Retry correction" });
    fireEvent.click(screen.getByRole("button", { name: "Retry correction" }));
    await vi.waitFor(() => expect(onCorrected).toHaveBeenCalled());

    expect(posted).toHaveLength(2);
    const first = JSON.parse(posted[0].body) as { units: string; idempotencyKey: string };
    const second = JSON.parse(posted[1].body) as { units: string; idempotencyKey: string };
    expect(first.units).toBe("-2.00000000");
    expect(second.idempotencyKey).toBe(first.idempotencyKey);
  });

  it("issues a new idempotency key once the owner edits a field after review", async () => {
    const posted: Array<{ idempotencyKey: string }> = [];
    const request = requestFrom((path, init) => {
      if (path === "/investment-events/00000000-0000-4000-8000-000000000060/cash-lifecycle-corrections" && init?.method === "POST") {
        posted.push(JSON.parse(String(init.body)) as { idempotencyKey: string });
        return event();
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(<EventCorrectionForm event={event()} holding={HOLDING} onCorrected={vi.fn()} request={request} />);
    fireEvent.change(screen.getByLabelText("Correction reason"), { target: { value: "First reason" } });
    fireEvent.click(screen.getByRole("button", { name: "Review correction" }));
    fireEvent.click(screen.getByRole("button", { name: "Go back" }));
    fireEvent.change(screen.getByLabelText("Units"), { target: { value: "-3.00000000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review correction" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm correction" }));

    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].idempotencyKey).toBeTruthy();
  });
});
