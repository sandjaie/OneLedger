// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SalaryAnalysis } from "@/app/salary/salary-analysis";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { CurrencyRow, SalaryAnalysisResponse } from "@/types/api";

type Request = typeof apiFetch;

const currencies: Array<CurrencyRow> = [
  { code: "XAA", name: "Synthetic Currency", symbol: "A", decimals: 2, numberFormat: "international" },
];

const analysis: SalaryAnalysisResponse = {
  currencies: [
    {
      currency: "XAA",
      years: [
        {
          year: 2026,
          compensation: "6000.0000",
          fixed: "5000.0000",
          variable: "1000.0000",
          variableShare: "16.67",
          reimbursement: "0.0000",
          deductions: "1000.0000",
          netPay: "5000.0000",
          cashReceived: "5000.0000",
          payslipMonths: 2,
          gapMonths: 1,
          receivedMonths: 1,
        },
      ],
      payslips: [
        {
          payslipId: "00000000-0000-4000-8000-000000000001",
          payMonth: "2026-01",
          compensation: "3000.0000",
          fixed: "2500.0000",
          variable: "500.0000",
          reimbursement: "0.0000",
          deductions: "500.0000",
          netPay: "2500.0000",
          cashReceived: "2500.0000",
          receiptStatus: "received",
          lines: [],
        },
        {
          payslipId: "00000000-0000-4000-8000-000000000002",
          payMonth: "2026-03",
          compensation: "3000.0000",
          fixed: "2500.0000",
          variable: "500.0000",
          reimbursement: "0.0000",
          deductions: "500.0000",
          netPay: "2500.0000",
          cashReceived: null,
          receiptStatus: "unlinked",
          lines: [],
        },
      ],
      gaps: ["2026-02"],
      increments: [
        {
          effectiveMonth: "2026-03",
          previousFixed: "2200.0000",
          newFixed: "2500.0000",
          change: "300.0000",
          changePercent: "13.64",
          confirmed: false,
          acrossGap: true,
        },
      ],
      variablePay: [
        { payslipId: "00000000-0000-4000-8000-000000000001", payMonth: "2026-01", label: "Bonus", amount: "500.0000" },
      ],
      labels: [
        { label: "Basic", component: "fixed", source: "inferred", monthCount: 2, firstMonth: "2026-01", lastMonth: "2026-03", latestAmount: "2500.0000" },
        { label: "Bonus", component: "variable", source: "rule", monthCount: 1, firstMonth: "2026-01", lastMonth: "2026-01", latestAmount: "500.0000" },
      ],
    },
  ],
  salaryCreditsWithoutPayslip: [
    { transactionId: "00000000-0000-4000-8000-000000000009", bookedDate: "2026-04-01", amount: "3000.0000", currency: "XAA", accountName: "Checking" },
  ],
};

// An `async` wrapper, not `Promise.resolve(handler(...))`: a handler that throws to simulate a
// refusal must become a rejected promise (as the real `apiFetch` — itself `async` — always does),
// not a synchronous throw that only a `try`/`catch` around the call would catch.
function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("salary analysis", () => {
  it("renders years, a gap month, increments, variable pay and credits without a payslip from server strings", async () => {
    const request = requestFrom((path) => {
      if (path === "/salary/analysis") return analysis;
      throw new ApiError(500, "unexpected");
    });
    render(<SalaryAnalysis currencies={currencies} refreshKey={0} request={request} />);

    await screen.findByText("2026");
    // Years row.
    expect(screen.getByText("16.67%")).toBeTruthy();
    expect(screen.getAllByText("A 6,000.00").length).toBeGreaterThan(0);
    // Months: the gap row reads "No payslip" and its month is labelled from the pay month.
    expect(screen.getByText("Feb 2026")).toBeTruthy();
    expect(screen.getByText("No payslip")).toBeTruthy();
    expect(screen.getByText("Not linked")).toBeTruthy();
    // Increments: both markers on the one across-gap, unconfirmed change.
    expect(screen.getByText("Unconfirmed")).toBeTruthy();
    expect(screen.getByText("After a gap")).toBeTruthy();
    expect(screen.getByText(/13\.64%/)).toBeTruthy();
    // Variable pay.
    expect(screen.getByText("Jan 2026 · Bonus")).toBeTruthy();
    // Earning labels: the inferred one is marked, the owner's rule is not.
    expect(screen.getByText("Inferred")).toBeTruthy();
    expect(screen.getByText("Use inference")).toBeTruthy();
    // Salary credits without a payslip.
    expect(screen.getByText(/Checking/)).toBeTruthy();
  });

  it("shows the empty state when there are no payslips", async () => {
    const request = requestFrom((path) => {
      if (path === "/salary/analysis") {
        return { currencies: [], salaryCreditsWithoutPayslip: [] } satisfies SalaryAnalysisResponse;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<SalaryAnalysis currencies={currencies} refreshKey={0} request={request} />);

    await screen.findByText("No payslips yet.");
  });

  it("shows an error with retry", async () => {
    let calls = 0;
    const request = requestFrom((path) => {
      if (path === "/salary/analysis") {
        calls += 1;
        if (calls === 1) throw new ApiError(500, "boom");
        return { currencies: [], salaryCreditsWithoutPayslip: [] } satisfies SalaryAnalysisResponse;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<SalaryAnalysis currencies={currencies} refreshKey={0} request={request} />);

    await screen.findByText("Salary analysis could not be loaded.");
    fireEvent.click(screen.getByText("Try again"));
    await screen.findByText("No payslips yet.");
  });

  it("PUTs a rule on a component change and reloads", async () => {
    let saved: unknown;
    const request = requestFrom((path, init) => {
      if (path === "/salary/analysis") return analysis;
      if (path === "/salary/earning-rules/Basic" && init?.method === "PUT") {
        saved = JSON.parse(String(init.body));
        return { label: "Basic", component: "variable" };
      }
      throw new ApiError(500, "unexpected");
    });
    render(<SalaryAnalysis currencies={currencies} refreshKey={0} request={request} />);

    const select = await screen.findByLabelText("Component for Basic");
    fireEvent.change(select, { target: { value: "variable" } });

    await waitFor(() => expect(saved).toEqual({ component: "variable" }));
  });

  it("DELETEs the rule when the owner chooses Use inference", async () => {
    let deleted = false;
    const request = requestFrom((path, init) => {
      if (path === "/salary/analysis") return analysis;
      if (path === "/salary/earning-rules/Bonus" && init?.method === "DELETE") {
        deleted = true;
        return undefined;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<SalaryAnalysis currencies={currencies} refreshKey={0} request={request} />);

    const button = await screen.findByText("Use inference");
    fireEvent.click(button);

    await waitFor(() => expect(deleted).toBe(true));
  });
});
