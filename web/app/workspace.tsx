"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { ApiError, apiFetch } from "@/lib/api/client";
import { formatMoney } from "@/lib/money/format";
import type { HouseholdCurrency, HouseholdResponse } from "@/types/api";

const PREFERENCE_KEY = "oneledger.reportingCurrency";
const PREVIEW_VALUE = "1234567.89";

type Props = {
  request?: typeof apiFetch;
  navigate?: (path: string) => void;
};

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

export function Workspace({
  request = apiFetch,
  navigate = (path) => window.location.replace(path),
}: Props = {}) {
  const [household, setHousehold] = useState<HouseholdResponse>();
  const [currency, setCurrency] = useState<HouseholdCurrency>();
  const [state, setState] = useState<"loading" | "ready" | "service" | "leaving">("loading");
  const [attempt, setAttempt] = useState(0);
  const [destination, setDestination] = useState<string>();
  const [signingOut, setSigningOut] = useState(false);
  const [logoutFailed, setLogoutFailed] = useState(false);

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

  useEffect(() => {
    if (destination) navigate(destination);
  }, [destination, navigate]);

  async function logout() {
    setSigningOut(true);
    setLogoutFailed(false);
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
            <Link aria-current="page" href="/">Overview</Link>
            {[
              ["Activity", "Ledger import arrives later"],
              ["Plans", "Available after ledger import"],
              ["Settings", "FX inspector pending"],
            ].map(([label, note]) => (
              <button aria-label={`${label} unavailable — ${note}`} disabled key={label} type="button">
                <span>{label}</span><small>{note}</small>
              </button>
            ))}
          </nav>

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
        </main>
      )}

      <footer className="footer"><p>Private owner workspace</p><p className="edition">Phase 0</p></footer>
    </div>
  );
}
