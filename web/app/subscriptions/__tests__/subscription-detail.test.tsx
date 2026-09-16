// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SubscriptionDetail } from "@/app/subscriptions/subscription-detail";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { CurrencyRow, RecurringSeriesDetail } from "@/types/api";

type Request = typeof apiFetch;

const CURRENCIES: Array<CurrencyRow> = [
  { code: "USD", name: "US Dollar", symbol: "$", decimals: 4, numberFormat: "international" },
];

const CHANNELS = { channels: [{ id: "chan-1", name: "App store", sortOrder: 1 }] };

function aDetail(overrides: Partial<RecurringSeriesDetail> = {}): RecurringSeriesDetail {
  return {
    id: "series-1",
    label: "Synthetic streaming service",
    identity: { merchantId: null, matchKey: "synthetic streaming service" },
    cadence: "monthly",
    customIntervalDays: null,
    lifecycle: "active",
    categoryId: null,
    categoryName: "Streaming",
    isSubscription: true,
    detectionMethod: "detected",
    needsName: false,
    nativeAmount: "15.0000",
    nativeCurrency: "USD",
    priceChangedFrom: null,
    priceChangedOn: null,
    lastSeenOn: "2026-08-01",
    isCurrent: true,
    currentPeriodStartedOn: "2026-06-01",
    lastPeriodEndedOn: null,
    lastEndReason: null,
    periodCount: 2,
    resumed: true,
    nextExpectedOn: "2026-09-01",
    status: "active",
    paymentChannelId: null,
    paymentChannelName: null,
    payingAccountId: "acct-1",
    payingAccountName: "Everyday card",
    payingAccountMasked: "•••• 4242",
    annualAmount: "180.0000",
    monthlyAmount: "15.0000",
    annualAmountBase: "180.0000",
    monthlyAmountBase: "15.0000",
    firstSeenOn: "2026-01-03",
    spentSoFarBase: "90.0000",
    last12MonthsBase: "90.0000",
    periods: [
      {
        id: "period-1",
        startedOn: "2026-01-03",
        endedOn: "2026-03-03",
        endReason: "cancelled",
        chargeCount: 2,
        spentBase: "30.0000",
      },
      {
        id: "period-2",
        startedOn: "2026-06-01",
        endedOn: null,
        endReason: null,
        chargeCount: 3,
        spentBase: "60.0000",
      },
    ],
    priceHistory: [{ observedOn: "2026-01-03", amount: "15.0000", currency: "USD" }],
    charges: [
      {
        transactionId: "txn-1",
        observedOn: "2026-01-03",
        amount: "15.0000",
        currency: "USD",
        amountBase: "15.0000",
        accountName: "Everyday card",
        periodId: "period-1",
      },
      {
        transactionId: "txn-2",
        observedOn: "2026-02-03",
        amount: "15.0000",
        currency: "USD",
        amountBase: "15.0000",
        accountName: "Everyday card",
        periodId: "period-1",
      },
      {
        transactionId: "txn-3",
        observedOn: "2026-06-01",
        amount: "15.0000",
        currency: "USD",
        amountBase: "15.0000",
        accountName: "Everyday card",
        periodId: "period-2",
      },
    ],
    ...overrides,
  };
}

