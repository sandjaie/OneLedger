// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RecurringScreen } from "@/app/recurring/recurring-screen";
import { type apiFetch } from "@/lib/api/client";
import type { CurrencyRow, RecurringResponse, RecurringSeriesRow } from "@/types/api";

type Request = typeof apiFetch;

const CURRENCIES: Array<CurrencyRow> = [
  { code: "USD", name: "US Dollar", symbol: "$", decimals: 4, numberFormat: "international" },
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
    categoryName: null,
    // The Recurring screen now lists only payments that are not subscriptions (Task 7): a
    // subscription has its own screen, so the default fixture here matches what this screen
    // actually shows.
    isSubscription: false,
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
    payingAccountId: null,
    payingAccountName: null,
    payingAccountMasked: null,
    annualAmount: "180.0000",
    monthlyAmount: "15.0000",
    annualAmountBase: "180.0000",
    monthlyAmountBase: "15.0000",
    ...overrides,
  };
}

const empty: RecurringResponse = { series: [] };

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("recurring screen", () => {
  it("leads with the earliest dated non-subscription payment", async () => {
    const response: RecurringResponse = { series: [
      aSeries({ id: "later", label: "Synthetic later", nextExpectedOn: "2026-10-10" }),
      aSeries({ id: "earlier", label: "Synthetic earlier", nextExpectedOn: "2026-09-20" }),
      aSeries({ id: "subscription", label: "Synthetic subscription", isSubscription: true, nextExpectedOn: "2026-09-19" }),
    ] };
    const request = vi.fn((path: string) => path === "/recurring/refresh"
      ? Promise.resolve({}) : Promise.resolve(response));
    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);

    const next = await screen.findByRole("region", { name: "Next payment" });
    expect(next.textContent).toContain("Synthetic earlier");
    expect(next.textContent).toContain("2026-09-20");
    expect(next.textContent).not.toContain("Synthetic subscription");
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("separates Active and Ended and keeps lifecycle controls behind review", async () => {
    const response: RecurringResponse = { series: [
      aSeries({ id: "active", label: "Synthetic active" }),
      aSeries({ id: "ended", label: "Synthetic ended", isCurrent: false, status: "ended",
        lastPeriodEndedOn: "2026-08-15", lastEndReason: "lapsed" }),
    ] };
    const request = vi.fn((path: string) => path === "/recurring/refresh"
      ? Promise.resolve({}) : Promise.resolve(response));
    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);

    await screen.findByRole("button", { name: "Active" });
    expect(within(screen.getByRole("region", { name: "Active payments" })).getByText("Synthetic active")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Active payments" })).queryByText("Synthetic ended")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Ended" }));
    expect(within(screen.getByRole("region", { name: "Ended payments" })).getByText("Synthetic ended")).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Ended payments" })).queryByText("Synthetic active")).toBeNull();
    expect(screen.getByText(/ended 2026-08-15/)).toBeTruthy();
    expect(screen.queryByRole("combobox", { name: "Status" })).toBeNull();
  });

  it("refreshes once on open, then lists the returned series from server strings", async () => {
    const response: RecurringResponse = { series: [aSeries()] };
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(response);
      throw new Error(`unexpected path ${path}`);
    });

    const { rerender } = render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);

    const active = await screen.findByRole("region", { name: "Active payments" });
    expect(within(active).getByText("Synthetic streaming service")).toBeTruthy();
    expect(within(active).getByText(/\$15\.0000/)).toBeTruthy();
    expect(request).toHaveBeenCalledTimes(2);
    expect(request.mock.calls[0][0]).toBe("/recurring/refresh");
    const [, refreshInit] = request.mock.calls[0] as unknown as [string, RequestInit];
    expect(refreshInit).toEqual(expect.objectContaining({ method: "POST" }));
    expect(request.mock.calls[1][0]).toBe("/recurring");

    // A re-render with the same stable `request` must not run refresh again.
    rerender(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  });

  it("identifies the specific recurring row that needs an owner-supplied name", async () => {
    const response: RecurringResponse = { series: [
      aSeries({ id: "named", label: "Synthetic named payment" }),
      aSeries({ id: "needs-name", label: "Synthetic unidentified payment", needsName: true }),
    ] };
    const request = vi.fn((path: string) => path === "/recurring/refresh"
      ? Promise.resolve({}) : Promise.resolve(response));

    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);

    const row = (await screen.findByText("Synthetic unidentified payment")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Needs a name")).toBeTruthy();
    expect(within(screen.getByRole("row", { name: /Synthetic named payment/ }))
      .queryByText("Needs a name")).toBeNull();
  });

  it("flips the subscription flag by posting isSubscription", async () => {
    const response: RecurringResponse = { series: [aSeries({ isSubscription: false })] };
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(response);
      if (path === "/recurring/series-1") return Promise.resolve(aSeries({ isSubscription: true }));
      throw new Error(`unexpected path ${path}`);
    });

    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    await screen.findByRole("region", { name: "Active payments" });

    fireEvent.click(screen.getByRole("button", { name: "Manage Synthetic streaming service" }));
    fireEvent.click(screen.getByRole("button", { name: "A subscription" }));

    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/recurring/series-1",
        expect.objectContaining({ method: "PATCH", body: JSON.stringify({ isSubscription: true }) }),
      ),
    );
  });

  it("never lists a row already flagged a subscription — it has moved to Subscriptions", async () => {
    const response: RecurringResponse = { series: [aSeries({ isSubscription: true })] };
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(response);
      throw new Error(`unexpected path ${path}`);
    });

    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    expect(within(await screen.findByRole("region", { name: "Next payment" }))
      .getByText(/no recurring payment other than a subscription has been found yet/i)).toBeTruthy();
    expect(screen.queryByText(/Synthetic streaming service/)).toBeNull();
  });

  it("shows the paying account and channel when the series carries them", async () => {
    const response: RecurringResponse = {
      series: [
        aSeries({
          payingAccountName: "Everyday card",
          payingAccountMasked: "•••• 4242",
          paymentChannelName: "App store",
        }),
      ],
    };
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(response);
      throw new Error(`unexpected path ${path}`);
    });

    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    expect(await screen.findByText(/Everyday card \(•••• 4242\)/)).toBeTruthy();
    expect(screen.getByText("App store")).toBeTruthy();
  });

  it("asks before removing, and only posts remove on confirmation", async () => {
    const response: RecurringResponse = { series: [aSeries()] };
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(response);
      if (path === "/recurring/series-1/remove") return Promise.resolve({ removed: true });
      throw new Error(`unexpected path ${path}`);
    });
    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    await screen.findByRole("region", { name: "Active payments" });

    fireEvent.click(screen.getByRole("button", { name: "Manage Synthetic streaming service" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText('Remove Synthetic streaming service? This is not "not a subscription" — it stops tracking this payment, and it will not come back on its own.')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep payment" }));
    expect(request).not.toHaveBeenCalledWith(
      "/recurring/series-1/remove",
      expect.anything(),
    );
    await waitFor(() => expect(document.activeElement?.textContent).toBe("Remove"));

    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm remove" }));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/recurring/series-1/remove",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("asks before cancelling, and only posts cancel on confirmation", async () => {
    const response: RecurringResponse = { series: [aSeries()] };
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(response);
      if (path === "/recurring/series-1/cancel") return Promise.resolve(aSeries());
      throw new Error(`unexpected path ${path}`);
    });
    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    await screen.findByRole("region", { name: "Active payments" });

    fireEvent.click(screen.getByRole("button", { name: "Manage Synthetic streaming service" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText("Cancel Synthetic streaming service? This closes its current billing period.")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Keep payment" }));
    expect(request).not.toHaveBeenCalledWith("/recurring/series-1/cancel", expect.anything());
    await waitFor(() => expect(document.activeElement?.textContent).toBe("Cancel"));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Confirm cancel" }));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/recurring/series-1/cancel",
        expect.objectContaining({ method: "POST", body: JSON.stringify({}) }),
      ),
    );
  });

  it("posts resume for a past subscription", async () => {
    const past = aSeries({
      isCurrent: false,
      status: "ended",
      lastPeriodEndedOn: "2026-08-15",
      lastEndReason: "cancelled",
    });
    const response: RecurringResponse = { series: [past] };
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(response);
      if (path === "/recurring/series-1/resume") return Promise.resolve(aSeries());
      throw new Error(`unexpected path ${path}`);
    });

    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    await screen.findByRole("button", { name: "Ended" });
    fireEvent.click(screen.getByRole("button", { name: "Ended" }));

    fireEvent.click(screen.getByRole("button", { name: "Manage Synthetic streaming service" }));
    expect(screen.queryByRole("combobox", { name: "Status" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        "/recurring/series-1/resume",
        expect.objectContaining({ method: "POST", body: JSON.stringify({}) }),
      ),
    );
  });

  it("shows an ended row's date and reason", async () => {
    const past = aSeries({
      isCurrent: false,
      status: "ended",
      lastPeriodEndedOn: "2026-08-15",
      lastEndReason: "lapsed",
    });
    const response: RecurringResponse = { series: [past] };
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(response);
      throw new Error(`unexpected path ${path}`);
    });

    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    await screen.findByRole("button", { name: "Ended" });
    fireEvent.click(screen.getByRole("button", { name: "Ended" }));
    expect(await screen.findByText(/ended 2026-08-15/)).toBeTruthy();
    expect(screen.getByText(/stopped charging/)).toBeTruthy();
  });

  it("shows nothing found instead of an empty table", async () => {
    const request = vi.fn((path: string) => {
      if (path === "/recurring/refresh") return Promise.resolve({});
      if (path === "/recurring") return Promise.resolve(empty);
      throw new Error(`unexpected path ${path}`);
    });

    render(<RecurringScreen currencies={CURRENCIES} request={request as Request} />);
    expect(within(await screen.findByRole("region", { name: "Next payment" }))
      .getByText(/no recurring payment other than a subscription has been found yet/i)).toBeTruthy();
  });
});
