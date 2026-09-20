"use client";

import { useEffect, useRef, useState } from "react";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
  const regionRef = useRef<HTMLElement>(null);
  const [password, setPassword] = useState("");
  const [preview, setPreview] = useState<DescriptionReparseResult>();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string>();

  useEffect(() => {
    regionRef.current?.scrollIntoView?.({ block: "center" });
    regionRef.current?.focus();
  }, []);

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
    <section
      aria-label={`Re-read descriptions for ${document.title ?? document.documentType.replaceAll("_", " ")}`}
      className="description-repair documents-context-panel"
      ref={regionRef}
      tabIndex={-1}
    >
      <p className="section-label">A newer layout is available for this import.</p>
      {document.isPasswordProtected && (
        <label className="text-field">
          <span>Password</span>
          <Input
            onChange={(event) => setPassword(event.target.value)}
            type="password"
            value={password}
          />
        </label>
      )}
      {problem && (
        <Alert variant="destructive"><AlertDescription>{problem}</AlertDescription></Alert>
      )}
      {preview && (
        <p className="lede">
          {preview.rowsCompared} rows compared · {preview.descriptionsChanged} descriptions would
          change
          {preview.applied ? ` · ${preview.rowsQueuedForReview} queued for review` : ""}.
        </p>
      )}
      <div className="account-form-actions">
        <Button
          disabled={busy}
          onClick={() => void run(false)}
          type="button"
          variant="outline"
        >
          {busy ? "Checking…" : "Preview re-read"}
        </Button>
        {preview && !preview.applied && (
          <Button
            disabled={busy}
            onClick={() => void run(true)}
            type="button"
          >
            Apply
          </Button>
        )}
      </div>
    </section>
  );
}
