// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ValuationPanel } from "@/app/accounts/valuation-panel";
import type { apiFetch } from "@/lib/api/client";
import type {
  AccountValuationsResponse,
  CurrencyRow,
  ValuationSnapshotPreview,
  ValuationSnapshotRow,
} from "@/types/api";

type Request = typeof apiFetch;

const ACCOUNT_ID = "00000000-0000-4000-8000-000000000010";

const currencies: Array<CurrencyRow> = [
  { code: "XAA", name: "Synthetic currency A", symbol: "A", decimals: 2, numberFormat: "international" },
  { code: "XBB", name: "Synthetic currency B", symbol: "B", decimals: 2, numberFormat: "international" },
];

function valuations(overrides: Partial<AccountValuationsResponse> = {}): AccountValuationsResponse {
  return {
    accountId: ACCOUNT_ID,
    currentMode: "snapshot",
    currentEffectiveFrom: "2026-01-01",
    periods: [
      { id: "period-1", valuationMode: "snapshot", effectiveFrom: "2026-01-01", effectiveTo: null },
    ],
    snapshots: [],
    ...overrides,
  };
}

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(cleanup);

describe("ValuationPanel", () => {
  it("shows the current mode and no-valuation state when the account has none", async () => {
    const request = requestFrom((path) => {
      if (path === `/accounts/${ACCOUNT_ID}/valuations`) return valuations();
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(
      <ValuationPanel
        accountId={ACCOUNT_ID}
        accountName="Synthetic retirement account"
        baseCurrency="XAA"
        currencies={currencies}
        currency="XBB"
        onChanged={vi.fn()}
        onClose={vi.fn()}
        request={request}
      />,
    );

    expect(await screen.findByText((_, element) => element?.tagName === "STRONG" && element.textContent === "Dated valuation")).toBeTruthy();
    expect(screen.getByText("No valuation recorded yet.")).toBeTruthy();
  });

  const SNAPSHOT_ROW: ValuationSnapshotRow = {
    id: "snap-1",
    asOfDate: "2026-03-01",
    valueNative: "1000.0000",
    currency: "XBB",
    fxRateDate: "2026-03-01",
    fxRateToBase: "1.0000000000",
    fxRateToSecondary: "2.0000000000",
    valueBase: "1000.0000",
    valueSecondary: "2000.0000",
    method: "owner_entered",
    confidence: "high",
    sourceNote: null,
    createdAt: "2026-03-01T00:00:00Z",
  };
  const SNAPSHOT_PREVIEW: ValuationSnapshotPreview = {
    asOfDate: "2026-03-01",
    valueNative: "1000.0000",
    currency: "XBB",
    fxRateDate: "2026-03-01",
    valueBase: "1000.0000",
    valueSecondary: "2000.0000",
  };

  it("reviews a valuation with a dryRun request only, then confirms with the same strings", async () => {
    let loads = 0;
    let posts = 0;
    const onChanged = vi.fn();
    const request = requestFrom((path, init) => {
      if (path === `/accounts/${ACCOUNT_ID}/valuations`) {
        loads += 1;
        return loads === 1 ? valuations() : valuations({ snapshots: [SNAPSHOT_ROW] });
      }
      if (path === `/accounts/${ACCOUNT_ID}/valuation-snapshots` && init?.method === "POST") {
        posts += 1;
        const body = JSON.parse(String(init.body));
        if (posts === 1) {
          expect(body).toEqual({
            asOfDate: "2026-03-01",
            valueNative: "1000.0000",
            method: "owner_entered",
            confidence: "high",
            sourceNote: null,
            dryRun: true,
          });
          return SNAPSHOT_PREVIEW;
        }
        expect(body).toEqual({
          asOfDate: "2026-03-01",
          valueNative: "1000.0000",
          method: "owner_entered",
          confidence: "high",
          sourceNote: null,
        });
        return SNAPSHOT_ROW;
      }
      throw new Error(`Unexpected synthetic route ${path} ${init?.method}`);
    });

    render(
      <ValuationPanel
        accountId={ACCOUNT_ID}
        accountName="Synthetic retirement account"
        baseCurrency="XAA"
        currencies={currencies}
        currency="XBB"
        onChanged={onChanged}
        onClose={vi.fn()}
        request={request}
      />,
    );
    await screen.findByText("No valuation recorded yet.");

    fireEvent.change(screen.getByLabelText("As of date"), { target: { value: "2026-03-01" } });
    fireEvent.change(screen.getByLabelText("Value (XBB)"), { target: { value: "1000.0000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review valuation" }));

    expect(await screen.findByText(/Review:/)).toBeTruthy();
    expect(screen.getByText(/FX rate date 2026-03-01/)).toBeTruthy();
    // Nothing was ever recorded from the review alone.
    expect(posts).toBe(1);
    expect(onChanged).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Confirm and record" }));

    expect(await screen.findByText(/Recorded/)).toBeTruthy();
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(posts).toBe(2);
    // The reload is requested from a passive effect after the recorded result renders.
    await waitFor(() => expect(loads).toBe(2));
  });

  it("discards the preview when any field is edited after review", async () => {
    const request = requestFrom((path, init) => {
      if (path === `/accounts/${ACCOUNT_ID}/valuations`) return valuations();
      if (path === `/accounts/${ACCOUNT_ID}/valuation-snapshots` && init?.method === "POST") {
        return SNAPSHOT_PREVIEW;
      }
      throw new Error(`Unexpected synthetic route ${path} ${init?.method}`);
    });

    render(
      <ValuationPanel
        accountId={ACCOUNT_ID}
        accountName="Synthetic retirement account"
        baseCurrency="XAA"
        currencies={currencies}
        currency="XBB"
        onChanged={vi.fn()}
        onClose={vi.fn()}
        request={request}
      />,
    );
    await screen.findByText("No valuation recorded yet.");

    fireEvent.change(screen.getByLabelText("As of date"), { target: { value: "2026-03-01" } });
    fireEvent.change(screen.getByLabelText("Value (XBB)"), { target: { value: "1000.0000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review valuation" }));
    expect(await screen.findByText(/Review:/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Value (XBB)"), { target: { value: "1000.0001" } });

    expect(screen.queryByText(/Review:/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm and record" })).toBeNull();
  });

  it("ignores a review that returns after a field was edited", async () => {
    let answer: (value: ValuationSnapshotPreview) => void = () => undefined;
    const request = requestFrom((path, init) => {
      if (path === `/accounts/${ACCOUNT_ID}/valuations`) return valuations();
      if (path === `/accounts/${ACCOUNT_ID}/valuation-snapshots` && init?.method === "POST") {
        return new Promise<ValuationSnapshotPreview>((resolve) => {
          answer = resolve;
        });
      }
      throw new Error(`Unexpected synthetic route ${path} ${init?.method}`);
    });

    render(
      <ValuationPanel
        accountId={ACCOUNT_ID}
        accountName="Synthetic retirement account"
        baseCurrency="XAA"
        currencies={currencies}
        currency="XBB"
        onChanged={vi.fn()}
        onClose={vi.fn()}
        request={request}
      />,
    );
    await screen.findByText("No valuation recorded yet.");

    fireEvent.change(screen.getByLabelText("Value (XBB)"), { target: { value: "1000.0000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review valuation" }));
    fireEvent.change(screen.getByLabelText("Value (XBB)"), { target: { value: "2000.0000" } });
    await act(async () => answer(SNAPSHOT_PREVIEW));

    expect(screen.queryByText(/Review:/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirm and record" })).toBeNull();
  });

  it("changing the valuation mode to a backdated date notes that wealth history needs a refresh", async () => {
    const request = requestFrom((path, init) => {
      if (path === `/accounts/${ACCOUNT_ID}/valuations`) {
        return valuations({ currentMode: "ledger_balance", currentEffectiveFrom: "2018-01-01" });
      }
      if (path === `/accounts/${ACCOUNT_ID}/valuation-mode` && init?.method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          valuationMode: "holdings",
          effectiveFrom: "2020-01-01",
        });
        return { accountId: ACCOUNT_ID, valuationMode: "holdings", effectiveFrom: "2020-01-01" };
      }
      throw new Error(`Unexpected synthetic route ${path} ${init?.method}`);
    });

    render(
      <ValuationPanel
        accountId={ACCOUNT_ID}
        accountName="Synthetic brokerage account"
        baseCurrency="XAA"
        currencies={currencies}
        currency="XBB"
        onChanged={vi.fn()}
        onClose={vi.fn()}
        request={request}
      />,
    );
    await screen.findByText((_, element) => element?.tagName === "STRONG" && element.textContent === "Ledger balance");

    fireEvent.change(screen.getByLabelText("Mode"), { target: { value: "holdings" } });
    fireEvent.change(screen.getByLabelText("Effective from"), { target: { value: "2020-01-01" } });
    fireEvent.click(screen.getByRole("button", { name: "Change mode" }));

    expect(await screen.findByText(/affects past net worth/)).toBeTruthy();
  });

  it("shows the service's own refusal when a review is not accepted", async () => {
    const request = requestFrom((path, init) => {
      if (path === `/accounts/${ACCOUNT_ID}/valuations`) return valuations();
      if (path === `/accounts/${ACCOUNT_ID}/valuation-snapshots` && init?.method === "POST") {
        return Promise.reject({ detail: "wrong mode" });
      }
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(
      <ValuationPanel
        accountId={ACCOUNT_ID}
        accountName="Synthetic retirement account"
        baseCurrency="XAA"
        currencies={currencies}
        currency="XBB"
        onChanged={vi.fn()}
        onClose={vi.fn()}
        request={request}
      />,
    );
    await screen.findByText("No valuation recorded yet.");

    fireEvent.change(screen.getByLabelText("As of date"), { target: { value: "2026-03-01" } });
    fireEvent.change(screen.getByLabelText("Value (XBB)"), { target: { value: "1000.0000" } });
    fireEvent.click(screen.getByRole("button", { name: "Review valuation" }));

    expect(await screen.findByText("wrong mode")).toBeTruthy();
  });

  it("closes through the onClose callback", async () => {
    const onClose = vi.fn();
    const request = requestFrom((path) => {
      if (path === `/accounts/${ACCOUNT_ID}/valuations`) return valuations();
      throw new Error(`Unexpected synthetic route ${path}`);
    });

    render(
      <ValuationPanel
        accountId={ACCOUNT_ID}
        accountName="Synthetic retirement account"
        baseCurrency="XAA"
        currencies={currencies}
        currency="XBB"
        onChanged={vi.fn()}
        onClose={onClose}
        request={request}
      />,
    );
    await screen.findByText((_, element) => element?.tagName === "STRONG" && element.textContent === "Dated valuation");

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