function fixedRequest(detail: RecurringSeriesDetail) {
  return vi.fn((path: string) => {
    if (path === "/recurring/series-1") return Promise.resolve(detail);
    if (path === "/payment-channels") return Promise.resolve(CHANNELS);
    throw new Error(`unexpected path ${path}`);
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("subscription detail", () => {
  it("renders the amount, category, paying account, started and totals from server strings", async () => {
    const request = fixedRequest(aDetail());
    render(
      <SubscriptionDetail
        baseCurrency="USD"
        currencies={CURRENCIES}
        request={request as Request}
        seriesId="series-1"
      />,
    );

    expect(await screen.findByRole("heading", { name: "Synthetic streaming service" })).toBeTruthy();
    expect(screen.getByText("$15.0000 per monthly")).toBeTruthy();
    expect(screen.getByText("Streaming")).toBeTruthy();
    expect(screen.getByText(/Everyday card \(•••• 4242\)/)).toBeTruthy();
    expect(screen.getByText("2026-01-03")).toBeTruthy(); // started
    expect(screen.getAllByText("$90.0000").length).toBe(2); // spent so far and last 12 months
  });

  it("shows the timeline with two periods, their spend, and the charges grouped by period", async () => {
    const request = fixedRequest(aDetail());
    render(
      <SubscriptionDetail
        baseCurrency="USD"
        currencies={CURRENCIES}
        request={request as Request}
        seriesId="series-1"
      />,
    );
    await screen.findByRole("heading", { name: "Synthetic streaming service" });

    const timeline = screen.getByText("Billing periods").nextElementSibling as HTMLElement;
    const periods = within(timeline).getAllByRole("listitem");
    // Includes each period's nested charge <li>s too; only a lower bound is asserted below.
    expect(within(timeline).getByText(/2026-01-03 – 2026-03-03 · cancelled/)).toBeTruthy();
    expect(within(timeline).getByText(/2 charges · \$30\.0000/)).toBeTruthy();
    expect(within(timeline).getByText(/2026-06-01 – now · active, resumed/)).toBeTruthy();
    expect(within(timeline).getByText(/3 charges · \$60\.0000/)).toBeTruthy();
    expect(periods.length).toBeGreaterThanOrEqual(2);

    // Charges are grouped under their own period, each linking to the ledger.
    const firstPeriodLink = within(timeline).getByRole("link", { name: /2026-01-03 · \$15\.0000 · Everyday card/ });
    expect(firstPeriodLink.getAttribute("href")).toBe("/transactions?dateFrom=2026-01-03&dateTo=2026-01-03");
    expect(
      within(timeline).getByRole("link", { name: /2026-06-01 · \$15\.0000 · Everyday card/ }),
    ).toBeTruthy();
  });

  it("renders the price history", async () => {
    const request = fixedRequest(aDetail());
    render(
      <SubscriptionDetail
        baseCurrency="USD"
        currencies={CURRENCIES}
        request={request as Request}
        seriesId="series-1"
      />,
    );
    await screen.findByRole("heading", { name: "Synthetic streaming service" });
    const history = screen.getByText("Price history").nextElementSibling as HTMLElement;
    expect(within(history).getByText(/2026-01-03 · \$15\.0000/)).toBeTruthy();
  });

  it("asks before cancelling and posts the cancel", async () => {
    const detail = aDetail();
    const request = vi.fn((path: string) => {
      if (path === "/recurring/series-1" ) return Promise.resolve(detail);
      if (path === "/payment-channels") return Promise.resolve(CHANNELS);
      if (path === "/recurring/series-1/cancel") return Promise.resolve(detail);
      throw new Error(`unexpected path ${path}`);
    });
    const confirm = vi.spyOn(window, "confirm");

    render(
      <SubscriptionDetail
        baseCurrency="USD"
        currencies={CURRENCIES}
        request={request as Request}
        seriesId="series-1"
      />,
    );
    await screen.findByRole("heading", { name: "Synthetic streaming service" });

    confirm.mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(request).not.toHaveBeenCalledWith("/recurring/series-1/cancel", expect.anything());

    confirm.mockReturnValueOnce(true);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/recurring/series-1/cancel",
        expect.objectContaining({ method: "POST", body: JSON.stringify({}) }),
      ),
    );
  });

  it("saves the payment channel from the channel select", async () => {
    const detail = aDetail();
    const request = vi.fn((path: string) => {
      if (path === "/recurring/series-1") return Promise.resolve(detail);
      if (path === "/payment-channels") return Promise.resolve(CHANNELS);
      throw new Error(`unexpected path ${path}`);
    });

    render(
      <SubscriptionDetail
        baseCurrency="USD"
        currencies={CURRENCIES}
        request={request as Request}
        seriesId="series-1"
      />,
    );
    await screen.findByRole("heading", { name: "Synthetic streaming service" });

    fireEvent.change(screen.getByLabelText("Payment channel"), { target: { value: "chan-1" } });
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/recurring/series-1",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ paymentChannelId: "chan-1" }),
        }),
      ),
    );
  });

  it("shows a not-found state for an unknown or foreign series", async () => {
    const request = vi.fn((path: string) => {
      if (path === "/recurring/series-1") return Promise.reject(new ApiError(404, "series not found"));
      if (path === "/payment-channels") return Promise.resolve(CHANNELS);
      throw new Error(`unexpected path ${path}`);
    });

    render(
      <SubscriptionDetail
        baseCurrency="USD"
        currencies={CURRENCIES}
        request={request as Request}
        seriesId="series-1"
      />,
    );
    expect(await screen.findByText("This subscription could not be found.")).toBeTruthy();
  });
});
