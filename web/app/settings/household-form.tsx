"use client";

import { useState } from "react";

import { ApiError } from "@/lib/api/client";
import type { CurrencyRow, HouseholdResponse, HouseholdUpdate } from "@/types/api";

type Props = {
  household: HouseholdResponse;
  currencies: Array<CurrencyRow>;
  save: (body: HouseholdUpdate) => Promise<void>;
  onSaved: () => void;
};

const LOCKED_EXPLANATION =
  "Both amounts are frozen on every row, so changing which two currencies the pair contains is " +
  "a backfill from stored FX history, not a settings change. The pair is fixed now that amounts " +
  "are frozen; which of the two you look at is still yours to choose in the header.";

export function HouseholdForm({ household, currencies, save, onSaved }: Props) {
  const [name, setName] = useState(household.name);
  const [displayName, setDisplayName] = useState(household.ownerDisplayName);
  const [residency, setResidency] = useState(household.ownerTaxResidency ?? "");
  const [base, setBase] = useState(household.baseCurrency);
  const [secondary, setSecondary] = useState(household.secondaryCurrency);
  const [problem, setProblem] = useState<string>();
  const [saving, setSaving] = useState(false);
  const locked = household.reportingPairLocked;

  async function submit() {
    if (base === secondary) {
      setProblem("The reporting pair must name two different currencies.");
      return;
    }
    setProblem(undefined);
    setSaving(true);
    try {
      await save({
        name,
        ownerDisplayName: displayName,
        ownerTaxResidency: residency === "" ? null : residency.toUpperCase(),
        baseCurrency: base,
        secondaryCurrency: secondary,
      });
      onSaved();
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        return;
      }
      setProblem(
        error instanceof ApiError ? error.detail : "The household could not be saved. Try again.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="household-card" aria-labelledby="household-title">
      <div>
        <p className="section-label">Household</p>
        <h2 id="household-title">Name and reporting pair</h2>
      </div>

      <label className="text-field">
        <span>Household name</span>
        <input onChange={(e) => setName(e.target.value)} required value={name} />
      </label>
      <label className="text-field">
        <span>Your display name</span>
        <input onChange={(e) => setDisplayName(e.target.value)} required value={displayName} />
      </label>
      <label className="text-field">
        <span>Tax residency</span>
        <input
          maxLength={2}
          onChange={(e) => setResidency(e.target.value.toUpperCase())}
          placeholder="ISO 3166-1 alpha-2"
          value={residency}
        />
      </label>

      {(
        [
          ["Base currency", base, setBase],
          ["Secondary currency", secondary, setSecondary],
        ] as const
      ).map(([label, value, set]) => (
        <label className="select-field" key={label}>
          <span>{label}</span>
          <select disabled={locked} onChange={(e) => set(e.target.value)} value={value}>
            {currencies.map((option) => (
              <option key={option.code} value={option.code}>
                {option.code} — {option.name}
              </option>
            ))}
          </select>
        </label>
      ))}

      {locked && <p className="inline-note">{LOCKED_EXPLANATION}</p>}
      {!locked && (
        <p className="inline-note">
          Choose the pair before the first import. Afterwards it is a backfill, not a setting.
        </p>
      )}
      {problem && (
        <p className="inline-alert" role="alert">
          {problem}
        </p>
      )}

      <button className="primary-action" disabled={saving} onClick={submit} type="button">
        {saving ? "Saving…" : "Save household"}
      </button>
    </section>
  );
}
