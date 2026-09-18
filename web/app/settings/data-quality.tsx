"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { type apiFetch, errorText } from "@/lib/api/client";
import type { DoctorReport } from "@/types/api";

type Props = {
  request: typeof apiFetch;
  onWarningCount?: (count: number) => void;
};

const MARK = { ok: "✓", warning: "⚠" } as const;

/**
 * Settings › Data Quality (testing-and-data-quality.html §5): the service's own findings,
 * shown as it wrote them. It loads its own report, as the rules section loads its rules, so the
 * settings loader's fixed sequence is not renumbered.
 */
export function DataQuality({ request, onWarningCount }: Props) {
  const [report, setReport] = useState<DoctorReport>();
  const [problem, setProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    void request<DoctorReport>("/doctor")
      .then((loaded) => {
        if (current) {
          setReport(loaded);
          onWarningCount?.(loaded.sections.reduce((count, section) =>
            count + section.findings.filter((finding) => finding.level === "warning").length, 0));
        }
      })
      .catch((error: unknown) => {
        if (!current) return;
        setProblem(
          errorText(error, "The data-quality report could not be loaded."),
        );
        onWarningCount?.(0);
      });
    return () => {
      current = false;
    };
  }, [request, onWarningCount]);

  return (
    <section aria-labelledby="data-quality-title" className="settings-block">
      <div className="settings-block-head">
        <div>
          <p className="section-label">Keep your ledger reliable</p>
          <h2 id="data-quality-title" tabIndex={-1}>Data Quality</h2>
        </div>
        <div className="settings-block-actions">
          <Link className="text-action" href="/coverage">History coverage</Link>
          <Link className="text-action" href="/investments">Investment reviews</Link>
        </div>
      </div>
      {!report && !problem && <p className="loading-frame" role="status">Checking data health…</p>}
      {problem && <Alert variant="destructive"><AlertDescription>{problem}</AlertDescription></Alert>}
      {report && (
        <div className="data-quality-groups">
          {report.sections.map((section) => {
            const warnings = section.findings.filter((finding) => finding.level === "warning").length;
            return (
              <Card className="data-quality-group" key={section.title}>
                <Collapsible defaultOpen={warnings > 0 && section.findings.length <= 2}>
                  <CollapsibleTrigger className="data-quality-trigger">
                    <span>{section.title}</span>
                    <Badge variant={warnings > 0 ? "destructive" : "secondary"}>
                      {warnings > 0 ? `${warnings} needs attention` : "No warnings"}
                    </Badge>
                  </CollapsibleTrigger>
                  <CollapsibleContent>
                    <ul>
                      {section.findings.map((finding) => (
                        <li className={finding.level === "warning" ? "is-warning" : undefined} key={finding.message}>
                          <span aria-hidden="true">{MARK[finding.level]}</span> {finding.message}
                        </li>
                      ))}
                    </ul>
                  </CollapsibleContent>
                </Collapsible>
              </Card>
            );
          })}
        </div>
      )}
    </section>
  );
}
