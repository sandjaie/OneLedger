// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LayoutMapper } from "@/app/import/layout-mapper";
import type { LayoutCreate, LayoutProfile, LayoutProposal } from "@/types/api";

function draft(overrides: Partial<LayoutCreate> = {}): LayoutCreate {
  return {
    name: "Acme layout",
    institutionId: null,
    accountType: null,
    fingerprints: ["Acme Bank"],
    headerRowPattern: "Date\\s+Description\\s+Amount",
    columns: [
      { field: "booked_date", xMin: 0, xMax: 50, label: null },
      { field: "description", xMin: 50, xMax: 200, label: null },
      { field: "amount", xMin: 200, xMax: 260, label: null },
    ],
    dateFormat: "dd/MM/yyyy",
    signConvention: "signed",
    invertsSign: false,
    chronologicalOrder: "ascending",
    rowStartRule: "has_date_and_amount",
    continuationRule: null,
    skipLinePatterns: [],
    openingBalancePattern: null,
    closingBalancePattern: null,
    hasRunningBalance: false,
    quirks: [],
    metadataPatterns: null,
    ...overrides,
  };
}

function proposal(overrides: Partial<LayoutProposal> = {}): LayoutProposal {
  return {
    draft: draft(),
    rows: [],
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
    failures: [],
    rowsExtracted: 2,
    identifies: true,
    accountNumberCaptures: 1,
    ...overrides,
  };
}

function saveButton() {
  return screen.getByRole("button", { name: /save layout/i }) as HTMLButtonElement;
}

function nameField() {
  return screen.getByLabelText("Name (required)") as HTMLInputElement;
}

function pdf() {
  return new File([new Uint8Array([37, 80, 68, 70])], "statement.pdf", { type: "application/pdf" });
}

afterEach(cleanup);

describe("LayoutMapper", () => {
  it("enables Save only once the preview matches a fully mapped, reconciling draft", async () => {
    const request = vi.fn(async (path: string) => {
      if (path === "/layouts/propose") return proposal();
      throw new Error(`unexpected ${path}`);
    });
    render(
      <LayoutMapper
        file={pdf()}
        onCancel={vi.fn()}
        onSaved={vi.fn()}
        request={request as never}
        savedNames={[]}
        seed={{ draft: draft(), remap: false }}
      />,
    );

    expect(saveButton().disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));
    await screen.findByText(/reaches the declared closing/);

    // The preview matches, but no account type is chosen yet.
    expect(saveButton().disabled).toBe(true);

    // Choosing it now is itself an edit, so the preview must be run again against it.
    fireEvent.change(screen.getByLabelText("Account type (required)"), {
      target: { value: "savings" },
    });
    expect(saveButton().disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));
    await screen.findByText(/reaches the declared closing/);
    expect(saveButton().disabled).toBe(false);
  });

  it("disables Save again once the form is edited after a matching preview", async () => {
    const request = vi.fn(async () => proposal());
    render(
      <LayoutMapper
        file={pdf()}
        onCancel={vi.fn()}
        onSaved={vi.fn()}
        request={request as never}
        savedNames={[]}
        seed={{ draft: draft({ accountType: "savings" }), remap: false }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));
    await screen.findByText(/reaches the declared closing/);
    expect(saveButton().disabled).toBe(false);

    fireEvent.change(screen.getByLabelText("Name (required)"), {
      target: { value: "Edited after preview" },
    });
    expect(saveButton().disabled).toBe(true);
  });

  it("never replaces the form with the echoed preview draft", async () => {
    const request = vi.fn(async () => proposal({ draft: draft({ name: "Echoed different name" }) }));
    render(
      <LayoutMapper
        file={pdf()}
        onCancel={vi.fn()}
        onSaved={vi.fn()}
        request={request as never}
        savedNames={[]}
        seed={{ draft: draft({ name: "Owner typed name" }), remap: false }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));
    await screen.findByText(/reaches the declared closing/);

    expect(screen.getByDisplayValue("Owner typed name")).toBeTruthy();
  });

  it("warns of the next version when mapping onto a saved name, and locks the name when remapping", () => {
    render(
      <LayoutMapper
        file={pdf()}
        onCancel={vi.fn()}
        onSaved={vi.fn()}
        request={vi.fn() as never}
        savedNames={["Acme layout"]}
        seed={{ draft: draft({ name: "Acme layout" }), remap: false }}
      />,
    );
    expect(screen.getByText(/next version of the existing layout/i)).toBeTruthy();
    expect(nameField().disabled).toBe(false);

    cleanup();

    render(
      <LayoutMapper
        file={pdf()}
        onCancel={vi.fn()}
        onSaved={vi.fn()}
        request={vi.fn() as never}
        savedNames={["Acme layout"]}
        seed={{ draft: draft({ name: "Acme layout" }), remap: true }}
      />,
    );
    expect(screen.queryByText(/next version of the existing layout/i)).toBeNull();
    expect(nameField().disabled).toBe(true);
  });

  it("saves the mapped draft as JSON and reports the created profile", async () => {
    const savedProfile = {
      id: "layout-9",
      version: 1,
      ...draft({ accountType: "savings" }),
    } as unknown as LayoutProfile;
    const request = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === "/layouts/propose") return proposal();
      if (path === "/layouts") return savedProfile;
      throw new Error(`unexpected ${path} ${init?.method ?? "GET"}`);
    });
    const onSaved = vi.fn();
    render(
      <LayoutMapper
        file={pdf()}
        onCancel={vi.fn()}
        onSaved={onSaved}
        request={request as never}
        savedNames={[]}
        seed={{ draft: draft({ accountType: "savings" }), remap: false }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));
    await screen.findByText(/reaches the declared closing/);
    fireEvent.click(screen.getByRole("button", { name: /save layout/i }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(savedProfile));
    const call = request.mock.calls.find(([path]) => path === "/layouts");
    expect(call?.[1]).toMatchObject({ method: "POST" });
    expect(JSON.parse(call?.[1]?.body as string)).toMatchObject({
      name: "Acme layout",
      accountType: "savings",
    });
  });

  it("keeps Save disabled and shows the reasons when the preview does not reconcile", async () => {
    const request = vi.fn(async () => proposal({
      reconciliation: {
        ...proposal().reconciliation!,
        status: "mismatched",
        message: "the statement does not add up",
      },
      failures: [
        { sourcePageNumber: 1, sourceRowNumber: 3, reason: "booked_date is not a dd/MM/yyyy date" },
      ],
    }));
    render(
      <LayoutMapper
        file={pdf()}
        onCancel={vi.fn()}
        onSaved={vi.fn()}
        request={request as never}
        savedNames={[]}
        seed={{ draft: draft({ accountType: "savings" }), remap: false }}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /^preview$/i }));
    await screen.findByText(/does not add up/);

    expect(screen.getByText(/booked_date is not a dd\/MM\/yyyy date/)).toBeTruthy();
    expect(saveButton().disabled).toBe(true);
  });

  it("cancels back to the caller without saving anything", () => {
    const onCancel = vi.fn();
    render(
      <LayoutMapper
        file={pdf()}
        onCancel={onCancel}
        onSaved={vi.fn()}
        request={vi.fn() as never}
        savedNames={[]}
        seed={{ draft: draft(), remap: false }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
