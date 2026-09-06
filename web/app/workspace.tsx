"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";

import { ApiError, apiFetch } from "@/lib/api/client";
import { formatMoney } from "@/lib/money/format";
import type { FxRateResponse, HouseholdCurrency, HouseholdResponse } from "@/types/api";

const PREFERENCE_KEY = "oneledger.reportingCurrency";
const PREVIEW_VALUE = "1234567.89";

type Props = {
  section?: "overview" | "settings";
  request?: typeof apiFetch;
  navigate?: (path: string) => void;
};

type FxState =
  | { status: "idle" }
  | { status: "loading"; requestedDate: string }
  | { status: "ready"; requestedDate: string; rates: Array<FxRateResponse> }
  | { status: "empty"; requestedDate: string }
  | { status: "service"; requestedDate: string };

function authDestination(error: unknown) {
  if (!(error instanceof ApiError)) return undefined;
  if (error.status === 401) return "/login";
  if (error.status === 403) return "/unauthorized";
}

function configuredCurrency(household: HouseholdResponse): HouseholdCurrency {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(PREFERENCE_KEY);
  } catch {}
  return (
    household.currencies.find(({ code }) => code === stored) ??
    household.currencies.find(({ code }) => code === household.baseCurrency) ??
    household.currencies[0]
  );
}

function sectionFromPath() {
  if (typeof window === "undefined") return "overview";
  return window.location.pathname === "/settings" ? "settings" : "overview";
}

function fxStatusText(fx: FxState) {
  if (fx.status === "loading") return `Checking stored FX rates for ${fx.requestedDate}.`;
  if (fx.status === "empty") return `No FX rates are stored for ${fx.requestedDate}.`;
  if (fx.status === "ready") {
    return `Loaded ${fx.rates.length} FX ${fx.rates.length === 1 ? "rate" : "rates"} for ${fx.requestedDate}.`;
  }
  return "";
}

