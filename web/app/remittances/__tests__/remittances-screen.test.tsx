// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RemittancesScreen } from "@/app/remittances/remittances-screen";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  CurrencyRow,
  ProviderRowOut,
  RemittanceAnalysisResponse,
} from "@/types/api";

type Request = typeof apiFetch;

const currencies: Array<CurrencyRow> = [
  { code: "XAA", name: "Synthetic Currency", symbol: "A", decimals: 2, numberFormat: "international" },
  { code: "XBB", name: "Other Synthetic", symbol: "B", decimals: 2, numberFormat: "international" },
];

function providerRow(overrides: Partial<ProviderRowOut> = {}): ProviderRowOut {
  return {
    provider: "Exchange house",
    transferCount: 2,
    sentSource: "10000.0000",
    costedSentSource: "10000.0000",
    feesSource: "30.0000",
    spreadSource: "220.0000",
    totalCostSource: "250.0000",
    effectiveCostPct: "2.5000",
    avgRate: "22.2000000000",
    approximateCount: 0,
    ...overrides,
  };
}

function analysisWith(
  providers: Array<ProviderRowOut>,
  overrides: Partial<RemittanceAnalysisResponse["corridors"][number]["years"][number]> = {},
): RemittanceAnalysisResponse {
  return {
    corridors: [
      {
        sourceCurrency: "XAA",
        destinationCurrency: "XBB",
        years: [
          {
            year: 2026,
            totalSentSource: "10000.0000",
            totalCostedSentSource: "10000.0000",
            totalFeesSource: "30.0000",
            totalSpreadSource: "220.0000",
            totalCostSource: "250.0000",
            totalEffectiveCostPct: "2.5000",
            cheapestProvider: null,
            potentialSavingsSource: null,
            providers,
            unattributed: null,
            ...overrides,
          },
        ],
      },
    ],
  };
}

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

