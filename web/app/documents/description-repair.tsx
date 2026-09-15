"use client";

import { useState } from "react";

import { errorText, LONG_REQUEST_TIMEOUT_MS, type apiFetch } from "@/lib/api/client";
import type { DescriptionReparseResult, DocumentRow } from "@/types/api";

type Props = {
  document: DocumentRow;
  request: typeof apiFetch;
  onApplied: () => void;
};

// A completed statement import whose document carries `newerLayoutId`: preview the counts a
// newer layout would change, then apply. Nothing here bypasses `POST /import/{id}/
// reparse-descriptions` — the same guarded re-read the API route already proves.
export function DescriptionRepair({ document, request, onApplied }: Props) {
  const [password, setPassword] = useState("");
  const [preview, setPreview] = useState<DescriptionReparseResult>();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();

  async function run(apply: boolean) {
    setBusy(true);
    setProblem(undefined);
    try {
      const result = await request<DescriptionReparseResult>(
        `/import/${document.importId}/reparse-descriptions`,
        {
          method: "POST",
          body: JSON.stringify({
            layoutId: document.newerLayoutId,
            apply,
            password: document.isPasswordProtected ? password || null : null,
          }),
          headers: { "Content-Type": "application/json" },
          timeoutMs: LONG_REQUEST_TIMEOUT_MS,
        },
      );
      setPreview(result);
      if (apply) onApplied();
    } catch (error) {
      setProblem(errorText(error, "The re-read could not be completed. Check the password and retry."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="description-repair">
      <p className="section-label">A newer layout is available for this import.</p>
      {document.isPasswordProtected && (
        <label className="text-field">
          <span>Password</span>
          <input
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            value={password}
          />
        </label>
      )}
      {problem && (
        <p className="inline-alert" role="alert">{problem}</p>
      )}
      {preview && (
        <p className="lede">
          {preview.rowsCompared} rows compared · {preview.descriptionsChanged} descriptions would
          change
          {preview.applied ? ` · ${preview.rowsQueuedForReview} queued for review` : ""}.
        </p>
      )}
      <div className="account-form-actions">
        <button
          className="text-action"
          disabled={busy}
          onClick={() => void run(false)}
          type="button"
        >
          {busy ? "Checking…" : "Preview re-read"}
        </button>
        {preview && !preview.applied && (
          <button
            className="primary-action"
            disabled={busy}
            onClick={() => void run(true)}
            type="button"
          >
            Apply
          </button>
        )}
      </div>
    </div>
  );
}