export function Workspace({
  section,
  request = apiFetch,
  navigate = (path) => window.location.replace(path),
}: Props = {}) {
  const activeSection = section ?? sectionFromPath();
  const [household, setHousehold] = useState<HouseholdResponse>();
  const [currency, setCurrency] = useState<HouseholdCurrency>();
  const [state, setState] = useState<"loading" | "ready" | "service" | "leaving">("loading");
  const [attempt, setAttempt] = useState(0);
  const [destination, setDestination] = useState<string>();
  const [signingOut, setSigningOut] = useState(false);
  const [logoutFailed, setLogoutFailed] = useState(false);
  const [fxDate, setFxDate] = useState("");
  const [fx, setFx] = useState<FxState>({ status: "idle" });
  const fxRequest = useRef<{ id: number; controller: AbortController } | undefined>(undefined);

  useEffect(() => {
    let current = true;
    request<HouseholdResponse>("/household")
      .then((configuration) => {
        if (!current) return;
        setHousehold(configuration);
        setCurrency(configuredCurrency(configuration));
        setState("ready");
      })
      .catch((error: unknown) => {
        if (!current) return;
        const next = authDestination(error);
        if (next) setDestination(next);
        else setState("service");
      });
    return () => {
      current = false;
    };
  }, [attempt, request]);

  useEffect(() => () => fxRequest.current?.controller.abort(), []);

  useEffect(() => {
    if (destination) navigate(destination);
  }, [destination, navigate]);

  function retireFxRequest() {
    const id = (fxRequest.current?.id ?? 0) + 1;
    fxRequest.current?.controller.abort();
    fxRequest.current = { id, controller: new AbortController() };
    setFx({ status: "idle" });
  }

  async function logout() {
    setSigningOut(true);
    setLogoutFailed(false);
    retireFxRequest();
    try {
      await request<void>("/auth/logout", { method: "POST" });
      setHousehold(undefined);
      setCurrency(undefined);
      setState("leaving");
      setDestination("/login");
    } catch (error) {
      const next = authDestination(error);
      if (next) {
        setHousehold(undefined);
        setCurrency(undefined);
        setState("leaving");
        setDestination(next);
      } else {
        setLogoutFailed(true);
      }
    } finally {
      setSigningOut(false);
    }
  }

  function selectCurrency(next: HouseholdCurrency) {
    setCurrency(next);
    try {
      localStorage.setItem(PREFERENCE_KEY, next.code);
    } catch {}
  }

  function leaveForAuth(path: string) {
    fxRequest.current?.controller.abort();
    setHousehold(undefined);
    setCurrency(undefined);
    setState("leaving");
    setDestination(path);
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
      if (fxRequest.current?.id !== id) return;
      const next = authDestination(error);
      if (next) leaveForAuth(next);
      else setFx({ status: "service", requestedDate });
    }
  }

  return (
    <div className="site-shell">
      <header className="masthead">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">OL</span>
          <span>OneLedger</span>
        </div>
        {state === "ready" && (
          <button className="text-action" disabled={signingOut} onClick={logout} type="button">
            {signingOut ? "Signing out…" : logoutFailed ? "Retry sign out" : "Sign out"}
          </button>
        )}
      </header>

      {state === "loading" && <main className="state-panel"><p>Checking owner access…</p></main>}
      {state === "leaving" && <main className="state-panel"><p>Closing owner workspace…</p></main>}
      {state === "service" && (
        <main className="state-panel">
          <p className="eyebrow">Connection problem</p>
          <h1 className="state-title">OneLedger is unavailable</h1>
          <p className="lede">The owner workspace could not be loaded. Check the API service and try again.</p>
          <button className="primary-action" onClick={() => { setState("loading"); setAttempt((value) => value + 1); }} type="button">Try again</button>
        </main>
      )}
      {state === "ready" && household && currency && (
        <main className="workspace-grid">
          <nav className="workspace-nav" aria-label="Workspace">
            <Link aria-current={activeSection === "overview" ? "page" : undefined} href="/">Overview</Link>
            {[
              ["Activity", "Ledger import arrives later"],
              ["Plans", "Available after ledger import"],
            ].map(([label, note]) => (
              <button aria-label={`${label} unavailable — ${note}`} disabled key={label} type="button">
                <span>{label}</span><small>{note}</small>
              </button>
            ))}
            <Link aria-current={activeSection === "settings" ? "page" : undefined} href="/settings">Settings</Link>
          </nav>

          {activeSection === "overview" && (
            <section className="workspace-content" aria-labelledby="overview-title">
              <p className="eyebrow">Phase 0 overview</p>
              <h1 className="workspace-title" id="overview-title">Your reporting workspace is ready.</h1>
              <p className="lede">Ledger import and financial views arrive in a later phase. This preview confirms how configured currencies will be displayed.</p>
              {logoutFailed && <p className="inline-alert" role="alert">OneLedger could not confirm that the server session was revoked. Your workspace remains open; retry sign out.</p>}

              <section className="currency-card" aria-labelledby="currency-title">
                <div>
                  <p className="section-label">Display preference</p>
                  <h2 id="currency-title">Reporting currency</h2>
                </div>
                <div className="currency-toggle" role="group" aria-label="Reporting currency">
                  {household.currencies.map((option) => (
                    <button
                      aria-pressed={currency.code === option.code}
                      key={option.code}
                      onClick={() => selectCurrency(option)}
                      type="button"
                    >{option.code}</button>
                  ))}
                </div>
                <div className="format-preview">
                  <span>Formatting preview · synthetic value</span>
                  <output aria-live="polite">{formatMoney(PREVIEW_VALUE, currency)}</output>
                  <small>{currency.code} · {currency.numberFormat} grouping · {currency.decimals} decimals</small>
                </div>
              </section>
            </section>
          )}

          {activeSection === "settings" && (
            <section className="workspace-content" aria-labelledby="settings-title">
              <p className="eyebrow">Owner settings</p>
              <h1 className="workspace-title" id="settings-title">Settings</h1>
              <p className="lede">Choose a date to inspect recorded exchange rates, their sources and whether a prior rate was carried forward.</p>
              {logoutFailed && <p className="inline-alert" role="alert">OneLedger could not confirm that the server session was revoked. Your workspace remains open; retry sign out.</p>}

              <section className="fx-card" aria-labelledby="fx-title">
                <div>
                  <p className="section-label">FX inspector</p>
                  <h2 id="fx-title">Stored rate</h2>
                </div>
                <label className="date-field">
                  <span>FX date</span>
                  <input
                    onChange={(event) => setFxDate(event.target.value)}
                    required
                    type="date"
                    value={fxDate}
                  />
                </label>
                <button className="primary-action" disabled={!fxDate || signingOut} onClick={loadFx} type="button">
                  {fx.status === "service" ? "Retry FX rate" : "Check FX rate"}
                </button>

                <p className="fx-empty" role="status" aria-atomic="true">{fxStatusText(fx)}</p>
                {fx.status === "service" && <p className="inline-alert" role="alert">FX rates could not be loaded. Check the API service and retry.</p>}
                {fx.status === "ready" && (
                  <div className="fx-results">
                    {fx.rates.map((item) => (
                      <article className="fx-result" key={`${item.base}-${item.quote}`}>
                        <div>
                          <span className="section-label">{item.base}/{item.quote}</span>
                          <strong>{item.rate}</strong>
                        </div>
                        <dl>
                          <div><dt>Source</dt><dd>{item.source}</dd></div>
                          <div><dt>Carry</dt><dd>{item.carried ? `Carried from ${item.carried_from}` : "Direct rate"}</dd></div>
                          <div><dt>As of</dt><dd>As of {fx.requestedDate}</dd></div>
                        </dl>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </section>
          )}
        </main>
      )}

      <footer className="footer"><p>Private owner workspace</p><p className="edition">Phase 0</p></footer>
    </div>
  );
}
