// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PayslipsScreen } from "@/app/salary/payslips-screen";
import { ApiError, type apiFetch } from "@/lib/api/client";
import type {
  CategoryRow,
  CurrencyRow,
  DepositCandidate,
  PayslipImportResponse,
  PayslipRow,
  PayslipsResponse,
} from "@/types/api";

type Request = typeof apiFetch;

const currencies: Array<CurrencyRow> = [
  { code: "XAA", name: "Synthetic Currency", symbol: "A", decimals: 2, numberFormat: "international" },
];

const categories: Array<CategoryRow> = [
  {
    id: "00000000-0000-4000-8000-000000000010",
    parentId: null,
    name: "Travel",
    slug: "travel",
    kind: "expense",
    bucket: "want",
    sortOrder: 1,
    isSystem: true,
    isActive: true,
  },
  {
    id: "00000000-0000-4000-8000-000000000011",
    parentId: "00000000-0000-4000-8000-000000000010",
    name: "Flights",
    slug: "travel-flights",
    kind: "expense",
    bucket: "want",
    sortOrder: 1,
    isSystem: true,
    isActive: true,
  },
];

const payslip: PayslipRow = {
  id: "00000000-0000-4000-8000-000000000001",
  payMonth: "2026-01",
  currency: "XAA",
  grossEarnings: "5000.0000",
  totalDeductions: "1000.0000",
  netPay: "4000.0000",
  lines: [
    {
      lineNo: 1,
      kind: "earning",
      label: "Basic",
      amount: "5000.0000",
      countedAsSpending: false,
      categoryId: null,
    },
    {
      lineNo: 2,
      kind: "deduction",
      label: "Flights",
      amount: "1000.0000",
      countedAsSpending: false,
      categoryId: null,
    },
  ],
  supersedesPayslipId: null,
  supersededByPayslipId: null,
  imported: false,
  createdAt: "2026-01-05T00:00:00Z",
  deposit: null,
};

