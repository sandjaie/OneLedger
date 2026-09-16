// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SubscriptionsScreen } from "@/app/subscriptions/subscriptions-screen";
import { type apiFetch } from "@/lib/api/client";
import type {
  ByAccountResponse,
  CurrencyRow,
  RecurringResponse,
  RecurringSeriesRow,
  RecurringSummary,
} from "@/types/api";

type Request = typeof apiFetch;

const CURRENCIES: Array<CurrencyRow> = [
  { code: "USD", name: "US Dollar", symbol: "$", decimals: 4, numberFormat: "international" },
  { code: "XIN", name: "Synthetic Indian", symbol: "₹", decimals: 4, numberFormat: "indian" },
];

function aSeries(overrides: Partial<RecurringSeriesRow> = {}): RecurringSeriesRow {
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
    periodCount: 1,
    resumed: false,
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
    ...overrides,
  };
}

function aSummary(overrides: Partial<RecurringSummary> = {}): RecurringSummary {
  return {
    monthlyBase: "15.0000",
    annualBase: "180.0000",
    currentCount: 1,
    renewingIn30Days: 1,
    priceChangesIn90Days: 0,
    baseCurrency: "USD",
    upcoming: [
      {
        seriesId: "series-1",
        label: "Synthetic streaming service",
        nextExpectedOn: "2026-09-01",
        nativeAmount: "15.0000",
        nativeCurrency: "USD",
        payingAccountName: "Everyday card",
      },
    ],
    ...overrides,
  };
}

const emptyByAccount: ByAccountResponse = { accounts: [] };

function fixedRequest(
  series: Array<RecurringSeriesRow>,
  summary: RecurringSummary,
  byAccount: ByAccountResponse = emptyByAccount,
) {
  return vi.fn((path: string) => {
    if (path === "/recurring/refresh") return Promise.resolve({});
    if (path === "/recurring") return Promise.resolve<RecurringResponse>({ series });
    if (path === "/recurring/summary") return Promise.resolve(summary);
    if (path === "/recurring/by-account") return Promise.resolve(byAccount);
    throw new Error(`unexpected path ${path}`);
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("subscriptions screen", () => {
  it("refreshes once on open, then renders the summary, upcoming and current lists from server strings", async () => {
    const series = [aSeries()];
    const summary = aSummary();
    const request = fixedRequest(series, summary);

    render(<SubscriptionsScreen currencies={CURRENCIES} request={request as Request} />);

    await screen.findAllByText(/Synthetic streaming service/);
    expect(screen.getAllByText(/\$15\.0000/).length).toBeGreaterThan(0);
    const annualCard = screen.getByText("Annual spend").parentElement!;
    expect(within(annualCard).getByText("$180.0000")).toBeTruthy();

    expect(request.mock.calls[0][0]).toBe("/recurring/refresh");
    const [, refreshInit] = request.mock.calls[0] as unknown as [string, RequestInit];
    expect(refreshInit).toEqual(expect.objectContaining({ method: "POST" }));
    expect(request.mock.calls.map((call) => call[0])).toEqual(
      expect.arrayContaining(["/recurring", "/recurring/summary", "/recurring/by-account"]),
    );
    expect(request).toHaveBeenCalledTimes(4);
  });

  it('"not a subscription" patches isSubscription and the row leaves Current', async () => {
    const series = [aSeries()];
    const summary = aSummary();
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring/summary") return Promise.resolve(summary);
      if (path === "/recurring/by-account") return Promise.resolve(emptyByAccount);
      if (path === "/recurring") return Promise.resolve<RecurringResponse>({ series });
      if (path === "/recurring/series-1") return Promise.resolve(aSeries());
      throw new Error(`unexpected path ${path}`);
    });

    render(<SubscriptionsScreen currencies={CURRENCIES} request={request as Request} />);
    await screen.findAllByText(/Synthetic streaming service/);

    fireEvent.click(screen.getByRole("button", { name: "Not a subscription" }));

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/recurring/series-1",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({ isSubscription: false }),
        }),
      ),
    );
  });

  it("asks before cancelling, and moves the row to Past on confirmation", async () => {
    const series = [aSeries()];
    const summary = aSummary();
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring/summary") return Promise.resolve(summary);
      if (path === "/recurring/by-account") return Promise.resolve(emptyByAccount);
      if (path === "/recurring") return Promise.resolve<RecurringResponse>({ series });
      if (path === "/recurring/series-1/cancel") {
        series[0] = aSeries({
          isCurrent: false,
          status: "ended",
          lastPeriodEndedOn: "2026-08-15",
          lastEndReason: "cancelled",
        });
        return Promise.resolve(series[0]);
      }
      throw new Error(`unexpected path ${path}`);
    });
    const confirm = vi.spyOn(window, "confirm");

    render(<SubscriptionsScreen currencies={CURRENCIES} request={request as Request} />);
    await screen.findAllByText(/Synthetic streaming service/);

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
    await screen.findByText(/ended 2026-08-15/);
    expect(screen.getByText(/\(cancelled\)/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();
  });

  it("resumes a past subscription, moving it back to Current", async () => {
    const past = aSeries({
      isCurrent: false,
      status: "ended",
      lastPeriodEndedOn: "2026-08-15",
      lastEndReason: "cancelled",
    });
    const series = [past];
    const summary = aSummary({ currentCount: 0, upcoming: [] });
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring/summary") return Promise.resolve(summary);
      if (path === "/recurring/by-account") return Promise.resolve(emptyByAccount);
      if (path === "/recurring") return Promise.resolve<RecurringResponse>({ series });
      if (path === "/recurring/series-1/resume") {
        series[0] = aSeries();
        return Promise.resolve(series[0]);
      }
      throw new Error(`unexpected path ${path}`);
    });

    render(<SubscriptionsScreen currencies={CURRENCIES} request={request as Request} />);
    await screen.findAllByText(/Synthetic streaming service/);
    expect(screen.getByRole("button", { name: "Resume" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Resume" }));

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/recurring/series-1/resume",
        expect.objectContaining({ method: "POST", body: JSON.stringify({}) }),
      ),
    );
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy());
  });

  it("renders by-card grouping with masked identifiers", async () => {
    const series = [aSeries()];
    const summary = aSummary();
    const byAccount: ByAccountResponse = {
      accounts: [
        {
          accountId: "acct-1",
          accountName: "Everyday card",
          accountMasked: "•••• 4242",
          count: 1,
          labels: ["Synthetic streaming service"],
          monthlyBase: "15.0000",
        },
      ],
    };
    const request = fixedRequest(series, summary, byAccount);

    render(<SubscriptionsScreen currencies={CURRENCIES} request={request as Request} />);
    await screen.findByText("By card");
    const byCardList = screen.getByText("By card").nextElementSibling as HTMLElement;
    expect(within(byCardList).getByText(/Everyday card \(•••• 4242\)/)).toBeTruthy();
    expect(within(byCardList).getByText(/1 subscription · Synthetic streaming service/)).toBeTruthy();
  });

  it("shows nothing found for every list when there are no series", async () => {
    const summary = aSummary({ currentCount: 0, upcoming: [] });
    const request = fixedRequest([], summary);

    render(<SubscriptionsScreen currencies={CURRENCIES} request={request as Request} />);
    expect(await screen.findByText(/Nothing is expected to renew/)).toBeTruthy();
    expect(screen.getByText(/No current subscription has been found yet/)).toBeTruthy();
    expect(screen.getByText(/No past subscription is recorded/)).toBeTruthy();
    expect(screen.getByText(/No subscription is linked to a card yet/)).toBeTruthy();
  });
});
