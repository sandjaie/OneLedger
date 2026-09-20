"use client";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";

import { useEffect, useState } from "react";

import { type apiFetch, errorText } from "@/lib/api/client";
import { moneyText } from "@/lib/money/format";
import { accountValueReasonText, reasonsText } from "@/lib/plans/account-value-reasons";
import type {
  AccountRow,
  CurrencyRow,
  EligibleAccountRow,
  EmergencyReserveResponse,
  ReservationRow,
} from "@/types/api";

type Props = {
  accounts: Array<AccountRow>;
  currencies: Array<CurrencyRow>;
  request: typeof apiFetch;
};

type Bucket = "need" | "want" | "investment" | "debt_service";
const BUCKETS: Array<{ value: Bucket; label: string }> = [
  { value: "need", label: "Need" },
  { value: "want", label: "Want" },
  { value: "investment", label: "Investment" },
  { value: "debt_service", label: "Debt service" },
];

type SettingsFormState = {
  targetMonths: string;
  baselineMethod: "reference_period" | "manual";
  // A plain count, not money — held as a number and read from the native `valueAsNumber`, never
  // `Number()`/`parseInt()` (banned in this codebase; money is a string on the wire and stays
  // one, and this field follows the same no-string-to-number-conversion rule for consistency).
  referenceMonths: number;
  buckets: Array<Bucket>;
  manualMonthlyBase: string;
};

type EligibilityFormState = {
  accessDays: number;
  restrictionNote: string;
};

// Server sends short reason codes; the web owns only the sentence (goals.tsx's own convention).
const COVERAGE_REASON_TEXT: Record<string, string> = {
  not_configured: "the emergency reserve has not been set up yet",
  baseline_not_enough_information: "spending is not yet known well enough to estimate a baseline",
  missing_rate: "an exchange rate is unavailable for one of the eligible accounts",
};

function reasonText(reasons: Array<string>): string {
  return reasonsText(reasons, COVERAGE_REASON_TEXT);
}

function settingsFormFrom(data: EmergencyReserveResponse): SettingsFormState {
  const settings = data.settings;
  return {
    targetMonths: settings?.targetMonths ?? "6.0",
    baselineMethod: (settings?.baselineMethod as "reference_period" | "manual") ?? "reference_period",
    referenceMonths: settings?.referenceMonths ?? 6,
    buckets: (settings?.buckets as Array<Bucket> | undefined) ?? ["need", "debt_service"],
    manualMonthlyBase: settings?.manualMonthlyBase ?? "",
  };
}

const EMPTY_SETTINGS_FORM: SettingsFormState = {
  targetMonths: "6.0",
  baselineMethod: "reference_period",
  referenceMonths: 6,
  buckets: ["need", "debt_service"],
  manualMonthlyBase: "",
};

