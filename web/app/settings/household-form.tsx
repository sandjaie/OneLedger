"use client";

import { useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { ApiError, errorText } from "@/lib/api/client";
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
        errorText(error, "The household could not be saved. Try again."),
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <section aria-labelledby="household-title" className="settings-block settings-general">
      <div className="settings-section-head">
        <div>
          <p className="section-label">General</p>
          <h2 id="household-title" tabIndex={-1}>Name and reporting pair</h2>
          <p>These details identify your household and set how reports display amounts.</p>
        </div>
      </div>
      <div className="settings-general-body">
          <FieldGroup className="settings-general-fields">
            <Field>
              <FieldLabel htmlFor="settings-household-name">Household name</FieldLabel>
              <Input id="settings-household-name" onChange={(event) => setName(event.target.value)} required value={name} />
            </Field>
            <Field>
              <FieldLabel htmlFor="settings-display-name">Your display name</FieldLabel>
              <Input id="settings-display-name" onChange={(event) => setDisplayName(event.target.value)} required value={displayName} />
            </Field>
            <Field>
              <FieldLabel htmlFor="settings-tax-residency">Tax residency</FieldLabel>
              <Input id="settings-tax-residency" maxLength={2} onChange={(event) => setResidency(event.target.value.toUpperCase())} placeholder="ISO 3166-1 alpha-2" value={residency} />
            </Field>
            {([
              ["Base currency", "settings-base-currency", base, setBase],
              ["Secondary currency", "settings-secondary-currency", secondary, setSecondary],
            ] as const).map(([label, id, value, set]) => (
              <Field data-disabled={locked} key={id}>
                <FieldLabel htmlFor={id}>{label}</FieldLabel>
                <NativeSelect disabled={locked} id={id} onChange={(event) => set(event.target.value)} value={value}>
                  {currencies.map((option) => (
                    <NativeSelectOption key={option.code} value={option.code}>{option.code} — {option.name}</NativeSelectOption>
                  ))}
                </NativeSelect>
              </Field>
            ))}
          </FieldGroup>
          {locked ? (
            <Collapsible className="settings-explanation">
              <CollapsibleTrigger>Why does changing the reporting pair require a backfill?</CollapsibleTrigger>
              <CollapsibleContent><p>{LOCKED_EXPLANATION}</p></CollapsibleContent>
            </Collapsible>
          ) : <p className="settings-explanation">Choose the pair before the first import. Afterwards changing it requires a backfill.</p>}
          {problem && <Alert variant="destructive"><AlertDescription>{problem}</AlertDescription></Alert>}
      </div>
      <div className="settings-save-row">
        <Button disabled={saving} onClick={() => void submit()} type="button">{saving ? "Saving…" : "Save household"}</Button>
      </div>
    </section>
  );
}
