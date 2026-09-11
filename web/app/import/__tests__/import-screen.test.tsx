// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ImportScreen } from "@/app/import/import-screen";
import { ApiError } from "@/lib/api/client";
import type {
  AccountProposal,
  AccountRow,
  CurrencyRow,
  HouseholdCurrency,
  ParseResponse,
} from "@/types/api";

// `CurrencyRow` is the catalogue row and carries a display name; `HouseholdCurrency` is the
// subset the formatter reads. Deriving one from the other keeps them from drifting in the
// fixture the way they cannot drift in the API.
const catalogue: CurrencyRow = {
  code: "XAE",
  name: "Synthetic A",
  symbol: "A",
  decimals: 2,
  numberFormat: "international",
};

// A second currency with a different grouping, so a test can tell which one was used rather
// than passing because both format the same.
const other: CurrencyRow = {
  code: "XIN",
  name: "Synthetic I",
  symbol: "I",
  decimals: 2,
  numberFormat: "indian",
};

const currencies: Array<CurrencyRow> = [catalogue, other];
const currency: HouseholdCurrency = {
  code: catalogue.code,
  symbol: catalogue.symbol,
  decimals: catalogue.decimals,
  numberFormat: catalogue.numberFormat,
};

const accounts = [
  { id: "acct-card", name: "Synthetic Card", currency: "XAE" },
  { id: "acct-savings", name: "Synthetic Savings", currency: "XIN" },
] as Array<AccountRow>;

function parsed(overrides: Partial<ParseResponse> = {}): ParseResponse {
  return {
    staged: false,
    batchId: null,
    documentId: null,
    accountId: "acct-card",
    identityResolution: {
      status: "matched",
      institutionAction: "reuse",
      accountAction: "reuse",
      institution: {
        status: "matched",
        proposedName: "Synthetic Institution",
        country: "ZZ",
        candidates: [],
      },
      accountNumberMasked: "****1234",
      candidates: [],
      conflicts: [],
      proposalGroupKey: null,
    },
    layoutId: "layout-1",
    layoutVersion: 1,
    period: { start: "2026-03-01", end: "2026-03-31", isDerived: false },
    rowsExtracted: 2,
    counts: { new: 2 },
    reconciliation: {
      status: "matched",
      declaredOpening: "1000.0000",
      openingIsDerived: false,
      declaredClosing: "1490.0000",
      computedClosing: "1490.0000",
      delta: "0.0000",
      firstDivergentRow: null,
      previousBalance: null,
      amount: null,
      candidateRows: [],
      message: "opening 1000.0000 plus 2 rows reaches the declared closing",
    },
    rows: [],
    failures: [],
    metadataFailures: [],
    ...overrides,
  };
}

function accountProposal(
  overrides: Partial<AccountProposal> = {},
  resolution: Partial<ParseResponse["identityResolution"]> = {},
): ParseResponse {
  return parsed({
    accountId: null,
    identityResolution: {
      status: "needs_confirmation",
      institutionAction: "create",
      accountAction: "create",
      institution: {
        status: "needs_confirmation",
        proposedName: "Example Institution",
        country: "XY",
        candidates: [],
      },
      accountNumberMasked: "****1234",
      candidates: [],
      conflicts: [],
      proposalGroupKey: "pg1-example",
      ...resolution,
    },
    accountProposal: {
      accountNumberMasked: "****1234",
      suggestedAccounts: [],
      name: "Example savings",
      accountType: "savings",
      institutionId: null,
      institutionResolution: {
        status: "needs_confirmation",
        proposedName: "Example Institution",
        country: "XY",
        candidates: [],
      },
      country: "XY",
      currency: "XAE",
      openingBalance: "1490.0000",
      openingBalanceIsDerived: false,
      openingBalanceDate: "2026-03-01",
      openingBalanceNote: "Declared opening balance for the statement period",
      creditLimit: null,
      ...overrides,
    },
  });
}

function createdAccount(id = "acct-created"): AccountRow {
  return {
    ...accounts[0],
    id,
    name: "Example savings",
    accountNumberMasked: "****1234",
    accountType: "savings",
    country: "XY",
    currency: "XAE",
  } as AccountRow;
}

function pdf(name: string) {
  return new File([new Uint8Array([37, 80, 68, 70])], name, { type: "application/pdf" });
}

function choose(files: Array<File>) {
  const input = screen.getByLabelText("Statement files");
  fireEvent.change(input, { target: { files } });
}