// An `async` wrapper, not `Promise.resolve(handler(...))`: a handler that throws to simulate a
// refusal must become a rejected promise (as the real `apiFetch` — itself `async` — always does),
// not a synchronous throw that only a `try`/`catch` around the call would catch. SalaryAnalysis's
// initial load uses `.then()/.catch()` rather than `await`, which only a rejected promise reaches.
function requestFrom(handler: (path: string, init?: RequestInit) => unknown | Promise<unknown>): Request {
  return vi.fn(async (path: string, init?: RequestInit) => handler(path, init)) as Request;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("salary screen", () => {
  it("lists a recorded payslip with its money formatted in its own currency", async () => {
    const request = requestFrom((path) => {
      if (path === "/salary/analysis") return { currencies: [], salaryCreditsWithoutPayslip: [] };
      if (path === "/payslips/deduction-rules") return { rules: [] };
      if (path === "/payslips") return { payslips: [payslip], total: 1 } satisfies PayslipsResponse;
      throw new ApiError(500, "unexpected");
    });
    render(<PayslipsScreen categories={categories} currencies={currencies} request={request} />);

    await screen.findByText("2026-01");
    expect(screen.getByText("A 4,000.00 net · A 5,000.00 gross")).toBeTruthy();
    expect(screen.getByText("Not linked")).toBeTruthy();
  });

  it("posts typed strings with one idempotency key, reused on a retry", async () => {
    let firstKey: string | undefined;
    let calls = 0;
    const request = requestFrom((path, init) => {
      if (path === "/salary/analysis") return { currencies: [], salaryCreditsWithoutPayslip: [] };
      if (path === "/payslips/deduction-rules") return { rules: [] };
      if (path === "/payslips" && (!init || init.method === undefined)) {
        return { payslips: [], total: 0 } satisfies PayslipsResponse;
      }
      if (path === "/payslips" && init?.method === "POST") {
        calls += 1;
        const body = JSON.parse(String(init.body)) as { idempotencyKey: string; grossEarnings: string };
        if (calls === 1) {
          firstKey = body.idempotencyKey;
          throw new ApiError(500, "transient failure");
        }
        expect(body.idempotencyKey).toBe(firstKey);
        expect(body.grossEarnings).toBe("5000.0000");
        return payslip;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<PayslipsScreen categories={categories} currencies={currencies} request={request} />);
    await screen.findByText("No payslips are recorded yet.");

    fireEvent.change(screen.getByLabelText("Pay month"), { target: { value: "2026-01" } });
    fireEvent.change(screen.getByLabelText("Gross earnings"), { target: { value: "5000.0000" } });
    fireEvent.change(screen.getByLabelText("Total deductions"), { target: { value: "1000.0000" } });
    fireEvent.change(screen.getByLabelText("Net pay"), { target: { value: "4000.0000" } });
    fireEvent.change(screen.getByPlaceholderText("Label"), { target: { value: "Basic" } });
    fireEvent.change(screen.getByPlaceholderText("Amount"), { target: { value: "5000.0000" } });

    fireEvent.click(screen.getByRole("button", { name: "Record payslip" }));
    await waitFor(() => expect(screen.getByText("transient failure")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: "Record payslip" }));
    await waitFor(() => expect(calls).toBe(2));
  });

  it("previews an import and only then offers to confirm it", async () => {
    const preview: PayslipImportResponse = {
      supported: true,
      months: [
        {
          payMonth: "2026-01",
          currency: "XAA",
          grossEarnings: "5000.0000",
          totalDeductions: "1000.0000",
          netPay: "4000.0000",
          lines: [],
          reason: null,
          status: null,
          payslipId: null,
        },
      ],
    };
    const request = requestFrom((path, init) => {
      if (path === "/salary/analysis") return { currencies: [], salaryCreditsWithoutPayslip: [] };
      if (path === "/payslips/deduction-rules") return { rules: [] };
      if (path === "/payslips") return { payslips: [], total: 0 } satisfies PayslipsResponse;
      if (path === "/payslips/import" && init?.method === "POST") return preview;
      throw new ApiError(500, "unexpected");
    });
    render(<PayslipsScreen categories={categories} currencies={currencies} request={request} />);
    await screen.findByText("No payslips are recorded yet.");

    expect(screen.queryByText("Import reconciling months")).toBeNull();
    const file = new File(["synthetic"], "payslip.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("Payslip file"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Check file" }));

    await screen.findByText("Reconciles");
    expect(screen.getByRole("button", { name: "Import reconciling months" })).toBeTruthy();
  });

  it("links a candidate only after explicit confirmation", async () => {
    const candidate: DepositCandidate = {
      transactionId: "00000000-0000-4000-8000-000000000002",
      accountId: "00000000-0000-4000-8000-000000000003",
      accountName: "Synthetic checking",
      bookedDate: "2026-02-03",
      amount: "4000.0000",
      currency: "XAA",
      description: "SYNTHETIC SALARY CREDIT",
    };
    let linkCalls = 0;
    const request = requestFrom((path, init) => {
      if (path === "/salary/analysis") return { currencies: [], salaryCreditsWithoutPayslip: [] };
      if (path === "/payslips/deduction-rules") return { rules: [] };
      if (path === "/payslips") return { payslips: [payslip], total: 1 } satisfies PayslipsResponse;
      if (path === `/payslips/${payslip.id}/deposit-candidates`) return { candidates: [candidate] };
      if (path === `/payslips/${payslip.id}/deposit` && init?.method === "PUT") {
        linkCalls += 1;
        expect(JSON.parse(String(init.body))).toEqual({ transactionId: candidate.transactionId });
        return { ...payslip, deposit: { ...candidate, eligible: true } };
      }
      throw new ApiError(500, "unexpected");
    });
    render(<PayslipsScreen categories={categories} currencies={currencies} request={request} />);
    await screen.findByText("2026-01");

    fireEvent.click(screen.getByRole("button", { name: "Find net-pay credit" }));
    await screen.findByText(/SYNTHETIC SALARY CREDIT/);
    expect(linkCalls).toBe(0);

    fireEvent.click(screen.getByRole("button", { name: "Link" }));
    await screen.findByText(/Link the credit on 2026-02-03/);
    fireEvent.click(screen.getByRole("button", { name: "Confirm link" }));

    await waitFor(() => expect(linkCalls).toBe(1));
  });

  it("unlinks a linked payslip", async () => {
    const linked: PayslipRow = {
      ...payslip,
      deposit: {
        transactionId: "00000000-0000-4000-8000-000000000002",
        accountId: "00000000-0000-4000-8000-000000000003",
        accountName: "Synthetic checking",
        bookedDate: "2026-02-03",
        amount: "4000.0000",
        eligible: true,
      },
    };
    let unlinkCalls = 0;
    const request = requestFrom((path, init) => {
      if (path === "/salary/analysis") return { currencies: [], salaryCreditsWithoutPayslip: [] };
      if (path === "/payslips/deduction-rules") return { rules: [] };
      if (path === "/payslips" && (!init || init.method === undefined)) {
        return { payslips: [unlinkCalls > 0 ? payslip : linked], total: 1 } satisfies PayslipsResponse;
      }
      if (path === `/payslips/${payslip.id}/deposit` && init?.method === "DELETE") {
        unlinkCalls += 1;
        return { ...payslip, deposit: null };
      }
      throw new ApiError(500, "unexpected");
    });
    render(<PayslipsScreen categories={categories} currencies={currencies} request={request} />);
    await screen.findByText("Linked · 2026-02-03");

    fireEvent.click(screen.getByRole("button", { name: "Unlink" }));
    await waitFor(() => expect(unlinkCalls).toBe(1));
    await screen.findByText("Not linked");
  });

  it("counts a deduction line as spending, defaulting to Travel › Flights, then stops", async () => {
    let posted: unknown;
    let deleteCalls = 0;
    let current = payslip;
    const request = requestFrom((path, init) => {
      if (path === "/salary/analysis") return { currencies: [], salaryCreditsWithoutPayslip: [] };
      if (path === "/payslips/deduction-rules") return { rules: [] };
      if (path === "/payslips" && (!init || init.method === undefined)) {
        return { payslips: [current], total: 1 } satisfies PayslipsResponse;
      }
      if (path === "/payslips/00000000-0000-4000-8000-000000000001/lines/2/spending") {
        if (init?.method === "POST") {
          posted = JSON.parse(String(init.body));
          current = {
            ...current,
            lines: [current.lines[0], { ...current.lines[1], countedAsSpending: true, categoryId: categories[1].id }],
          };
          return current;
        }
        if (init?.method === "DELETE") {
          deleteCalls += 1;
          current = {
            ...current,
            lines: [current.lines[0], { ...current.lines[1], countedAsSpending: false, categoryId: null }],
          };
          return current;
        }
      }
      throw new ApiError(500, "unexpected");
    });
    render(<PayslipsScreen categories={categories} currencies={currencies} request={request} />);
    await screen.findByText("2026-01");

    const checkbox = screen.getByLabelText("Count as spending") as HTMLInputElement;
    expect(checkbox.disabled).toBe(false);
    fireEvent.click(checkbox);

    await waitFor(() => expect(posted).toEqual({ categoryId: categories[1].id }));
    await waitFor(() => expect((screen.getByLabelText("Count as spending") as HTMLInputElement).checked).toBe(true));

    fireEvent.click(screen.getByLabelText("Count as spending"));
    await waitFor(() => expect(deleteCalls).toBe(1));
    await waitFor(() => expect((screen.getByLabelText("Count as spending") as HTMLInputElement).checked).toBe(false));
  });

  it("lists, saves and removes deduction rules with the label URI-encoded", async () => {
    let rules: Array<{ label: string; categoryId: string }> = [
      { label: "Pension & NI", categoryId: categories[0].id },
    ];
    let putBody: unknown;
    let putPath: string | undefined;
    let deleteCalls = 0;
    const request = requestFrom((path, init) => {
      if (path === "/salary/analysis") return { currencies: [], salaryCreditsWithoutPayslip: [] };
      if (path === "/payslips") return { payslips: [], total: 0 } satisfies PayslipsResponse;
      if (path === "/payslips/deduction-rules") return { rules };
      if (path === "/payslips/deduction-rules/Pension%20%26%20NI" && init?.method === "PUT") {
        putPath = path;
        putBody = JSON.parse(String(init.body));
        rules = [{ label: "Pension & NI", categoryId: categories[1].id }];
        return { label: "Pension & NI", categoryId: categories[1].id };
      }
      if (path === "/payslips/deduction-rules/Pension%20%26%20NI" && init?.method === "DELETE") {
        deleteCalls += 1;
        rules = [];
        return undefined;
      }
      throw new ApiError(500, "unexpected");
    });
    render(<PayslipsScreen categories={categories} currencies={currencies} request={request} />);

    await screen.findByText("Pension & NI");
    fireEvent.change(screen.getByDisplayValue("Travel"), { target: { value: categories[1].id } });

    await waitFor(() => expect(putPath).toBe("/payslips/deduction-rules/Pension%20%26%20NI"));
    expect(putBody).toEqual({ categoryId: categories[1].id });
    await screen.findByDisplayValue("Travel → Flights");

    const ruleRow = screen.getByText("Pension & NI").closest("li") as HTMLElement;
    fireEvent.click(within(ruleRow).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(deleteCalls).toBe(1));
    await screen.findByText("No deduction rules are recorded yet.");
  });
});
