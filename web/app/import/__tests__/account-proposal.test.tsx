// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccountProposalPanel } from "@/app/import/account-proposal";
import { ApiError } from "@/lib/api/client";
import type {
  AccountRow,
  CurrencyRow,
  IdentityResolution,
  InstitutionRow,
} from "@/types/api";

const currencies: Array<CurrencyRow> = [
  {
    code: "XAE",
    name: "Synthetic A",
    symbol: "A",
    decimals: 2,
    numberFormat: "international",
  },
];

const institutions: Array<InstitutionRow> = [
  {
    id: "institution-1",
    name: "Example Institution",
    shortName: "Example",
    country: "XY",
    institutionType: "bank",
  },
];

function account(): AccountRow {
  return {
    id: "account-created",
    name: "Example savings",
    institutionId: "institution-1",
    institutionName: "Example Institution",
    accountType: "credit_card",
    accountSubtype: null,
    accountClass: "liability",
    currency: "XAE",
    country: "XY",
    openedOn: null,
    closedOn: null,
    includeInNetWorth: true,
    isLiquid: true,
    openingBalance: "-1490.0000",
    openingBalanceDate: "2026-03-01",
    openingBalanceNote: "Declared opening balance for the statement period",
    openingBalanceConfirmedAt: null,
    creditLimit: "5000.0000",
    currentBalance: "-1490.0000",
    currentBalanceAsOf: "2026-03-01",
    isActive: true,
    identifiers: [],
  };
}

function resolution(overrides: Partial<IdentityResolution> = {}): IdentityResolution {
  return {
    status: "needs_confirmation",
    institutionAction: "reuse",
    accountAction: "create",
    institution: {
      status: "matched",
      proposedName: "Example Institution",
      country: "XY",
      candidates: [institutions[0]],
    },
    accountNumberMasked: "****1234",
    candidates: [],
    conflicts: [],
    proposalGroupKey: "pg1-example",
    name: "Example savings",
    accountType: "savings",
    country: "XY",
    currency: "XAE",
    openingBalance: "1490.0000",
    openingBalanceIsDerived: false,
    openingBalanceDate: "2026-03-01",
    openingBalanceNote: "Declared opening balance for the statement period",
    creditLimit: null,
    ...overrides,
  };
}

afterEach(cleanup);