export function EmergencyReserve({ accounts, currencies, request }: Props) {
  const activeAssetAccounts = accounts.filter(
    (account) => account.isActive && account.accountClass === "asset",
  );
  const [data, setData] = useState<EmergencyReserveResponse>();
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);

  const [editingSettings, setEditingSettings] = useState(false);
  const [settingsForm, setSettingsForm] = useState<SettingsFormState>(EMPTY_SETTINGS_FORM);
  const [settingsBusy, setSettingsBusy] = useState(false);
  const [settingsProblem, setSettingsProblem] = useState<string>();

  const [addingAccountId, setAddingAccountId] = useState<string>();
  const [eligibilityForm, setEligibilityForm] = useState<EligibilityFormState>({
    accessDays: 0, restrictionNote: "",
  });
  const [eligibilityBusy, setEligibilityBusy] = useState(false);
  const [eligibilityProblem, setEligibilityProblem] = useState<string>();

  const [reservingAccountId, setReservingAccountId] = useState<string>();
  const [reserveAmount, setReserveAmount] = useState("");
  const [reserveKey, setReserveKey] = useState(() => crypto.randomUUID());
  const [reserveBusy, setReserveBusy] = useState(false);
  const [reserveProblem, setReserveProblem] = useState<string>();

  const [rowProblem, setRowProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    request<EmergencyReserveResponse>("/emergency-reserve")
      .then((result) => {
        if (!current) return;
        setData(result);
        if (!editingSettings) setSettingsForm(settingsFormFrom(result));
        setState("ready");
      })
      .catch(() => {
        if (current) setState("error");
      });
    return () => {
      current = false;
    };
    // editingSettings is read only to seed the form on load, not to retrigger the fetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attempt, request]);

  function toggleBucket(bucket: Bucket) {
    setSettingsForm((current) => ({
      ...current,
      buckets: current.buckets.includes(bucket)
        ? current.buckets.filter((value) => value !== bucket)
        : [...current.buckets, bucket],
    }));
  }

  function startEditSettings() {
    if (data) setSettingsForm(settingsFormFrom(data));
    setEditingSettings(true);
    setSettingsProblem(undefined);
  }

  async function saveSettings() {
    setSettingsBusy(true);
    setSettingsProblem(undefined);
    try {
      await request("/emergency-reserve", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          targetMonths: settingsForm.targetMonths,
          baselineMethod: settingsForm.baselineMethod,
          referenceMonths: settingsForm.referenceMonths,
          buckets: settingsForm.buckets,
          manualMonthlyBase:
            settingsForm.baselineMethod === "manual" ? settingsForm.manualMonthlyBase : null,
        }),
      });
      setEditingSettings(false);
      setAttempt((value) => value + 1);
    } catch (error) {
      setSettingsProblem(errorText(error, "These settings could not be saved."));
    } finally {
      setSettingsBusy(false);
    }
  }

  function startAddEligible() {
    setAddingAccountId(activeAssetAccounts.find((account) => !eligibleIds.has(account.id))?.id);
    setEligibilityForm({ accessDays: 0, restrictionNote: "" });
    setEligibilityProblem(undefined);
  }

  function startEditEligible(row: EligibleAccountRow) {
    setAddingAccountId(row.accountId);
    setEligibilityForm({
      accessDays: row.accessDays, restrictionNote: row.restrictionNote ?? "",
    });
    setEligibilityProblem(undefined);
  }

  async function saveEligibility() {
    if (!addingAccountId) return;
    setEligibilityBusy(true);
    setEligibilityProblem(undefined);
    try {
      await request(`/emergency-reserve/accounts/${addingAccountId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          accessDays: eligibilityForm.accessDays,
          restrictionNote: eligibilityForm.restrictionNote || null,
        }),
      });
      setAddingAccountId(undefined);
      setAttempt((value) => value + 1);
    } catch (error) {
      setEligibilityProblem(errorText(error, "This account could not be marked eligible."));
    } finally {
      setEligibilityBusy(false);
    }
  }

  async function removeEligible(accountId: string) {
    setRowProblem(undefined);
    try {
      await request(`/emergency-reserve/accounts/${accountId}`, { method: "DELETE" });
      setAttempt((value) => value + 1);
    } catch (error) {
      setRowProblem(errorText(error, "This account could not be removed."));
    }
  }

  function startReserve(accountId: string) {
    setReservingAccountId(accountId);
    setReserveAmount("");
    setReserveKey(crypto.randomUUID());
    setReserveProblem(undefined);
  }

  async function submitReserve() {
    if (!reservingAccountId) return;
    setReserveBusy(true);
    setReserveProblem(undefined);
    try {
      await request("/reservations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          purpose: "emergency",
          goalId: null,
          accountId: reservingAccountId,
          kind: "amount",
          amount: reserveAmount,
          idempotencyKey: reserveKey,
        }),
      });
      setReservingAccountId(undefined);
      setReserveKey(crypto.randomUUID());
      setAttempt((value) => value + 1);
    } catch (error) {
      setReserveProblem(errorText(error, "This reservation could not be made."));
    } finally {
      setReserveBusy(false);
    }
  }

  async function release(reservation: ReservationRow) {
    setRowProblem(undefined);
    try {
      await request(`/reservations/${reservation.id}/release`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      setAttempt((value) => value + 1);
    } catch (error) {
      setRowProblem(errorText(error, "This reservation could not be released."));
    }
  }

  const eligibleIds = new Set(data?.accounts.map((row) => row.accountId) ?? []);
  const nonEligibleActiveAssetAccounts = activeAssetAccounts.filter(
    (account) => !eligibleIds.has(account.id),
  );

  return (
    <section className="plans-reserve-section" aria-labelledby="reserve-title" id="reserve">
      <div className="console-section-head"><h2 id="reserve-title">Emergency reserve</h2></div>

      {state === "loading" && <p role="status">Loading the emergency reserve…</p>}
      {state === "error" && (
        <div role="alert">
          <p>The emergency reserve could not be loaded.</p>
          <Button variant="outline" onClick={() => setAttempt((value) => value + 1)} type="button">Try again</Button>
        </div>
      )}

      {state === "ready" && data && (
        <>
          {!data.configured && <p>The emergency reserve has not been set up yet.</p>}

          <h3>Settings</h3>
          {!editingSettings ? (
            <>
              {data.settings && (
                <p>
                  Target {data.settings.targetMonths} months · Baseline{" "}
                  {data.settings.baselineMethod === "manual"
                    ? "manual"
                    : `${data.settings.referenceMonths}-month reference period over ${data.settings.buckets.join(", ")}`}
                </p>
              )}
              <Button variant="outline" onClick={startEditSettings} type="button">
                {data.settings ? "Edit settings" : "Set up the reserve"}
              </Button>
            </>
          ) : (
            <div className="plans-reserve-form">
              <label className="text-field">
                <span>Target months</span>
                <Input
                  inputMode="decimal"
                  onChange={(e) =>
                    setSettingsForm((current) => ({ ...current, targetMonths: e.target.value }))
                  }
                  value={settingsForm.targetMonths}
                />
              </label>
              <label className="select-field">
                <span>Baseline method</span>
                <NativeSelect
                  onChange={(e) =>
                    setSettingsForm((current) => ({
                      ...current,
                      baselineMethod: e.target.value as SettingsFormState["baselineMethod"],
                    }))
                  }
                  value={settingsForm.baselineMethod}
                >
                  <option value="reference_period">Reference period</option>
                  <option value="manual">Manual amount</option>
                </NativeSelect>
              </label>
              {settingsForm.baselineMethod === "reference_period" ? (
                <>
                  <label className="text-field">
                    <span>Reference months</span>
                    <Input
                      onChange={(e) =>
                        setSettingsForm((current) => ({
                          ...current, referenceMonths: e.target.valueAsNumber || 0,
                        }))
                      }
                      type="number"
                      value={settingsForm.referenceMonths}
                    />
                  </label>
                  <fieldset>
                    <legend>Buckets</legend>
                    {BUCKETS.map((bucket) => (
                      <label className="flex items-center gap-2" key={bucket.value}>
                        <Checkbox
                          checked={settingsForm.buckets.includes(bucket.value)}
                          onCheckedChange={() => toggleBucket(bucket.value)}
                        />
                        {bucket.label}
                      </label>
                    ))}
                  </fieldset>
                </>
              ) : (
                <label className="text-field">
                  <span>Manual monthly amount</span>
                  <Input
                    inputMode="decimal"
                    onChange={(e) =>
                      setSettingsForm((current) => ({
                        ...current, manualMonthlyBase: e.target.value,
                      }))
                    }
                    value={settingsForm.manualMonthlyBase}
                  />
                </label>
              )}
              {settingsProblem && <p className="inline-alert" role="alert">{settingsProblem}</p>}
              <Button
                variant="default"
                disabled={settingsBusy}
                onClick={() => void saveSettings()}
                type="button"
              >{settingsBusy ? "Saving…" : "Save settings"}</Button>
              <Button
                variant="outline"
                onClick={() => setEditingSettings(false)}
                type="button"
              >Cancel</Button>
            </div>
          )}

          {data.baseline && (
            <p>
              Baseline{" "}
              {data.baseline.status === "not_enough_information"
                ? "not enough information"
                : moneyText(data.baseline.monthlyBase ?? "0", data.baseCurrency, currencies)}
              {data.baseline.method === "reference_period" && data.baseline.referenceStart && (
                <> · {data.baseline.referenceStart} to {data.baseline.referenceEnd}</>
              )}
              {data.baseline.uncategorisedBase && (
                <>
                  {" "}(excludes {moneyText(data.baseline.uncategorisedBase, data.baseCurrency, currencies)}{" "}
                  uncategorised)
                </>
              )}
            </p>
          )}

          <h3>Coverage</h3>
          {data.coverageStatus !== "available" ? (
            <p>
              {data.coverageStatus === "not_enough_information"
                ? "Coverage: not enough information"
                : "Coverage unavailable"}
              {data.reasons.length > 0 && <> — {reasonText(data.reasons)}</>}
            </p>
          ) : (
            <>
              <p>
                {data.coverageMonths} months of coverage · Target{" "}
                {moneyText(data.targetBase ?? "0", data.baseCurrency, currencies)}
                {" · Gap "}
                {moneyText(data.gapBase ?? "0", data.baseCurrency, currencies)}
                {" · Reserved "}
                {data.reservedMonths} months
              </p>
              {data.currencies.length > 0 && (
                <ul>
                  {data.currencies.map((row) => (
                    <li key={row.currency}>
                      {moneyText(row.eligibleNative, row.currency, currencies)}
                      {row.rate ? <> · rate {row.rate} as of {row.rateDate}</> : <> · rate unavailable</>}
                    </li>
                  ))}
                </ul>
              )}
              <p>
                A resource reserved for a goal is not counted; a commitment due within 30 days is
                shown but not subtracted; coverage uses current rates; a reservation moves no
                money.
              </p>
              {data.coverageGapMonths > 0 && (
                <p>
                  {data.coverageGapMonths} month(s) of the reference period have missing or
                  partial statement coverage, so the baseline may be understated.
                </p>
              )}
            </>
          )}

          {rowProblem && <p className="inline-alert" role="alert">{rowProblem}</p>}

          <h3>Eligible accounts</h3>
          {data.accounts.length === 0 && <p>No accounts are marked as emergency resources.</p>}
          {data.accounts.map((row) => (
            <div key={row.accountId} className="plans-reserve-account">
              <p>
                {row.accountName} ·{" "}
                {row.value
                  ? moneyText(row.value, row.currency, currencies)
                  : `Unavailable (${accountValueReasonText(row.reason ?? "")})`}
                {" · Reserved for goals "}
                {moneyText(row.goalReserved, row.currency, currencies)}
                {" · Reserved for emergencies "}
                {moneyText(row.emergencyReserved, row.currency, currencies)}
                {" · Eligible "}
                {row.eligible ? moneyText(row.eligible, row.currency, currencies) : "—"}
                {" · Access "}
                {row.accessDays} day(s)
                {row.restrictionNote && <> · {row.restrictionNote}</>}
              </p>
              {row.upcomingCommitments.length > 0 && (
                <ul>
                  {row.upcomingCommitments.map((commitment) => (
                    <li key={commitment.commitmentId}>
                      {commitment.label} · due {commitment.dueDate}
                      {commitment.remainingNext && (
                        <> · {moneyText(commitment.remainingNext, row.currency, currencies)}</>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              <Button variant="outline" onClick={() => startEditEligible(row)} type="button">
                Edit
              </Button>
              <Button
                variant="outline"
                onClick={() => void removeEligible(row.accountId)}
                type="button"
              >Remove</Button>
              <Button variant="outline" onClick={() => startReserve(row.accountId)} type="button">
                Reserve for emergencies
              </Button>

              {reservingAccountId === row.accountId && (
                <div className="plans-reserve-form">
                  <label className="text-field">
                    <span>Amount ({row.currency})</span>
                    <Input
                      inputMode="decimal"
                      onChange={(e) => setReserveAmount(e.target.value)}
                      value={reserveAmount}
                    />
                  </label>
                  {reserveProblem && <p className="inline-alert" role="alert">{reserveProblem}</p>}
                  <Button
                    variant="default"
                    disabled={!reserveAmount || reserveBusy}
                    onClick={() => void submitReserve()}
                    type="button"
                  >{reserveBusy ? "Reserving…" : "Reserve"}</Button>
                  <Button
                    variant="outline"
                    onClick={() => setReservingAccountId(undefined)}
                    type="button"
                  >Cancel</Button>
                </div>
              )}
            </div>
          ))}

          {data.reservations.length > 0 && (
            <ul>
              {data.reservations.map((reservation) => (
                <li key={reservation.id}>
                  {reservation.accountName} · Reserved {reservation.reservedOn} ·{" "}
                  {moneyText(reservation.nativeAmount, reservation.currency, currencies)}
                  {reservation.releasedAt ? (
                    <small>
                      {" · Released"}
                      {reservation.releaseReason && ` (${reservation.releaseReason})`}
                    </small>
                  ) : (
                    <Button
                      variant="outline"
                      onClick={() => void release(reservation)}
                      type="button"
                    >Release</Button>
                  )}
                </li>
              ))}
            </ul>
          )}

          <h3>Mark as emergency resource</h3>
          {nonEligibleActiveAssetAccounts.length === 0 && !addingAccountId && (
            <p>Every active asset account is already marked.</p>
          )}
          {!addingAccountId && nonEligibleActiveAssetAccounts.length > 0 && (
            <Button variant="outline" onClick={startAddEligible} type="button">
              Mark an account eligible
            </Button>
          )}
          {addingAccountId && (
            <div className="plans-reserve-form">
              <label className="select-field">
                <span>Account</span>
                <NativeSelect
                  onChange={(e) => setAddingAccountId(e.target.value)}
                  value={addingAccountId}
                >
                  {activeAssetAccounts
                    .filter((account) => !eligibleIds.has(account.id) || account.id === addingAccountId)
                    .map((account) => (
                      <option key={account.id} value={account.id}>{account.name}</option>
                    ))}
                </NativeSelect>
              </label>
              <label className="text-field">
                <span>Access days</span>
                <Input
                  onChange={(e) =>
                    setEligibilityForm((current) => ({
                      ...current, accessDays: e.target.valueAsNumber || 0,
                    }))
                  }
                  type="number"
                  value={eligibilityForm.accessDays}
                />
              </label>
              <label className="text-field">
                <span>Restriction note (optional)</span>
                <Input
                  onChange={(e) =>
                    setEligibilityForm((current) => ({
                      ...current, restrictionNote: e.target.value,
                    }))
                  }
                  value={eligibilityForm.restrictionNote}
                />
              </label>
              {eligibilityProblem && <p className="inline-alert" role="alert">{eligibilityProblem}</p>}
              <Button
                variant="default"
                disabled={eligibilityBusy}
                onClick={() => void saveEligibility()}
                type="button"
              >{eligibilityBusy ? "Saving…" : "Save"}</Button>
              <Button
                variant="outline"
                onClick={() => setAddingAccountId(undefined)}
                type="button"
              >Cancel</Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
