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
    ...overrides,
  };
}

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(cleanup);

describe("correctionRequest", () => {
  it("posts a cash lifecycle correction with the holding's own account and security", () => {
    const built = correctionRequest(event({ eventKind: "sale" }), HOLDING, "key-1", "Mistyped price");
    expect(built?.path).toBe("/investment-events/00000000-0000-4000-8000-000000000060/cash-lifecycle-corrections");
    expect(built?.body).toEqual({
      accountId: HOLDING.accountId,
      securityId: HOLDING.securityId,
      eventDate: "2026-02-01",
      eventKind: "sale",
      units: "-1.00000000",
      price: "6.000000",
      grossAmount: "6.0000",
      charges: "0.0000",
      withheldTax: "0.0000",
      netAmount: "6.0000",
      currency: "XAA",
      idempotencyKey: "key-1",
      expectedCorrectionState: "uncorrected",
      correctionReason: "Mistyped price",
    });
  });

  it("posts an acquisition correction to the acquisitions route", () => {
    const built = correctionRequest(
      event({ eventKind: "purchase", units: "4.00000000", grossAmount: "20.0000", netAmount: "-21.0000" }),
      HOLDING, "key-2", "Wrong units",
    );
    expect(built?.path).toBe("/investment-events/00000000-0000-4000-8000-000000000060/corrections");
    expect(built?.body).toMatchObject({ eventKind: "purchase", units: "4.00000000", correctionReason: "Wrong units" });
  });

  it("posts a split correction with only its own unitRatio field", () => {
    const built = correctionRequest(
      event({ eventKind: "split", units: null, price: null, grossAmount: null, netAmount: null, unitRatio: "2" }),
      HOLDING, "key-3", "Wrong ratio",
    );
    expect(built?.path).toBe("/investment-events/00000000-0000-4000-8000-000000000060/unit-action-corrections");
    expect(built?.body).toEqual({
      kind: "split", accountId: HOLDING.accountId, securityId: HOLDING.securityId, eventDate: "2026-02-01",
      unitRatio: "2", idempotencyKey: "key-3", expectedCorrectionState: "uncorrected", correctionReason: "Wrong ratio",
    });
  });

  it("corrects a switch's out leg through its own id, naming the counterpart as the in leg", () => {
    const outLeg = event({
      id: "out-leg", eventKind: "switch_out", units: "-4.00000000", price: "10.000000", grossAmount: "40.0000",
      netAmount: null, counterpartEventId: "in-leg",
      counterpartAccountId: HOLDING.accountId, counterpartSecurityId: "security-2",
      counterpartUnits: "8.00000000", counterpartPrice: "5.000000", counterpartGrossAmount: "40.0000",
    });

    const built = correctionRequest(outLeg, HOLDING, "key-4", "Wrong ratio");

    expect(built?.path).toBe("/investment-events/out-leg/unit-action-corrections");
    expect(built?.body).toMatchObject({
      kind: "switch", accountId: HOLDING.accountId,
      outSecurityId: HOLDING.securityId, inSecurityId: "security-2",
      outUnits: "-4.00000000", outPrice: "10.000000", outGrossAmount: "40.0000",
      inUnits: "8.00000000", inPrice: "5.000000", inGrossAmount: "40.0000",
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

    const built = correctionRequest(inLeg, holdingForInLeg, "key-5", "Wrong ratio");

    expect(built?.path).toBe("/investment-events/out-leg/unit-action-corrections");
    expect(built?.body).toMatchObject({
      kind: "switch", outSecurityId: "security-1", inSecurityId: "security-2",
      outUnits: "-4.00000000", inUnits: "8.00000000",
    });
  });

  it("corrects a platform transfer's out leg with the counterpart's own account", () => {
    const outLeg = event({
      id: "out-leg", eventKind: "platform_transfer_out", units: "-6.00000000", price: null, grossAmount: null,
      netAmount: null, counterpartEventId: "in-leg",
      counterpartAccountId: "account-2", counterpartSecurityId: HOLDING.securityId,
      counterpartUnits: "6.00000000", counterpartPrice: null, counterpartGrossAmount: null,
    });

    const built = correctionRequest(outLeg, HOLDING, "key-6", "Wrong account");

    expect(built?.body).toMatchObject({
      kind: "platform_transfer", outAccountId: HOLDING.accountId, inAccountId: "account-2",
      securityId: HOLDING.securityId, outUnits: "-6.00000000", inUnits: "6.00000000",
    });
  });

  it("returns undefined when a paired leg's own counterpart data is missing", () => {
    const orphaned = event({ eventKind: "switch_out", counterpartEventId: null });
    expect(correctionRequest(orphaned, HOLDING, "key-7", "reason")).toBeUndefined();
  });
});

describe("isFundedEvent", () => {
  const links: Array<HoldingCashLink> = [
    { investmentEventId: "sale-1", cashTransactionId: "cash-1", role: "proceeds", allocatedAmount: "6.0000", currency: "XAA" },
  ];

  it("reads a purchase's funded state off fundingComplete", () => {
    expect(isFundedEvent(event({ eventKind: "purchase", fundingComplete: true }), [])).toBe(true);
    expect(isFundedEvent(event({ eventKind: "purchase", fundingComplete: false }), links)).toBe(false);
  });

  it("reads any other kind's funded state off its own cash links, any role", () => {
    expect(isFundedEvent(event({ id: "sale-1", eventKind: "sale" }), links)).toBe(true);
    expect(isFundedEvent(event({ id: "sale-2", eventKind: "sale" }), links)).toBe(false);
  });
});

describe("EventCorrectionForm", () => {
  it("submits the built correction and reuses the same idempotency key on a retry, a new one after editing the reason", async () => {
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
    fireEvent.change(screen.getByLabelText("Correction reason"), { target: { value: "Mistyped price" } });
    fireEvent.click(screen.getByRole("button", { name: "Review correction" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm correction" }));

    await screen.findByRole("button", { name: "Retry correction" });
    fireEvent.click(screen.getByRole("button", { name: "Retry correction" }));
    await vi.waitFor(() => expect(onCorrected).toHaveBeenCalled());

    expect(posted).toHaveLength(2);
    const firstKey = (JSON.parse(posted[0].body) as { idempotencyKey: string }).idempotencyKey;
    const secondKey = (JSON.parse(posted[1].body) as { idempotencyKey: string }).idempotencyKey;
    expect(secondKey).toBe(firstKey);

    // A changed reason is a new draft: it must not resend the failed attempt's key.
    fireEvent.change(screen.getByLabelText("Correction reason"), { target: { value: "Different reason entirely" } });
    fireEvent.click(screen.getByRole("button", { name: "Review correction" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm correction" }));
    await vi.waitFor(() => expect(posted).toHaveLength(3));
    const thirdKey = (JSON.parse(posted[2].body) as { idempotencyKey: string }).idempotencyKey;
    expect(thirdKey).not.toBe(firstKey);
  });
});