function renderScreen(request: ReturnType<typeof vi.fn>) {
  render(
    <ImportScreen
      accounts={accounts}
      currencies={currencies}
      currency={currency}
      request={request as never}
    />,
  );
}

afterEach(cleanup);

describe("choosing files", () => {
  it("starts with the compact statement-import workspace from the product mock", async () => {
    const request = vi.fn();
    renderScreen(request);

    expect(screen.getByRole("heading", { name: "Statement import" })).toBeTruthy();
    expect(screen.getByLabelText("Upload statement files").className).toContain(
      "statement-import-dropzone",
    );
    expect(screen.getByText("PDF", { selector: ".statement-import-format" })).toBeTruthy();
    expect(screen.queryByText("CSV", { selector: ".statement-import-format" })).toBeNull();

    choose([pdf("march.pdf")]);
    const chosen = await screen.findByRole("listitem", { name: /march\.pdf/ });
    expect(chosen.className).toContain("statement-import-file-card");
  });

  it("lists every chosen file independently and uploads none of them", async () => {
    const request = vi.fn();
    renderScreen(request);

    choose([pdf("march.pdf"), pdf("april.pdf")]);

    expect(await screen.findByText("march.pdf")).toBeTruthy();
    expect(screen.getByText("april.pdf")).toBeTruthy();
    expect(request).not.toHaveBeenCalled();
  });

  it("accepts dropped PDFs through the same local candidate path", async () => {
    const request = vi.fn();
    renderScreen(request);

    fireEvent.drop(screen.getByLabelText("Upload statement files"), {
      dataTransfer: { files: [pdf("dropped.pdf")] },
    });

    expect(await screen.findByRole("listitem", { name: /dropped\.pdf/ })).toBeTruthy();
    expect(request).not.toHaveBeenCalled();
  });

  it("ignores a non-file drop without erasing the current candidates", async () => {
    const request = vi.fn();
    renderScreen(request);
    choose([pdf("kept.pdf")]);
    expect(await screen.findByRole("listitem", { name: /kept\.pdf/ })).toBeTruthy();

    fireEvent.drop(screen.getByLabelText("Upload statement files"), {
      dataTransfer: { files: [] },
    });

    expect(screen.getByRole("listitem", { name: /kept\.pdf/ })).toBeTruthy();
  });

  it("refuses a file that is not a PDF, and says why, without asking the API", async () => {
    const request = vi.fn();
    renderScreen(request);

    choose([new File(["x"], "statement.csv", { type: "text/csv" }), pdf("march.pdf")]);

    expect(await screen.findByText(/only PDF statements/i)).toBeTruthy();
    expect(screen.getByText("march.pdf")).toBeTruthy();
    expect(request).not.toHaveBeenCalled();
  });

  it("does not post anything until the owner asks for the check", async () => {
    const request = vi.fn().mockResolvedValue(parsed());
    renderScreen(request);
    choose([pdf("march.pdf")]);

    expect(request).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    const [path, init] = request.mock.calls[0];
    expect(path).toBe("/parse");
    expect((init.body as FormData).get("stage")).toBe("false");
  });
});