function analysisRequest(body: RemittanceAnalysisResponse): Request {
  return requestFrom((path) => {
    if (path === "/transfers/analysis") return body;
    throw new ApiError(500, "unexpected");
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("remittances screen", () => {
  it("renders a corridor's provider table and its total row from server strings", async () => {
    render(
      <RemittancesScreen currencies={currencies} request={analysisRequest(analysisWith([providerRow()]))} />,
    );

    await screen.findByRole("heading", { name: "XAA → XBB" });
    const table = screen.getByRole("table", { name: "XAA → XBB by provider in 2026" });
    expect(table.textContent).toContain("Exchange house");
    expect(table.textContent).toContain("A 10,000.00");
    expect(table.textContent).toContain("A 250.00");
    expect(table.textContent).toContain("2.50%");
    expect(table.textContent).toContain("22.2000000000");
    // The total row is the server's own figures, not a sum computed here.
    const footer = table.querySelector("tfoot");
    expect(footer?.textContent).toContain("A 30.00");
    expect(footer?.textContent).toContain("A 220.00");
    expect(footer?.textContent).toContain("2.50%");
  });

  it("names the cheapest channel and what routing everything through it would have saved", async () => {
    const body = analysisWith(
      [
        providerRow({ provider: "Cheap", effectiveCostPct: "1.0000", totalCostSource: "100.0000" }),
        providerRow({ provider: "Dear", effectiveCostPct: "3.0000", totalCostSource: "300.0000" }),
      ],
      { cheapestProvider: "Cheap", potentialSavingsSource: "200.0000" },
    );
    render(<RemittancesScreen currencies={currencies} request={analysisRequest(body)} />);

    await screen.findByRole("heading", { name: "XAA → XBB" });
    // The badge on the provider's own row, and the year summary, are separate statements.
    const table = screen.getByRole("table", { name: /by provider in 2026/ });
    const cheapestRow = Array.from(table.querySelectorAll("tbody tr"))
      .find((row) => row.textContent?.includes("Cheap"));
    expect(cheapestRow?.textContent).toContain("Cheapest");
    expect(screen.getByText(/Cheapest Cheap · A 200.00 not saved/)).toBeTruthy();
    expect(screen.getByLabelText("XAA → XBB in 2026").textContent)
      .toContain("Routing everything through it would have saved A 200.00");
  });

  it("says a comparison needs two recorded providers when there is no cheapest channel", async () => {
    render(
      <RemittancesScreen
        currencies={currencies}
        request={analysisRequest(analysisWith([providerRow({ provider: "Only one" })]))}
      />,
    );

    await screen.findByRole("heading", { name: "XAA → XBB" });
    expect(screen.getByText("A cheapest channel needs two providers with a recorded cost")).toBeTruthy();
    expect(screen.getByLabelText("XAA → XBB in 2026").textContent).toContain("No comparison");
  });

  it("shows unattributed money inside the totals but outside the comparison", async () => {
    const body = analysisWith(
      [providerRow({ provider: "Named" })],
      {
        unattributed: providerRow({ provider: null, transferCount: 3, sentSource: "50000.0000" }),
        totalSentSource: "60000.0000",
      },
    );
    render(<RemittancesScreen currencies={currencies} request={analysisRequest(body)} />);

    await screen.findByRole("heading", { name: "XAA → XBB" });
    const row = screen.getByTestId("unattributed-2026");
    expect(row.textContent).toContain("No provider recorded");
    expect(row.textContent).toContain("Outside the comparison");
    expect(row.textContent).toContain("Label 3 transfers on Transactions");
    expect(screen.getByRole("table", { name: /by provider in 2026/ }).querySelector("tfoot")?.textContent)
      .toContain("A 60,000.00");
  });

  it("marks a provider row that rests on a carried-forward rate", async () => {
    render(
      <RemittancesScreen
        currencies={currencies}
        request={analysisRequest(analysisWith([providerRow({ approximateCount: 1 })]))}
      />,
    );

    await screen.findByRole("heading", { name: "XAA → XBB" });
    const [row] = Array.from(
      screen.getByRole("table", { name: /by provider in 2026/ }).querySelectorAll("tbody tr"),
    );
    expect(row.textContent).toContain("1 on a carried-forward rate");
  });

  it("states that nothing is costed rather than showing a zero cost", async () => {
    const uncosted = providerRow({
      costedSentSource: "0.0000",
      feesSource: null,
      spreadSource: null,
      totalCostSource: null,
      effectiveCostPct: null,
      avgRate: null,
    });
    const body = analysisWith([uncosted], {
      totalCostedSentSource: "0.0000",
      totalFeesSource: null,
      totalSpreadSource: null,
      totalCostSource: null,
      totalEffectiveCostPct: null,
    });
    render(<RemittancesScreen currencies={currencies} request={analysisRequest(body)} />);

    await screen.findByRole("heading", { name: "XAA → XBB" });
    const table = screen.getByRole("table", { name: /by provider in 2026/ });
    expect(table.textContent).toContain("Not costed yet");
    expect(table.textContent).toContain("none costed yet");
    expect(table.textContent).not.toContain("A 0.00");
    expect(screen.getByLabelText("XAA → XBB in 2026").textContent)
      .toContain("No transfer this year has a recorded cost");
  });

  it("says what a partial cost covers rather than implying it covers everything", async () => {
    // One costed provider plus uncosted unlabelled money: the server's 2.50% is of 10,000, not
    // of the 60,000 sent, and `_effective_pct` withholds a percentage of an unknown whole — so
    // the total row must say which part the percentage beside it covers.
    const body = analysisWith(
      [providerRow({ provider: "Named" })],
      {
        unattributed: providerRow({
          provider: null,
          transferCount: 1,
          sentSource: "50000.0000",
          costedSentSource: "0.0000",
          feesSource: null,
          spreadSource: null,
          totalCostSource: null,
          effectiveCostPct: null,
        }),
        totalSentSource: "60000.0000",
        totalCostedSentSource: "10000.0000",
      },
    );
    render(<RemittancesScreen currencies={currencies} request={analysisRequest(body)} />);

    await screen.findByRole("heading", { name: "XAA → XBB" });
    const footer = screen.getByRole("table", { name: /by provider in 2026/ }).querySelector("tfoot");
    expect(footer?.textContent).toContain("Effective covers the costed part only");
    expect(screen.getByLabelText("XAA → XBB in 2026").textContent).toContain("A 10,000.00 of it");
  });

  it("marks a total that rests partly on a carried-forward rate", async () => {
    render(
      <RemittancesScreen
        currencies={currencies}
        request={analysisRequest(analysisWith([providerRow({ approximateCount: 2 })]))}
      />,
    );

    await screen.findByRole("heading", { name: "XAA → XBB" });
    const footer = screen.getByRole("table", { name: /by provider in 2026/ }).querySelector("tfoot");
    expect(footer?.textContent).toContain("2 on a carried-forward rate");
    expect(screen.getByLabelText("XAA → XBB in 2026").textContent)
      .toContain("2 on a carried-forward rate");
  });

  it("says there is no achieved rate rather than inventing one", async () => {
    render(
      <RemittancesScreen
        currencies={currencies}
        request={analysisRequest(analysisWith([providerRow({ avgRate: null })]))}
      />,
    );

    await screen.findByRole("heading", { name: "XAA → XBB" });
    expect(screen.getByText("No achieved rate")).toBeTruthy();
  });

  it("invites the first remittance when there is none", async () => {
    render(<RemittancesScreen currencies={currencies} request={analysisRequest({ corridors: [] })} />);

    expect(await screen.findByText(/No confirmed remittance yet/)).toBeTruthy();
  });

  it("offers a retry when the analysis cannot be loaded", async () => {
    let calls = 0;
    const request = requestFrom((path) => {
      calls += 1;
      if (path !== "/transfers/analysis") throw new ApiError(500, "unexpected");
      if (calls === 1) throw new ApiError(500, "no");
      return analysisWith([providerRow()]);
    });
    render(<RemittancesScreen currencies={currencies} request={request} />);

    const retry = await screen.findByRole("button", { name: "Try again" });
    retry.click();

    await screen.findByRole("heading", { name: "XAA → XBB" });
  });
});
