// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { InvestmentImportScreen } from "@/app/investments/import/investment-import-screen";
import type {
  AccountRow,
  InvestmentCommitResponse,
  InvestmentParseResponse,
  SecurityResolution,
} from "@/types/api";

const account: AccountRow = {
  id: "acct-1",
  name: "Synthetic folio",
  institutionId: "inst-1",
  institutionName: "Synthetic platform",
  accountType: "mutual_fund_folio",
  accountClass: "asset",
  accountSubtype: null,
  currency: "XAA",
  country: "XZ",
  openedOn: "2026-01-01",
  closedOn: null,
  isActive: true,
  includeInNetWorth: true,
  isLiquid: false,
  openingBalance: "0.0000",
  openingBalanceDate: "2026-01-01",
  openingBalanceNote: null,
  openingBalanceConfirmedAt: "2026-01-01T00:00:00Z",
  currentBalance: "0.0000",
  currentBalanceAsOf: null,
  creditLimit: null,
  identifiers: [{
    id: "identifier-1",
    kind: "folio",
    masked: "****9999",
    isActive: true,
  }],
} as AccountRow;

it("links an owner without an investment account to the setup action", () => {
  render(<InvestmentImportScreen accounts={[]} request={vi.fn() as never} />);
  expect(screen.getByRole("heading", { level: 1, name: "Import investments" })).toBeTruthy();
  expect(screen.getByRole("heading", { level: 2, name: "Set up an investment account first" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Set up an investment account" }).getAttribute("href")).toBe("/accounts");
});

function draft(overrides: Partial<InvestmentParseResponse["groups"][number]["targets"][number]["drafts"][number]> = {}) {
  return {
    eventDate: "2026-01-05",
    eventKind: "purchase" as const,
    units: "10.0000",
    unitRatio: null,
    price: "100.0000",
    grossAmount: "1000.0000",
    charges: "0.0000",
    withheldTax: "0.0000",
    netAmount: "1000.0000",
    currency: "XAA",
    pageNumber: 1,
    rowNumber: 1,
    ...overrides,
  };
}

function unresolvedPreview(): InvestmentParseResponse {
  return {
    supported: true,
    staged: false,
    importId: null,
    status: "preview",
    groups: [
      {
        groupNumber: 1,
        stageable: false,
        status: null,
        targets: [
          {
            sourceGroupNumber: 1,
            accountId: null,
            securityId: null,
            accountMask: "****9999",
            securityLabel: "Example Fund",
            isin: "US0000000001",
            securityCandidates: [],
            drafts: [draft()],
            checkpoints: [],
            reasons: [],
          },
        ],
        reasons: [],
      },
    ],
    reasons: [],
    totalGroupCount: 1,
    totalDraftCount: 1,
    totalBlockerCount: 0,
    previewTruncated: false,
    expectedState: null,
  };
}

function stagedReady(): InvestmentParseResponse {
  const preview = unresolvedPreview();
  return {
    ...preview,
    staged: true,
    importId: "import-1",
    status: "awaiting_review",
    groups: [
      {
        ...preview.groups[0],
        stageable: true,
        status: "ready",
        targets: [
          { ...preview.groups[0].targets[0], accountId: "acct-1", securityId: "sec-1" },
        ],
      },
    ],
    expectedState: "state-1",
  };
}

function stagedCompleted(): InvestmentParseResponse {
  const staged = stagedReady();
  return {
    ...staged,
    status: "completed",
    groups: [{ ...staged.groups[0], status: "committed" }],
  };
}

function resolution(): SecurityResolution {
  return {
    status: "matched",
    reason: "exact_public_identity",
    candidates: [
      {
        id: "sec-1",
        securityType: "mutual_fund",
        name: "Example Fund",
        currency: "XAA",
        country: "XZ",
        assetClass: "equity",
        subAssetClass: null,
        isin: "US0000000001",
        ticker: null,
        exchange: null,
        manualPriceEligible: true,
      },
    ],
  };
}

function commitResult(): InvestmentCommitResponse {
  return {
    importId: "import-1",
    status: "completed",
    groups: [{ groupNumber: 1, status: "committed", reason: null }],
  };
}

function pdf(name = "statement.pdf") {
  return new File([new Uint8Array([37, 80, 68, 70])], name, { type: "application/pdf" });
}

async function runToStaged(request: ReturnType<typeof vi.fn>) {
  render(<InvestmentImportScreen accounts={[account]} request={request as never} />);

  expect(screen.getByRole("list", { name: "Import steps" }).querySelector('[aria-current="step"]')?.textContent).toContain("Check statement");

  const fileInput = screen.getByLabelText("Statement file");
  fireEvent.change(fileInput, { target: { files: [pdf()] } });
  fireEvent.click(screen.getByRole("button", { name: "Check statement" }));

  await screen.findByText("Example Fund", { exact: false });
  expect(screen.getByRole("list", { name: "Import steps" }).querySelector('[aria-current="step"]')?.textContent).toContain("Stage import");

  fireEvent.change(screen.getByLabelText("Public identifier"), { target: { value: "US0000000001" } });
  fireEvent.click(screen.getByRole("button", { name: "Find instrument" }));
  await waitFor(() => expect(screen.getByText("Change security")).toBeTruthy());

  fireEvent.change(screen.getByLabelText("Account"), { target: { value: "acct-1" } });

  const stageButton = screen.getByRole("button", { name: "Stage import" });
  await waitFor(() => expect(stageButton.hasAttribute("disabled")).toBe(false));
  fireEvent.click(stageButton);

  await screen.findByText("Ready to commit", { exact: false });
  expect(screen.getByRole("list", { name: "Import steps" }).querySelector('[aria-current="step"]')?.textContent).toContain("Commit ready groups");
}

afterEach(cleanup);

describe("investment statement import", () => {
  it("stages an uploaded statement and commits its ready group", async () => {
    const request = vi
      .fn()
      .mockResolvedValueOnce(unresolvedPreview())
      .mockResolvedValueOnce(resolution())
      .mockResolvedValueOnce(stagedReady())
      .mockResolvedValueOnce(commitResult())
      .mockResolvedValueOnce(stagedCompleted());

    await runToStaged(request);

    const commitButton = screen.getByRole("button", { name: "Commit ready groups" });
    expect(commitButton.hasAttribute("disabled")).toBe(false);
    fireEvent.click(commitButton);

    await screen.findByText("Commit result:");
    expect(screen.getByRole("link", { name: "Settings › Data quality" }).getAttribute("href")).toBe("/settings#data-quality-title");

    const commitCall = request.mock.calls[3];
    expect(commitCall[0]).toBe("/investment-imports/import-1/commit");
    expect(JSON.parse(commitCall[1].body as string)).toEqual({ expectedState: "state-1" });

    const stageCall = request.mock.calls[2];
    expect(stageCall[0]).toBe("/investment-imports/parse");
    const stagedForm = stageCall[1].body as FormData;
    expect(stagedForm.get("stage")).toBe("true");
    expect(JSON.parse(stagedForm.get("resolutions") as string)).toEqual([
      { sourceGroupNumber: 1, accountId: "acct-1", securityId: "sec-1" },
    ]);
  });

  it("does not let the owner commit a blocked group", async () => {
    const blocked: InvestmentParseResponse = {
      ...stagedReady(),
      status: "awaiting_review",
      groups: [
        {
          ...stagedReady().groups[0],
          stageable: false,
          status: "blocked",
          reasons: [{ code: "cost_mismatch", pageNumber: null, rowNumber: null }],
        },
      ],
    };
    const request = vi.fn().mockResolvedValueOnce(blocked);

    render(<InvestmentImportScreen accounts={[account]} importId="import-1" request={request as never} />);

    await screen.findByText("Blocked", { exact: false });
    const commitButton = screen.getByRole("button", { name: "Commit ready groups" });
    expect(commitButton.hasAttribute("disabled")).toBe(true);

    fireEvent.click(commitButton);
    expect(request).toHaveBeenCalledTimes(1);
  });

  it("blocks staging when the preview has more groups than it shows", async () => {
    const truncated: InvestmentParseResponse = {
      ...unresolvedPreview(),
      groups: [],
      previewTruncated: true,
    };
    const request = vi.fn().mockResolvedValueOnce(truncated);

    render(<InvestmentImportScreen accounts={[account]} request={request as never} />);
    fireEvent.change(screen.getByLabelText("Statement file"), { target: { files: [pdf()] } });
    fireEvent.click(screen.getByRole("button", { name: "Check statement" }));

    await screen.findByText(
      "This statement has more groups than the preview shows; staging is unavailable for it here.",
    );
    expect(screen.getByRole("button", { name: "Stage import" }).hasAttribute("disabled")).toBe(true);
  });

  it("writes nothing to browser storage while staging and committing", async () => {
    const setLocal = vi.spyOn(Storage.prototype, "setItem");
    const request = vi
      .fn()
      .mockResolvedValueOnce(unresolvedPreview())
      .mockResolvedValueOnce(resolution())
      .mockResolvedValueOnce(stagedReady())
      .mockResolvedValueOnce(commitResult())
      .mockResolvedValueOnce(stagedCompleted());

    await runToStaged(request);
    fireEvent.click(screen.getByRole("button", { name: "Commit ready groups" }));
    await screen.findByText("Commit result:");

    expect(setLocal).not.toHaveBeenCalled();
    setLocal.mockRestore();
  });
});