describe("per-file outcomes", () => {
  it("checks each file with its own request and reports each result separately", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(parsed({ rowsExtracted: 2 }))
      .mockRejectedValueOnce(new ApiError(422, "no layout matched this statement"));
    renderScreen(request);
    choose([pdf("march.pdf"), pdf("april.pdf")]);

    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });
    const april = await screen.findByRole("listitem", { name: /april\.pdf/ });
    expect(within(march).getByText(/2 rows/, { selector: "dd" })).toBeTruthy();
    expect(within(april).getByText("no layout matched this statement")).toBeTruthy();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("shows a blocked file's own verdict rather than a generic error", async () => {
    const request = vi.fn().mockResolvedValue(
      parsed({
        reconciliation: {
          ...parsed().reconciliation,
          status: "mismatched",
          computedClosing: "1400.0000",
          delta: "-90.0000",
          message: "the statement does not add up: declared 1490.0000, computed 1400.0000",
        },
      }),
    );
    renderScreen(request);
    choose([pdf("march.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });

    expect(
      within(march).getByText(/the statement does not add up: declared 1490.0000/),
    ).toBeTruthy();
    expect(within(march).queryByRole("button", { name: /stage/i })).toBeNull();
  });

  it("reports an encrypted file as needing a password and offers no prompt", async () => {
    const request = vi
      .fn()
      .mockRejectedValue(new ApiError(422, "this statement is password-protected"));
    renderScreen(request);
    choose([pdf("locked.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const locked = await screen.findByRole("listitem", { name: /locked\.pdf/ });

    expect(within(locked).getByText(/password-protected/)).toBeTruthy();
    expect(within(locked).queryByLabelText(/password/i)).toBeNull();
    expect(within(locked).queryByRole("button", { name: /stage/i })).toBeNull();
  });

  it("keeps the other files' outcomes when one of them fails", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(parsed())
      .mockRejectedValueOnce(new Error("network is gone"))
      .mockResolvedValueOnce(parsed({ rowsExtracted: 7, counts: { new: 7 } }));
    renderScreen(request);
    choose([pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const c = await screen.findByRole("listitem", { name: /c\.pdf/ });
    expect(within(c).getByText(/7 rows/, { selector: "dd" })).toBeTruthy();
    const a = screen.getByRole("listitem", { name: /a\.pdf/ });
    expect(within(a).getByText(/2 rows/, { selector: "dd" })).toBeTruthy();
    const b = screen.getByRole("listitem", { name: /b\.pdf/ });
    expect(within(b).getByText(/could not be checked/i)).toBeTruthy();
  });
});

describe("an encrypted statement", () => {
  // The service answers a file it cannot open with this exact sentence, from
  // `words.extract_pages`. The screen recognises it rather than guessing from the status code,
  // because a 422 covers every parse refusal and only this one is answerable by the owner.
  const LOCKED = "PDF is encrypted or the password is incorrect";

  it("offers a password field for the file that needs one, and no other", async () => {
    const request = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(422, LOCKED))
      .mockResolvedValueOnce(parsed());
    renderScreen(request);
    choose([pdf("locked.pdf"), pdf("open.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const locked = await screen.findByRole("listitem", { name: /locked\.pdf/ });
    const open = await screen.findByRole("listitem", { name: /open\.pdf/ });

    expect(within(locked).getByLabelText(/password/i)).toBeTruthy();
    expect(within(open).queryByLabelText(/password/i)).toBeNull();
  });

  it("sends the password for that file only", async () => {
    const request = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(422, LOCKED))
      .mockResolvedValueOnce(parsed())
      .mockResolvedValueOnce(parsed())
      .mockResolvedValueOnce(parsed());
    renderScreen(request);
    choose([pdf("locked.pdf"), pdf("open.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const locked = await screen.findByRole("listitem", { name: /locked\.pdf/ });
    fireEvent.change(within(locked).getByLabelText(/password/i), {
      target: { value: "let-me-in" },
    });

    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(4));
    const bodies = request.mock.calls.slice(2).map(([, init]) => init.body as FormData);
    expect(bodies.map((body) => body.get("password"))).toEqual(["let-me-in", null]);
  });

  it("shows the service's own refusal for a wrong password and keeps the others intact", async () => {
    // A second check re-runs every file that is not staged, so the pass takes two answers.
    const request = vi
      .fn()
      .mockResolvedValueOnce(parsed())
      .mockRejectedValueOnce(new ApiError(422, LOCKED))
      .mockResolvedValueOnce(parsed())
      .mockRejectedValueOnce(new ApiError(422, LOCKED));
    renderScreen(request);
    choose([pdf("open.pdf"), pdf("locked.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const locked = await screen.findByRole("listitem", { name: /locked\.pdf/ });
    fireEvent.change(within(locked).getByLabelText(/password/i), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(4));
    expect(within(locked).getByText(LOCKED)).toBeTruthy();
    const open = screen.getByRole("listitem", { name: /open\.pdf/ });
    expect(within(open).getByText(/2 rows/, { selector: "dd" })).toBeTruthy();
  });

  it("never writes the password anywhere but the request that uses it", async () => {
    const request = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(422, LOCKED))
      .mockResolvedValueOnce(parsed())
      .mockResolvedValueOnce(parsed({ staged: true, batchId: "batch-1" }));
    renderScreen(request);
    choose([pdf("locked.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const locked = await screen.findByRole("listitem", { name: /locked\.pdf/ });
    fireEvent.change(within(locked).getByLabelText(/password/i), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    await within(locked).findByRole("button", { name: /stage/i });
    fireEvent.click(within(locked).getByRole("button", { name: /stage/i }));
    await within(locked).findByRole("link", { name: /review/i });

    // Never persisted: a password is on the never-store list, and a browser store is storage.
    // Both named the same way, so both fail for the same reason. A bare `length === 0` on
    // either would hold whatever this screen did with the password.
    expect(JSON.stringify(window.localStorage)).not.toContain("s3cret");
    expect(JSON.stringify(window.sessionStorage)).not.toContain("s3cret");
    // Nor left on screen once the file is done with it.
    expect(within(locked).queryByLabelText(/password/i)).toBeNull();
    expect(document.body.textContent).not.toContain("s3cret");
  });

  it("re-sends the password when the file is staged", async () => {
    // Staging is a second `POST /parse`, and the service holds no password between requests.
    const request = vi
      .fn()
      .mockRejectedValueOnce(new ApiError(422, LOCKED))
      .mockResolvedValueOnce(parsed())
      .mockResolvedValueOnce(parsed({ staged: true, batchId: "batch-1" }));
    renderScreen(request);
    choose([pdf("locked.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const locked = await screen.findByRole("listitem", { name: /locked\.pdf/ });
    fireEvent.change(within(locked).getByLabelText(/password/i), { target: { value: "s3cret" } });
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    await within(locked).findByRole("button", { name: /stage/i });

    fireEvent.click(within(locked).getByRole("button", { name: /stage/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
    const staging = request.mock.calls[2][1].body as FormData;
    expect(staging.get("password")).toBe("s3cret");
    expect(staging.get("stage")).toBe("true");
  });
});

describe("staging", () => {
  it("stages one file on its own action and links to the batch it created", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(parsed())
      .mockResolvedValueOnce(parsed({ staged: true, batchId: "batch-1", documentId: "doc-1" }));
    renderScreen(request);
    choose([pdf("march.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });
    fireEvent.click(within(march).getByRole("button", { name: /stage/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    const [, init] = request.mock.calls[1];
    expect((init.body as FormData).get("stage")).toBe("true");
    const link = await within(march).findByRole("link", { name: /review/i });
    expect(link.getAttribute("href")).toBe("/import/batch-1");
  });

  it("does not claim a file was staged when the API says it was not", async () => {
    // `blocks_staging` is two conditions, and the second is a row that would not normalize.
    // The route answers 200 with `staged: false` rather than an error (spec §7.5), so HTTP
    // success is not staging success — the boolean is.
    const blocked = parsed({
      failures: [{ sourcePageNumber: 1, sourceRowNumber: 3, reason: "no date in the date column" }],
    });
    const request = vi
      .fn()
      .mockResolvedValueOnce(parsed())
      .mockResolvedValueOnce({ ...blocked, staged: false, batchId: null });
    renderScreen(request);
    choose([pdf("march.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });
    fireEvent.click(within(march).getByRole("button", { name: /stage/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect(within(march).queryByText(/staged for review/i)).toBeNull();
    expect(within(march).queryByRole("link", { name: /review/i })).toBeNull();
    expect(within(march).getByText(/was not staged/i)).toBeTruthy();
  });

  it("offers no Stage on a statement whose rows did not all normalize", async () => {
    // It reconciles — but a batch missing rows reconciles only by coincidence, and the API
    // refuses to stage it. Offering the button offers an action that cannot succeed.
    const request = vi.fn().mockResolvedValue(
      parsed({
        failures: [{ sourcePageNumber: 1, sourceRowNumber: 3, reason: "no date in the date column" }],
      }),
    );
    renderScreen(request);
    choose([pdf("march.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });

    expect(within(march).getByText(/no date in the date column/)).toBeTruthy();
    expect(within(march).queryByRole("button", { name: /stage/i })).toBeNull();
  });

  it("re-checks the files still waiting and leaves the staged one alone", async () => {
    // A second check re-posts the dry run, and an already-staged file's document is stored, so
    // the route would answer 409 — a duplicate of itself — and take its review link with it.
    // Asserted with a file still waiting beside it, so the check button is genuinely enabled
    // and it is the loop that has to skip, not the disabled button doing the work.
    const request = vi
      .fn()
      .mockResolvedValueOnce(parsed())
      .mockRejectedValueOnce(new Error("network is gone"))
      .mockResolvedValueOnce(parsed({ staged: true, batchId: "batch-1", documentId: "doc-1" }))
      .mockResolvedValueOnce(parsed());
    renderScreen(request);
    choose([pdf("march.pdf"), pdf("april.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });
    const april = await screen.findByRole("listitem", { name: /april\.pdf/ });
    await within(april).findByText(/could not be checked/i);
    fireEvent.click(within(march).getByRole("button", { name: /stage/i }));
    await within(march).findByRole("link", { name: /review/i });

    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    // Four calls, not five: march's check, april's failure, march's staging, april's re-check.
    await waitFor(() => expect(within(april).getByText(/2 rows/, { selector: "dd" })).toBeTruthy());
    expect(request).toHaveBeenCalledTimes(4);
    expect(within(march).getByRole("link", { name: /review/i }).getAttribute("href")).toBe(
      "/import/batch-1",
    );
  });

  it("stops offering a check once every file is staged", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(parsed())
      .mockResolvedValueOnce(parsed({ staged: true, batchId: "batch-1", documentId: "doc-1" }));
    renderScreen(request);
    choose([pdf("march.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });
    fireEvent.click(within(march).getByRole("button", { name: /stage/i }));
    await within(march).findByRole("link", { name: /review/i });

    expect(
      screen.getByRole<HTMLButtonElement>("button", { name: /check statements/i }).disabled,
    ).toBe(true);
  });

  it("reports a statement already imported without losing the file's place", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(parsed())
      .mockRejectedValueOnce(new ApiError(409, "this statement was imported on 2026-03-05"));
    renderScreen(request);
    choose([pdf("march.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });
    fireEvent.click(within(march).getByRole("button", { name: /stage/i }));

    expect(
      await within(march).findByText("this statement was imported on 2026-03-05"),
    ).toBeTruthy();
    expect(screen.getByText("march.pdf")).toBeTruthy();
  });

  it("sends the chosen account, and nothing when the statement is to be trusted", async () => {
    const request = vi.fn().mockResolvedValue(parsed());
    renderScreen(request);
    choose([pdf("march.pdf")]);

    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    expect((request.mock.calls[0][1].body as FormData).get("accountId")).toBeNull();

    fireEvent.change(screen.getByLabelText("Account"), { target: { value: "acct-savings" } });
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect((request.mock.calls[1][1].body as FormData).get("accountId")).toBe("acct-savings");
  });
});

describe("an account proposed by a statement", () => {
  it("creates the account, re-checks with its id, and then offers the ordinary Stage action", async () => {
    let parseCalls = 0;
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/institutions") return [];
      if (path === "/parse/account") return createdAccount();
      if (path === "/parse") {
        parseCalls += 1;
        if (parseCalls === 1) return accountProposal();
        if (parseCalls === 2) return parsed({ accountId: "acct-created" });
        return parsed({ staged: true, batchId: "batch-created", accountId: "acct-created" });
      }
      throw new Error(`Unexpected path ${path} ${init?.method ?? "GET"}`);
    });
    renderScreen(request);
    choose([pdf("unknown.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const item = await screen.findByRole("listitem", { name: /unknown\.pdf/ });

    fireEvent.click(
      await within(item).findByRole("button", { name: "Create account and continue" }),
    );
    const stage = await within(item).findByRole("button", { name: "Stage batch" });

    const accountCall = request.mock.calls.find(([path]) => path === "/parse/account");
    const accountBody = JSON.parse((accountCall?.[1]?.body as FormData).get("account") as string);
    expect(accountBody).not.toHaveProperty("accountNumberMasked");
    const recheck = request.mock.calls.filter(([path]) => path === "/parse")[1]![1]!.body as FormData;
    expect(recheck.get("accountId")).toBe("acct-created");

    fireEvent.click(stage);
    await within(item).findByRole("link", { name: /review/i });
    const staging = request.mock.calls.filter(([path]) => path === "/parse")[2]![1]!.body as FormData;
    expect(staging.get("accountId")).toBe("acct-created");
  });

  it("puts one form on the earliest statement and resolves every file sharing its mask", async () => {
    let parseCalls = 0;
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/institutions") return [];
      if (path === "/parse/account") return createdAccount();
      if (path === "/parse") {
        parseCalls += 1;
        if (parseCalls === 1) {
          return {
            ...accountProposal({ openingBalanceDate: "2026-04-01" }),
            period: { start: "2026-04-01", end: "2026-04-30", isDerived: false },
          };
        }
        if (parseCalls === 2) {
          return {
            ...accountProposal({ openingBalanceDate: "2026-03-01" }),
            period: { start: "2026-03-01", end: "2026-03-31", isDerived: false },
          };
        }
        return parsed({ accountId: "acct-created" });
      }
      throw new Error(`Unexpected path ${path} ${init?.method ?? "GET"}`);
    });
    renderScreen(request);
    choose([pdf("later.pdf"), pdf("earlier.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const later = await screen.findByRole("listitem", { name: /later\.pdf/ });
    const earlier = await screen.findByRole("listitem", { name: /earlier\.pdf/ });

    expect(within(later).queryByRole("button", { name: "Create account and continue" })).toBeNull();
    fireEvent.click(
      await within(earlier).findByRole("button", { name: "Create account and continue" }),
    );

    await within(later).findByRole("button", { name: "Stage batch" });
    await within(earlier).findByRole("button", { name: "Stage batch" });
    expect(request.mock.calls.filter(([path]) => path === "/parse/account")).toHaveLength(1);
    const rechecks = request.mock.calls.filter(([path]) => path === "/parse").slice(2);
    expect(rechecks).toHaveLength(2);
    expect(rechecks.map(([, init]) => (init!.body as FormData).get("accountId"))).toEqual([
      "acct-created",
      "acct-created",
    ]);
  });

  it("groups by the service proposal key even when display masks differ", async () => {
    let parseCalls = 0;
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return [];
      if (path === "/parse") {
        parseCalls += 1;
        if (parseCalls === 1) {
          return accountProposal(
            { accountNumberMasked: "****1111", openingBalanceDate: "2026-04-01" },
            { accountNumberMasked: "****1111", proposalGroupKey: "pg1-shared" },
          );
        }
        return {
          ...accountProposal(
            { accountNumberMasked: "••••1111", openingBalanceDate: "2026-03-01" },
            { accountNumberMasked: "••••1111", proposalGroupKey: "pg1-shared" },
          ),
          period: { start: "2026-03-01", end: "2026-03-31", isDerived: false },
        };
      }
      throw new Error(`Unexpected path ${path}`);
    });
    renderScreen(request);
    choose([pdf("later.pdf"), pdf("earlier.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const later = await screen.findByRole("listitem", { name: /later\.pdf/ });
    const earlier = await screen.findByRole("listitem", { name: /earlier\.pdf/ });
    expect(
      await within(earlier).findByRole("button", { name: "Create account and continue" }),
    ).toBeTruthy();
    expect(
      within(later).queryByRole("button", { name: "Create account and continue" }),
    ).toBeNull();
  });

  it("keeps two accounts at one institution separate even when their masks match", async () => {
    let parseCalls = 0;
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return [];
      if (path === "/parse") {
        parseCalls += 1;
        return accountProposal(
          { openingBalanceDate: parseCalls === 1 ? "2026-03-01" : "2026-04-01" },
          {
            institutionAction: "reuse",
            institution: {
              status: "matched",
              proposedName: "Example Institution",
              country: "XY",
              candidates: [],
            },
            proposalGroupKey: `pg1-account-${parseCalls}`,
        },
        );
      }
      throw new Error(`Unexpected path ${path}`);
    });
    renderScreen(request);
    choose([pdf("savings.pdf"), pdf("current.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const savings = await screen.findByRole("listitem", { name: /savings\.pdf/ });
    const current = await screen.findByRole("listitem", { name: /current\.pdf/ });
    expect(
      await within(savings).findByRole("button", { name: "Create account and continue" }),
    ).toBeTruthy();
    expect(
      within(current).getByRole("button", { name: "Create account and continue" }),
    ).toBeTruthy();
  });

  it("reconciles every grouped file independently after confirmation", async () => {
    let parseCalls = 0;
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return [];
      if (path === "/parse/account") return createdAccount();
      if (path === "/parse") {
        parseCalls += 1;
        if (parseCalls <= 2) {
          return {
            ...accountProposal(),
            period: parseCalls === 1
              ? { start: "2026-03-01", end: "2026-03-31", isDerived: false }
              : { start: "2026-04-01", end: "2026-04-30", isDerived: false },
          };
        }
        if (parseCalls === 3) return parsed({ accountId: "acct-created" });
        return parsed({
          accountId: "acct-created",
          reconciliation: {
            ...parsed().reconciliation,
            status: "mismatched",
            message: "this statement does not reconcile",
          },
        });
      }
      throw new Error(`Unexpected path ${path}`);
    });
    renderScreen(request);
    choose([pdf("march.pdf"), pdf("april.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });
    const april = await screen.findByRole("listitem", { name: /april\.pdf/ });
    fireEvent.click(
      await within(march).findByRole("button", { name: "Create account and continue" }),
    );

    expect(await within(march).findByRole("button", { name: "Stage batch" })).toBeTruthy();
    expect(await within(april).findByText("this statement does not reconcile")).toBeTruthy();
    expect(within(april).queryByRole("button", { name: "Stage batch" })).toBeNull();
  });

  it("serializes account confirmation with global checks and file replacement", async () => {
    let finishConfirmation!: (value: AccountRow) => void;
    let parseCalls = 0;
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return [];
      if (path === "/parse/account") {
        return new Promise<AccountRow>((resolve) => { finishConfirmation = resolve; });
      }
      if (path === "/parse") {
        parseCalls += 1;
        return parseCalls === 1
          ? accountProposal()
          : parsed({ accountId: "acct-created" });
      }
      throw new Error(`Unexpected path ${path}`);
    });
    renderScreen(request);
    choose([pdf("march.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });
    fireEvent.click(
      await within(march).findByRole("button", { name: "Create account and continue" }),
    );
    await waitFor(() => expect(request.mock.calls.some(([path]) => path === "/parse/account")).toBe(true));

    const check = screen.getByRole<HTMLButtonElement>("button", { name: /checking/i });
    const picker = screen.getByLabelText<HTMLInputElement>("Statement files");
    expect(check.disabled).toBe(true);
    expect(picker.disabled).toBe(true);
    fireEvent.click(check);
    fireEvent.change(picker, { target: { files: [pdf("replacement.pdf")] } });
    expect(parseCalls).toBe(1);
    expect(screen.getByRole("listitem", { name: /march\.pdf/ })).toBeTruthy();
    expect(screen.queryByRole("listitem", { name: /replacement\.pdf/ })).toBeNull();

    finishConfirmation(createdAccount());
    expect(await within(march).findByRole("button", { name: "Stage batch" })).toBeTruthy();
    expect(parseCalls).toBe(2);
  });

  it("keeps a rejected account confirmation visible and retryable", async () => {
    let accountCalls = 0;
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return [];
      if (path === "/parse/account") {
        accountCalls += 1;
        throw new ApiError(409, "That account identity is already assigned elsewhere.");
      }
      if (path === "/parse") return accountProposal();
      throw new Error(`Unexpected path ${path}`);
    });
    renderScreen(request);
    choose([pdf("march.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });
    fireEvent.click(
      await within(march).findByRole("button", { name: "Create account and continue" }),
    );

    expect((await within(march).findByRole("alert")).textContent).toBe(
      "That account identity is already assigned elsewhere.",
    );
    const retry = within(march).getByRole<HTMLButtonElement>("button", {
      name: "Create account and continue",
    });
    expect(retry.disabled).toBe(false);
    fireEvent.click(retry);
    await waitFor(() => expect(accountCalls).toBe(2));
  });

  it("waits for the whole selection before offering the earliest statement's form", async () => {
    let finishEarlier!: (value: ParseResponse) => void;
    let parseCalls = 0;
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return [];
      if (path === "/parse") {
        parseCalls += 1;
        if (parseCalls === 1) {
          return {
            ...accountProposal({ openingBalanceDate: "2026-04-01" }),
            period: { start: "2026-04-01", end: "2026-04-30", isDerived: false },
          };
        }
        return new Promise<ParseResponse>((resolve) => { finishEarlier = resolve; });
      }
      throw new Error(`Unexpected path ${path}`);
    });
    renderScreen(request);
    choose([pdf("later.pdf"), pdf("earlier.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const later = await screen.findByRole("listitem", { name: /later\.pdf/ });

    expect(
      within(later).queryByRole("button", { name: "Create account and continue" }),
    ).toBeNull();
    finishEarlier({
      ...accountProposal({ openingBalanceDate: "2026-03-01" }),
      period: { start: "2026-03-01", end: "2026-03-31", isDerived: false },
    });

    const earlier = await screen.findByRole("listitem", { name: /earlier\.pdf/ });
    expect(
      await within(earlier).findByRole("button", { name: "Create account and continue" }),
    ).toBeTruthy();
    expect(
      within(later).queryByRole("button", { name: "Create account and continue" }),
    ).toBeNull();
  });

  it("re-sends the file password through confirmation, re-check and staging, then clears it", async () => {
    let parseCalls = 0;
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/institutions") return [];
      if (path === "/parse/account") return createdAccount();
      if (path === "/parse") {
        parseCalls += 1;
        if (parseCalls === 1) throw new ApiError(422, "PDF is encrypted or the password is incorrect");
        if (parseCalls === 2) return accountProposal();
        if (parseCalls === 3) return parsed({ accountId: "acct-created" });
        return parsed({ staged: true, batchId: "batch-created", accountId: "acct-created" });
      }
      throw new Error(`Unexpected path ${path} ${init?.method ?? "GET"}`);
    });
    renderScreen(request);
    choose([pdf("locked.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    const item = await screen.findByRole("listitem", { name: /locked\.pdf/ });
    fireEvent.change(await within(item).findByLabelText(/password/i), {
      target: { value: "statement-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));
    fireEvent.click(
      await within(item).findByRole("button", { name: "Create account and continue" }),
    );
    fireEvent.click(await within(item).findByRole("button", { name: "Stage batch" }));
    await within(item).findByRole("link", { name: /review/i });

    const carryingPassword = request.mock.calls.filter(([path]) => (
      path === "/parse" || path === "/parse/account"
    )).slice(1);
    expect(carryingPassword.map(([, init]) => (init!.body as FormData).get("password"))).toEqual([
      "statement-secret",
      "statement-secret",
      "statement-secret",
      "statement-secret",
    ]);
    expect(within(item).queryByLabelText(/password/i)).toBeNull();
  });
});

describe("what the screen announces", () => {
  it("announces each file's progress to a screen reader", async () => {
    // WCAG AA (prd.html section 6): a state that changes across seconds of work has to be
    // announced, not only redrawn. The tree's own pattern is role="status".
    const request = vi.fn().mockResolvedValue(parsed());
    renderScreen(request);
    choose([pdf("march.pdf")]);

    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });
    const live = within(march).getByRole("status");
    expect(live.textContent).toContain("Ready to check");

    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    await waitFor(() => expect(within(march).getByRole("status").textContent).toContain("Checked"));
  });
});

describe("what the summary says", () => {
  it("names the institution and account that were matched automatically", async () => {
    const request = vi.fn().mockResolvedValue(parsed({
      identityResolution: {
        ...parsed().identityResolution,
        institution: {
          status: "matched",
          proposedName: "Synthetic Institution",
          country: "ZZ",
          candidates: [{
            id: "institution-1",
            name: "Synthetic Institution",
            shortName: "Synthetic",
            country: "ZZ",
            institutionType: "bank",
          }],
        },
        candidates: [{
          id: "acct-card",
          name: "Synthetic Card",
          institutionId: "institution-1",
          accountType: "credit_card",
          currency: "XAE",
          accountNumberMasked: "****1234",
          canReveal: true,
        }],
      },
    }));
    renderScreen(request);
    choose([pdf("march.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });
    expect(within(march).getByText("Matched automatically")).toBeTruthy();
    expect(within(march).getByText(
      "Synthetic Institution · Synthetic Card · ****1234",
    )).toBeTruthy();
  });

  it("formats the statement's own figures and derives none of them", async () => {
    const request = vi.fn().mockResolvedValue(parsed());
    renderScreen(request);
    choose([pdf("march.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });

    // formatMoney over the string the API sent — never arithmetic on it.
    expect(within(march).getByText(/A 1,490\.00/, { selector: "dd" })).toBeTruthy();
    expect(
      within(march).getByText(/2026-03-01 to 2026-03-31/, { selector: "dd" }),
    ).toBeTruthy();
  });

  it("formats the closing in the resolved account's currency, not the household's", async () => {
    // 1490.0000 groups as 1,490.00 internationally and as 1,490.00 in Indian grouping too —
    // so the figure is one that actually differs between the two.
    const request = vi.fn().mockResolvedValue(
      parsed({
        accountId: "acct-savings",
        reconciliation: { ...parsed().reconciliation, declaredClosing: "5982530.0000" },
      }),
    );
    renderScreen(request);
    choose([pdf("march.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });

    expect(within(march).getByText("I 59,82,530.00", { selector: "dd" })).toBeTruthy();
  });

  it("says a period was derived when the parse says so, and stays silent when it was not", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(parsed({ period: { start: "2026-03-01", end: "2026-03-31", isDerived: true } }))
      .mockResolvedValueOnce(parsed());
    renderScreen(request);
    choose([pdf("derived.pdf"), pdf("stated.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const derived = await screen.findByRole("listitem", { name: /derived\.pdf/ });
    const stated = await screen.findByRole("listitem", { name: /stated\.pdf/ });
    expect(within(derived).getByText(/derived from the rows/i)).toBeTruthy();
    expect(within(stated).queryByText(/derived from the rows/i)).toBeNull();
  });

  it("lists the rows that would not normalize, with their page and row numbers", async () => {
    const request = vi.fn().mockResolvedValue(
      parsed({
        failures: [
          { sourcePageNumber: 2, sourceRowNumber: 14, reason: "no amount in the amount column" },
        ],
      }),
    );
    renderScreen(request);
    choose([pdf("march.pdf")]);
    fireEvent.click(screen.getByRole("button", { name: /check statements/i }));

    const march = await screen.findByRole("listitem", { name: /march\.pdf/ });
    expect(within(march).getByText(/page 2, row 14/i)).toBeTruthy();
    expect(within(march).getByText(/no amount in the amount column/)).toBeTruthy();
  });
});
