// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CoverageScreen } from "@/app/coverage/coverage-screen";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type { AcknowledgementRow, CoverageResponse } from "@/types/api";

type Request = typeof apiFetch;

const baseCoverage: CoverageResponse = {
  historyAnchor: "2024-01-01",
  months: ["2024-01-01", "2024-02-01"],
  rows: [
    {
      subject: "account",
      accountId: "00000000-0000-4000-8000-0000000000a1",
      accountName: "Synthetic current account",
      lifeStart: "2024-01-01",
      openingBalanceDate: "2024-01-01",
      anchorAfterHistory: false,
      months: [
        { status: "reconciled", reason: null, note: null },
        { status: "missing", reason: null, note: null },
      ],
      counts: { reconciled: 1, acknowledged: 0, partial: 0, inProgress: 0, missing: 1, outsideAccountLife: 0 },
    },
    {
      subject: "payslips",
      accountId: null,
      accountName: null,
      lifeStart: null,
      openingBalanceDate: null,
      anchorAfterHistory: null,
      months: [
        { status: "acknowledged", reason: "unobtainable", note: "no paper record" },
        { status: "missing", reason: null, note: null },
      ],
      counts: { reconciled: 0, acknowledged: 1, partial: 0, inProgress: 0, missing: 1, outsideAccountLife: 0 },
    },
  ],
};

function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn((path: string, init?: RequestInit) => Promise.resolve(handler(path, init))) as Request;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("coverage screen", () => {
  it("explains an empty evidence grid", async () => {
    const request = requestFrom((path) => path === "/coverage" ? { ...baseCoverage, rows: [] } : {});
    render(<CoverageScreen request={request} />);
    expect(await screen.findByText("No account or payslip evidence source is available for this period.")).toBeTruthy();
    expect(screen.getByText("No missing or partial month needs review.")).toBeTruthy();
  });

  it("renders the grid, its status labels and the legend", async () => {
    const request = requestFrom((path) => {
      if (path === "/coverage") return baseCoverage;
      throw new ApiError(500, "unexpected");
    });
    render(<CoverageScreen request={request} />);

    expect(await screen.findByText("2024-01-01", { selector: "strong" })).toBeTruthy();
    const table = screen.getByRole("table", { name: /History coverage/ });
    expect(within(table).getByText("Synthetic current account")).toBeTruthy();
    expect(within(table).getByText("Payslips")).toBeTruthy();
    expect(
      within(table).getByLabelText("Synthetic current account 2024-01-01: Reconciled"),
    ).toBeTruthy();
    expect(within(table).getByLabelText("Payslips 2024-01-01: Acknowledged")).toBeTruthy();

    const legend = screen.getByRole("list", { name: "Status legend" });
    expect(within(legend).getByText("Missing")).toBeTruthy();
    expect(within(legend).getByText("Outside account life")).toBeTruthy();
    const summary = screen.getByRole("region", { name: "Coverage status" });
    expect(summary.textContent).toContain("Needs review2");
    expect(summary.textContent).toContain("Acknowledged1");
    expect(summary.textContent).toContain("Reconciled1");
  });

  it("opens the earliest unresolved gap directly from the summary", async () => {
    const request = requestFrom((path) => path === "/coverage" ? baseCoverage : {});
    render(<CoverageScreen request={request} />);
    fireEvent.click(await screen.findByRole("button", { name: /Review earliest gap/ }));
    expect(screen.getByRole("heading", { name: "Synthetic current account · 2024-02-01" })).toBeTruthy();
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("acknowledges a missing cell and refreshes the grid", async () => {
    let acknowledged = false;
    const request = requestFrom((path, init) => {
      if (path === "/coverage") {
        return acknowledged
          ? {
              ...baseCoverage,
              rows: [
                {
                  ...baseCoverage.rows[0],
                  months: [
                    baseCoverage.rows[0].months[0],
                    { status: "acknowledged", reason: "not_issued", note: null },
                  ],
                },
                baseCoverage.rows[1],
              ],
            }
          : baseCoverage;
      }
      if (path === "/coverage/acknowledgements" && init?.method === "PUT") {
        acknowledged = true;
        expect(JSON.parse(init.body as string)).toEqual({
          subject: "account",
          accountId: "00000000-0000-4000-8000-0000000000a1",
          month: "2024-02-01",
          reason: "not_issued",
          note: null,
        });
        return {
          subject: "account", accountId: "00000000-0000-4000-8000-0000000000a1",
          month: "2024-02-01", reason: "not_issued", note: null,
        } satisfies AcknowledgementRow;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<CoverageScreen request={request} />);

    fireEvent.click(await screen.findByLabelText("Synthetic current account 2024-02-01: Missing"));
    expect(await screen.findByText("Import statements")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge gap" }));

    await waitFor(() => expect(screen.getByText("Gap acknowledged.")).toBeTruthy());
    expect(
      await screen.findByLabelText("Synthetic current account 2024-02-01: Acknowledged"),
    ).toBeTruthy();
  });

  it("removes an acknowledgement from an acknowledged cell", async () => {
    let removed = false;
    const request = requestFrom((path, init) => {
      if (path === "/coverage") {
        return removed
          ? {
              ...baseCoverage,
              rows: [
                baseCoverage.rows[0],
                {
                  ...baseCoverage.rows[1],
                  months: [
                    { status: "missing", reason: null, note: null },
                    baseCoverage.rows[1].months[1],
                  ],
                },
              ],
            }
          : baseCoverage;
      }
      if (path.startsWith("/coverage/acknowledgements?") && init?.method === "DELETE") {
        removed = true;
        const params = new URLSearchParams(path.split("?")[1]);
        expect(params.get("subject")).toBe("payslips");
        expect(params.get("month")).toBe("2024-01-01");
        expect(params.get("accountId")).toBeNull();
        return undefined;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<CoverageScreen request={request} />);

    fireEvent.click(await screen.findByLabelText("Payslips 2024-01-01: Acknowledged"));
    expect(await screen.findByText(/no paper record/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove acknowledgement" }));

    await waitFor(() => expect(screen.getByText("Acknowledgement removed.")).toBeTruthy());
    expect(await screen.findByLabelText("Payslips 2024-01-01: Missing")).toBeTruthy();
  });
});
