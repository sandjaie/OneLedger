"use client";

import { useEffect, useRef, useState } from "react";

import { CategoriesManager } from "@/app/settings/categories-manager";
import { DataQuality } from "@/app/settings/data-quality";
import { HouseholdForm } from "@/app/settings/household-form";
import { MerchantKnowledge } from "@/app/settings/merchant-knowledge";
import { RulesManager } from "@/app/settings/rules-manager";
import { SectionNav } from "@/app/ui/section-nav";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { ApiError, type apiFetch, errorText, LONG_REQUEST_TIMEOUT_MS } from "@/lib/api/client";
import type { CategoryRow, CurrencyRow, FxRateResponse, HouseholdResponse, HouseholdUpdate } from "@/types/api";

type FxState =
  | { status: "idle" }
  | { status: "loading"; requestedDate: string }
  | { status: "ready"; requestedDate: string; rates: Array<FxRateResponse> }
  | { status: "empty"; requestedDate: string }
  | { status: "service"; requestedDate: string };

type BackfillState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; written: number }
  | { status: "error"; message: string };

const SECTIONS = [
  { id: "household-title", label: "General" },
  { id: "data-quality-title", label: "Data health" },
  { id: "categories-title", label: "Categories" },
  { id: "rules-title", label: "Rules" },
  { id: "fx-title", label: "Exchange rates" },
];

const TARGET_SECTIONS: Record<string, string> = {
  "household-title": "household-title",
  "data-quality-title": "data-quality-title",
  "categories-title": "categories-title",
  "rules-title": "rules-title",
  "rules-analytics-title": "rules-title",
  "rules-suggestions-title": "rules-title",
  "merchant-knowledge-title": "rules-title",
  "fx-title": "fx-title",
  "fx-backfill-title": "fx-title",
};

type Selection = { sectionId: string; targetId?: string };

function selectionFromLocation(): Selection {
  if (typeof window === "undefined") return { sectionId: "household-title" };
  const targetId = window.location.hash.slice(1);
  const sectionId = TARGET_SECTIONS[targetId];
  return sectionId ? { sectionId, targetId } : { sectionId: "household-title" };
}

function fxStatusText(fx: FxState) {
  if (fx.status === "loading") return `Checking stored FX rates for ${fx.requestedDate}.`;
  if (fx.status === "empty") return `No FX rates are stored for ${fx.requestedDate}.`;
  if (fx.status === "ready") return `Loaded ${fx.rates.length} FX ${fx.rates.length === 1 ? "rate" : "rates"} for ${fx.requestedDate}.`;
  return "";
}

type Props = {
  household: HouseholdResponse;
  currencies?: Array<CurrencyRow>;
  categories?: Array<CategoryRow>;
  request: typeof apiFetch;
  onChanged: () => void;
  signingOut: boolean;
  logoutFailed: boolean;
  fxEpoch: number;
};

