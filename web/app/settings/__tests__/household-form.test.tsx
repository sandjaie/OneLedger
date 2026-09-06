// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { HouseholdForm } from "@/app/settings/household-form";
import { ApiError } from "@/lib/api/client";
import type { CurrencyRow, HouseholdResponse } from "@/types/api";

const currencies: Array<CurrencyRow> = [
  { code: "XAE", name: "Synthetic A", symbol: "A", decimals: 2, numberFormat: "international" },
  { code: "XIN", name: "Synthetic I", symbol: "I", decimals: 2, numberFormat: "indian" },
  { code: "XZZ", name: "Synthetic Z", symbol: "Z", decimals: 3, numberFormat: "international" },
];

const household: HouseholdResponse = {
  id: "synthetic-owner",
  name: "Household",
  ownerDisplayName: "Owner",
  ownerTaxResidency: null,
  baseCurrency: "XIN",
  secondaryCurrency: "XAE",
  reportingPairLocked: false,
  currencies: [currencies[1], currencies[0]],
};

afterEach(cleanup);

describe("Settings › Household", () => {
  it("offers every catalogue currency, not only the configured pair", () => {
    render(<HouseholdForm household={household} currencies={currencies}
      save={vi.fn()} onSaved={vi.fn()} />);
    const base = screen.getByLabelText("Base currency") as HTMLSelectElement;
    expect([...base.options].map((option) => option.value)).toEqual(["XAE", "XIN", "XZZ"]);
    expect(base.value).toBe("XIN");
  });

  it("saves every editable field in one request", async () => {
    const save = vi.fn().mockResolvedValue(undefined);
    const onSaved = vi.fn();
    render(<HouseholdForm household={household} currencies={currencies}
      save={save} onSaved={onSaved} />);

    fireEvent.change(screen.getByLabelText("Household name"), { target: { value: "Home" } });
    fireEvent.change(screen.getByLabelText("Your display name"), { target: { value: "Me" } });
    fireEvent.change(screen.getByLabelText("Tax residency"), { target: { value: "XA" } });
    fireEvent.change(screen.getByLabelText("Secondary currency"), { target: { value: "XZZ" } });
    fireEvent.click(screen.getByRole("button", { name: "Save household" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(save).toHaveBeenCalledWith({
      name: "Home",
      ownerDisplayName: "Me",
      ownerTaxResidency: "XA",
      baseCurrency: "XIN",
      secondaryCurrency: "XZZ",
    });
  });

  it("refuses one currency in both roles without calling the API", () => {
    const save = vi.fn();
    render(<HouseholdForm household={household} currencies={currencies}
      save={save} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Secondary currency"), { target: { value: "XIN" } });
    fireEvent.click(screen.getByRole("button", { name: "Save household" }));
    expect(screen.getByRole("alert").textContent).toContain("two different currencies");
    expect(save).not.toHaveBeenCalled();
  });

  it("disables the pair and explains why once amounts are frozen", () => {
    render(<HouseholdForm household={{ ...household, reportingPairLocked: true }}
      currencies={currencies} save={vi.fn()} onSaved={vi.fn()} />);
    expect(screen.getByLabelText<HTMLSelectElement>("Base currency").disabled).toBe(true);
    expect(screen.getByLabelText<HTMLSelectElement>("Secondary currency").disabled).toBe(true);
    expect(screen.getByText(/backfill/i)).toBeTruthy();
    // The rest of the configuration stays editable behind a locked pair.
    expect(screen.getByLabelText<HTMLInputElement>("Household name").disabled).toBe(false);
  });

  it("shows the server's explanation when the pair is refused", async () => {
    const save = vi.fn().mockRejectedValue(
      new ApiError(409, "The reporting pair is fixed once amounts are frozen."),
    );
    render(<HouseholdForm household={household} currencies={currencies}
      save={save} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByLabelText("Secondary currency"), { target: { value: "XZZ" } });
    fireEvent.click(screen.getByRole("button", { name: "Save household" }));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toMatch(/fixed once amounts are frozen/),
    );
    expect(screen.getByLabelText<HTMLInputElement>("Household name").disabled).toBe(false);
  });

  it("does not convert an expired session into an inline save error", async () => {
    const save = vi.fn().mockRejectedValue(new ApiError(401, "not signed in"));
    const onSaved = vi.fn();
    render(<HouseholdForm household={household} currencies={currencies}
      save={save} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole("button", { name: "Save household" }));
    await waitFor(() => expect(save).toHaveBeenCalled());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(onSaved).not.toHaveBeenCalled();
  });
});