describe("an account proposed by a statement", () => {
  it("renders and formats the prefill, then posts money as strings without identity fields", async () => {
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/institutions") return institutions;
      if (path === "/parse/account") return account();
      throw new Error(`Unexpected path ${path} ${init?.method ?? "GET"}`);
    });
    const onResolved = vi.fn();
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        layoutId="layout-1"
        onBusyChange={vi.fn()}
        onResolved={onResolved}
        password="secret"
        resolution={resolution({
          accountType: "credit_card",
          openingBalance: "-1490.0000",
          creditLimit: "5000.0000",
        })}
        request={request as never}
        screenBusy={false}
      />,
    );

    expect(screen.getByDisplayValue("Example savings")).toBeTruthy();
    expect(screen.getByText("****1234")).toBeTruthy();
    expect(screen.getByText("-A 1,490.00")).toBeTruthy();
    expect(screen.getByText("A 5,000.00")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Name (required)"), {
      target: { value: "Owner-corrected card" },
    });
    fireEvent.change(screen.getByLabelText("Country (required)"), {
      target: { value: "XZ" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create account and continue" }));

    await waitFor(() => expect(onResolved).toHaveBeenCalledWith(account()));
    const call = request.mock.calls.find(([path]) => path === "/parse/account");
    const form = call?.[1]?.body as FormData;
    expect(form.get("password")).toBe("secret");
    expect(form.get("layoutId")).toBe("layout-1");
    expect(form.get("accountId")).toBeNull();
    const sent = JSON.parse(form.get("account") as string);
    expect(sent).toMatchObject({
      openingBalance: "-1490.0000",
      creditLimit: "5000.0000",
      currency: "XAE",
      country: "XZ",
      name: "Owner-corrected card",
    });
    expect(sent).not.toHaveProperty("accountNumber");
    expect(sent).not.toHaveProperty("accountNumberMasked");
  });

  it("keeps create disabled until every nullable required prefill is supplied", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return institutions;
      throw new Error(`Unexpected path ${path}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onBusyChange={vi.fn()}
        onResolved={vi.fn()}
        resolution={resolution({ country: null, currency: null, openingBalance: null })}
        request={request as never}
        screenBusy={false}
      />,
    );

    const create = screen.getByRole<HTMLButtonElement>("button", {
      name: "Create account and continue",
    });
    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Currency (required)"), {
      target: { value: "XAE" },
    });
    fireEvent.change(screen.getByLabelText("Country (required)"), {
      target: { value: "XY" },
    });
    fireEvent.change(screen.getByLabelText(/^Opening balance \(required\)/), {
      target: { value: "0.0000" },
    });
    expect(create.disabled).toBe(false);
  });

  it("does not offer a create body that the account contract would refuse", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return institutions;
      throw new Error(`Unexpected path ${path}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onBusyChange={vi.fn()}
        onResolved={vi.fn()}
        resolution={resolution({
          accountType: "credit_card",
          openingBalance: "1490.0000",
          creditLimit: null,
        })}
        request={request as never}
        screenBusy={false}
      />,
    );
    const create = screen.getByRole<HTMLButtonElement>("button", {
      name: "Create account and continue",
    });

    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/^Opening balance \(required\)/), {
      target: { value: "-1490.0000" },
    });
    const creditLimit = screen.getByLabelText(/^Credit limit/);
    fireEvent.change(creditLimit, {
      target: { value: "5000.00000" },
    });
    expect(create.disabled).toBe(true);
    fireEvent.change(creditLimit, {
      target: { value: "5000.0000" },
    });
    expect(create.disabled).toBe(false);

    fireEvent.change(screen.getByLabelText("Account type (required)"), {
      target: { value: "savings" },
    });
    expect(create.disabled).toBe(true);
    fireEvent.change(creditLimit, { target: { value: "" } });
    expect(create.disabled).toBe(false);
  });

  it("adopts a suggested account and shows the service's own conflict sentence", async () => {
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/institutions") return institutions;
      if (path === "/parse/account") throw new ApiError(409, "That identity belongs elsewhere.");
      throw new Error(`Unexpected path ${path} ${init?.method ?? "GET"}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onBusyChange={vi.fn()}
        onResolved={vi.fn()}
        resolution={resolution({
          status: "ambiguous",
          accountAction: "choose",
          candidates: [{
            id: "account-existing",
            name: "Existing savings",
            institutionId: "institution-1",
            accountType: "savings",
            currency: "XAE",
            accountNumberMasked: "****1234",
            canReveal: false,
          }],
        })}
        request={request as never}
        screenBusy={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Use Existing savings ****1234" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "That identity belongs elsewhere.",
    );
    const call = request.mock.calls.find(([path]) => path === "/parse/account");
    const form = call?.[1]?.body as FormData;
    expect(form.get("accountId")).toBe("account-existing");
    expect(form.get("account")).toBeNull();
  });

  it("creates a new institution from corrected statement details", async () => {
    const request = vi.fn(async (path: string, _init?: RequestInit) => {
      void _init;
      if (path === "/institutions") return institutions;
      if (path === "/parse/account") return account();
      throw new Error(`Unexpected path ${path}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onBusyChange={vi.fn()}
        onResolved={vi.fn()}
        resolution={resolution({
          institutionAction: "create",
          institution: {
            status: "needs_confirmation",
            proposedName: "Future Bank",
            country: "XY",
            candidates: [],
          },
        })}
        request={request as never}
        screenBusy={false}
      />,
    );

    expect(screen.getByText("New institution and account")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Institution name (required)"), {
      target: { value: "Future Bank Local" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create account and continue" }));

    await waitFor(() => expect(request.mock.calls.some(([path]) => path === "/parse/account")).toBe(true));
    const call = request.mock.calls.find(([path]) => path === "/parse/account");
    const form = call?.[1]?.body as FormData;
    expect(JSON.parse(form.get("institution") as string)).toMatchObject({
      name: "Future Bank Local",
      country: "XY",
      institutionType: "bank",
    });
    expect(JSON.parse(form.get("account") as string).institutionId).toBeNull();
  });

  it("updates the decision when an unknown issuer is assigned to an existing institution", async () => {
    const request = vi.fn(async (path: string, _init?: RequestInit) => {
      void _init;
      if (path === "/institutions") return institutions;
      if (path === "/parse/account") return account();
      throw new Error(`Unexpected path ${path}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onBusyChange={vi.fn()}
        onResolved={vi.fn()}
        resolution={resolution({
          institutionAction: "create",
          institution: {
            status: "needs_confirmation",
            proposedName: "Future Bank",
            country: "XY",
            candidates: [],
          },
        })}
        request={request as never}
        screenBusy={false}
      />,
    );

    expect(screen.getByText("New institution and account")).toBeTruthy();
    const institution = await screen.findByLabelText("Institution (required)");
    fireEvent.change(institution, { target: { value: "institution-1" } });
    expect(screen.getByText("New account at an existing institution")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Create account and continue" }));

    await waitFor(() => expect(request.mock.calls.some(([path]) => path === "/parse/account")).toBe(true));
    const call = request.mock.calls.find(([path]) => path === "/parse/account");
    const form = call?.[1]?.body as FormData;
    expect(JSON.parse(form.get("account") as string).institutionId).toBe("institution-1");
    expect(form.get("institution")).toBeNull();
  });

  it("enforces the institution short-name limit before confirmation", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return institutions;
      throw new Error(`Unexpected path ${path}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onBusyChange={vi.fn()}
        onResolved={vi.fn()}
        resolution={resolution({
          institutionAction: "create",
          institution: {
            status: "needs_confirmation",
            proposedName: "Future Bank",
            country: "XY",
            candidates: [],
          },
        })}
        request={request as never}
        screenBusy={false}
      />,
    );

    const shortName = screen.getByLabelText<HTMLInputElement>("Institution short name");
    const create = screen.getByRole<HTMLButtonElement>("button", {
      name: "Create account and continue",
    });
    expect(shortName.maxLength).toBe(40);
    fireEvent.change(shortName, { target: { value: "x".repeat(41) } });
    expect(create.disabled).toBe(true);
    fireEvent.change(shortName, { target: { value: "x".repeat(40) } });
    expect(create.disabled).toBe(false);
    expect(request.mock.calls.some(([path]) => path === "/parse/account")).toBe(false);
  });

  it("reveals from the selected PDF, then clears the value when hidden", async () => {
    const request = vi.fn(async (path: string, _init?: RequestInit) => {
      void _init;
      if (path === "/institutions") return institutions;
      if (path === "/parse/identifier/reveal") {
        return { status: "revealed", value: "2468011234", kind: "bank_account" };
      }
      throw new Error(`Unexpected path ${path}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        layoutId="layout-1"
        onBusyChange={vi.fn()}
        onResolved={vi.fn()}
        password="statement-secret"
        resolution={resolution()}
        request={request as never}
        screenBusy={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Show account number from statement" }));
    expect(await screen.findByText("2468011234")).toBeTruthy();
    const call = request.mock.calls.find(([path]) => path === "/parse/identifier/reveal");
    const form = call?.[1]?.body as FormData;
    expect(form.get("file")).toBeInstanceOf(File);
    expect(form.get("password")).toBe("statement-secret");
    expect(form.get("layoutId")).toBe("layout-1");
    expect(form.get("accountNumber")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Hide account number from statement" }));
    expect(screen.queryByText("2468011234")).toBeNull();
  });

  it("explains when the PDF itself contains only a mask", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return institutions;
      if (path === "/parse/identifier/reveal") {
        return { status: "unavailable", reason: "source_mask_only" };
      }
      throw new Error(`Unexpected path ${path}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onBusyChange={vi.fn()}
        onResolved={vi.fn()}
        resolution={resolution()}
        request={request as never}
        screenBusy={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Show account number from statement" }));
    expect(await screen.findByText("Statement supplied only a masked value.")).toBeTruthy();
    expect(screen.getByRole<HTMLButtonElement>("button", {
      name: "Full account number unavailable from statement",
    }).disabled).toBe(true);
  });

  it("aborts an in-flight reveal and leaves no plaintext when removed", async () => {
    let signal: AbortSignal | undefined;
    const request = vi.fn((path: string, init?: RequestInit) => {
      if (path === "/institutions") return Promise.resolve(institutions);
      if (path === "/parse/identifier/reveal") {
        signal = init?.signal ?? undefined;
        return new Promise(() => undefined);
      }
      throw new Error(`Unexpected path ${path}`);
    });
    const view = render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onBusyChange={vi.fn()}
        onResolved={vi.fn()}
        resolution={resolution()}
        request={request as never}
        screenBusy={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Show account number from statement" }));
    await waitFor(() => expect(signal).toBeDefined());
    view.unmount();

    expect(signal?.aborted).toBe(true);
    expect(document.body.textContent).not.toContain("2468011234");
  });

  it("names conflicts and refuses to offer an unsafe account action", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/institutions") return institutions;
      if (path === "/parse/account") return account();
      throw new Error(`Unexpected path ${path}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onBusyChange={vi.fn()}
        onResolved={vi.fn()}
        resolution={resolution({
          status: "ambiguous",
          accountAction: "choose",
          candidates: [{
            id: "account-existing",
            name: "Existing savings",
            institutionId: "institution-1",
            accountType: "savings",
            currency: "XAE",
            accountNumberMasked: "****1234",
            canReveal: true,
          }],
          conflicts: ["currency"],
        })}
        request={request as never}
        screenBusy={false}
      />,
    );

    expect(screen.getByText("Currency does not match the stored account.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Create account and continue" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Use Existing savings/ })).toBeNull();
    expect(request.mock.calls.some(([path]) => path === "/parse/account")).toBe(false);
  });

  it("requires an explicit choice between ambiguous compatible accounts", async () => {
    const request = vi.fn(async (path: string, _init?: RequestInit) => {
      void _init;
      if (path === "/institutions") return institutions;
      if (path === "/parse/account") return account();
      throw new Error(`Unexpected path ${path}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onBusyChange={vi.fn()}
        onResolved={vi.fn()}
        resolution={resolution({
          status: "ambiguous",
          accountAction: "choose",
          candidates: [{
            id: "account-existing",
            name: "Existing savings",
            institutionId: "institution-1",
            accountType: "savings",
            currency: "XAE",
            accountNumberMasked: "****1234",
            canReveal: true,
          }],
        })}
        request={request as never}
        screenBusy={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Use Existing savings/ }));
    await waitFor(() => expect(request.mock.calls.some(([path]) => path === "/parse/account")).toBe(true));
    const call = request.mock.calls.find(([path]) => path === "/parse/account");
    expect((call?.[1]?.body as FormData).get("accountId")).toBe("account-existing");
  });

  it("requires an explicit institution choice when PDF evidence matches more than one", async () => {
    const second = { ...institutions[0], id: "institution-2", name: "Example Holdings" };
    const request = vi.fn(async (path: string, _init?: RequestInit) => {
      void _init;
      if (path === "/institutions") return [institutions[0], second];
      if (path === "/parse/account") return account();
      throw new Error(`Unexpected path ${path}`);
    });
    render(
      <AccountProposalPanel
        currencies={currencies}
        file={new File(["pdf"], "statement.pdf", { type: "application/pdf" })}
        onBusyChange={vi.fn()}
        onResolved={vi.fn()}
        resolution={resolution({
          status: "ambiguous",
          institutionAction: "choose",
          accountAction: "choose",
          institution: {
            status: "ambiguous",
            proposedName: "Example",
            country: "XY",
            candidates: [institutions[0], second],
          },
          candidates: [],
        })}
        request={request as never}
        screenBusy={false}
      />,
    );

    const create = screen.getByRole<HTMLButtonElement>("button", {
      name: "Create account and continue",
    });
    expect(screen.getByText("Choose an institution for this new account")).toBeTruthy();
    expect(create.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Institution (required)"), {
      target: { value: "institution-2" },
    });
    expect(screen.getByText("New account at an existing institution")).toBeTruthy();
    expect(create.disabled).toBe(false);
    fireEvent.click(create);

    await waitFor(() => expect(request.mock.calls.some(([path]) => path === "/parse/account")).toBe(true));
    const call = request.mock.calls.find(([path]) => path === "/parse/account");
    const accountBody = JSON.parse((call?.[1]?.body as FormData).get("account") as string);
    expect(accountBody.institutionId).toBe("institution-2");
  });
});