export function SettingsScreen({ household, currencies, categories, request, onChanged, signingOut, logoutFailed, fxEpoch }: Props) {
  const [selection, setSelection] = useState<Selection>(selectionFromLocation);
  const [healthWarnings, setHealthWarnings] = useState(0);
  const [fxDate, setFxDate] = useState("");
  const [fx, setFx] = useState<FxState>({ status: "idle" });
  const fxRequest = useRef<{ id: number; controller: AbortController } | undefined>(undefined);
  const lastFxEpoch = useRef(fxEpoch);
  const [backfillStart, setBackfillStart] = useState("");
  const [backfillEnd, setBackfillEnd] = useState("");
  const [backfill, setBackfill] = useState<BackfillState>({ status: "idle" });

  useEffect(() => {
    const sync = () => setSelection(selectionFromLocation());
    window.addEventListener("popstate", sync);
    window.addEventListener("hashchange", sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener("hashchange", sync);
    };
  }, []);

  useEffect(() => {
    if (!selection.targetId) return;
    const target = document.getElementById(selection.targetId);
    target?.focus();
    target?.scrollIntoView?.();
  }, [selection]);

  useEffect(() => () => fxRequest.current?.controller.abort(), []);

  useEffect(() => {
    if (lastFxEpoch.current === fxEpoch) return;
    lastFxEpoch.current = fxEpoch;
    const id = (fxRequest.current?.id ?? 0) + 1;
    fxRequest.current?.controller.abort();
    fxRequest.current = { id, controller: new AbortController() };
    setFx({ status: "idle" });
  }, [fxEpoch]);

  function selectSection(id: string) {
    window.history.pushState(null, "", `#${id}`);
    setSelection({ sectionId: id, targetId: id });
  }

  async function loadFx() {
    const requestedDate = fxDate;
    if (!requestedDate || signingOut) return;
    fxRequest.current?.controller.abort();
    const controller = new AbortController();
    const id = (fxRequest.current?.id ?? 0) + 1;
    fxRequest.current = { id, controller };
    setFx({ status: "loading", requestedDate });
    try {
      const rates = await request<Array<FxRateResponse>>(`/fx/${encodeURIComponent(requestedDate)}`, {
        signal: controller.signal,
      });
      if (fxRequest.current?.id !== id) return;
      setFx(rates.length ? { status: "ready", requestedDate, rates } : { status: "empty", requestedDate });
    } catch (error) {
      if (fxRequest.current?.id !== id || (error instanceof ApiError && (error.status === 401 || error.status === 403))) return;
      setFx({ status: "service", requestedDate });
    }
  }

  async function runBackfill() {
    if (!backfillStart || !backfillEnd || signingOut) return;
    setBackfill({ status: "loading" });
    try {
      const result = await request<{ written: number }>(
        `/fx/backfill?start=${encodeURIComponent(backfillStart)}&end=${encodeURIComponent(backfillEnd)}`,
        { method: "POST", timeoutMs: LONG_REQUEST_TIMEOUT_MS },
      );
      setBackfill({ status: "ready", written: result.written });
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) return;
      setBackfill({ status: "error", message: errorText(error, "Rates could not be backfilled.") });
    }
  }

  return (
    <section className="workspace-content settings-screen" aria-labelledby="settings-title">
      <header className="page-head settings-page-head">
        <p className="eyebrow">Owner settings</p>
        <h1 className="workspace-title" id="settings-title">Settings</h1>
      </header>

      {logoutFailed && <Alert variant="destructive"><AlertDescription>OneLedger could not confirm that the server session was revoked. Your workspace remains open; retry sign out.</AlertDescription></Alert>}
      {healthWarnings > 0 && (
        <Alert>
          <AlertTitle>{healthWarnings} data {healthWarnings === 1 ? "finding needs" : "findings need"} attention</AlertTitle>
          <AlertDescription><a href="#data-quality-title" onClick={(event) => { event.preventDefault(); selectSection("data-quality-title"); }}>Review data health</a></AlertDescription>
        </Alert>
      )}

      <SectionNav activeId={selection.sectionId} items={SECTIONS} onSelect={selectSection} />

      <div className="settings-panel" hidden={selection.sectionId !== "household-title"}>
        {currencies && (
          <HouseholdForm
            currencies={currencies}
            household={household}
            onSaved={onChanged}
            save={(body: HouseholdUpdate) => request<void>("/household", {
              method: "PATCH",
              body: JSON.stringify(body),
              headers: { "Content-Type": "application/json" },
            })}
          />
        )}
      </div>

      <div className="settings-panel" hidden={selection.sectionId !== "data-quality-title"}>
        <DataQuality onWarningCount={setHealthWarnings} request={request} />
      </div>

      <div className="settings-panel" hidden={selection.sectionId !== "categories-title"}>
        {categories && <CategoriesManager categories={categories} onChanged={onChanged} request={request} />}
      </div>

      <div className="settings-panel" hidden={selection.sectionId !== "rules-title"}>
        {categories && <RulesManager categories={categories} onChanged={onChanged} request={request} />}
        {categories && <MerchantKnowledge categories={categories} request={request} />}
      </div>

      <div className="settings-panel settings-fx-panel" hidden={selection.sectionId !== "fx-title"}>
        <section aria-labelledby="fx-title">
          <Card>
            <CardHeader><CardTitle><h2 id="fx-title" tabIndex={-1}>Stored rate</h2></CardTitle></CardHeader>
            <CardContent>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="settings-fx-date">FX date</FieldLabel>
                  <Input id="settings-fx-date" onChange={(event) => setFxDate(event.target.value)} required type="date" value={fxDate} />
                </Field>
                <Button disabled={!fxDate || signingOut} onClick={() => void loadFx()} type="button">
                  {fx.status === "service" ? "Retry FX rate" : "Check FX rate"}
                </Button>
              </FieldGroup>
              <p className="fx-empty" role="status" aria-atomic="true">{fxStatusText(fx)}</p>
              {fx.status === "service" && <Alert variant="destructive"><AlertDescription>FX rates could not be loaded. Check the API service and retry.</AlertDescription></Alert>}
              {fx.status === "ready" && (
                <div className="fx-results">
                  {fx.rates.map((item) => (
                    <article className="fx-result" key={`${item.base}-${item.quote}`}>
                      <div><span className="section-label">{item.base}/{item.quote}</span><strong>{item.rate}</strong></div>
                      <dl>
                        <div><dt>Source</dt><dd>{item.source}</dd></div>
                        <div><dt>Carry</dt><dd>{item.carried ? `Carried from ${item.carried_from}` : "Direct rate"}</dd></div>
                        <div><dt>As of</dt><dd>As of {fx.requestedDate}</dd></div>
                      </dl>
                    </article>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </section>

        <section aria-labelledby="fx-backfill-title">
          <Card>
            <CardHeader><CardTitle><h2 id="fx-backfill-title" tabIndex={-1}>Backfill rates</h2></CardTitle></CardHeader>
            <CardContent>
              <FieldGroup>
                <Field>
                  <FieldLabel htmlFor="settings-backfill-start">Start date</FieldLabel>
                  <Input id="settings-backfill-start" onChange={(event) => setBackfillStart(event.target.value)} required type="date" value={backfillStart} />
                </Field>
                <Field>
                  <FieldLabel htmlFor="settings-backfill-end">End date</FieldLabel>
                  <Input id="settings-backfill-end" onChange={(event) => setBackfillEnd(event.target.value)} required type="date" value={backfillEnd} />
                </Field>
                <Button disabled={!backfillStart || !backfillEnd || signingOut || backfill.status === "loading"} onClick={() => void runBackfill()} type="button">
                  {backfill.status === "loading" ? "Backfilling…" : "Backfill rates"}
                </Button>
              </FieldGroup>
              {backfill.status === "ready" && <p className="fx-empty" role="status" aria-atomic="true">Wrote {backfill.written} {backfill.written === 1 ? "rate" : "rates"}.</p>}
              {backfill.status === "error" && <Alert variant="destructive"><AlertDescription>{backfill.message}</AlertDescription></Alert>}
            </CardContent>
          </Card>
        </section>
      </div>
    </section>
  );
}
