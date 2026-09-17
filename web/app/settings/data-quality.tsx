"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { type apiFetch, errorText } from "@/lib/api/client";
import type { DoctorReport } from "@/types/api";

type Props = {
  request: typeof apiFetch;
};

const MARK = { ok: "✓", warning: "⚠" } as const;

/**
 * Settings › Data Quality (testing-and-data-quality.html §5): the service's own findings,
 * shown as it wrote them. It loads its own report, as the rules section loads its rules, so the
 * settings loader's fixed sequence is not renumbered.
 */
export function DataQuality({ request }: Props) {
  const [report, setReport] = useState<DoctorReport>();
  const [problem, setProblem] = useState<string>();

  useEffect(() => {
    let current = true;
    void request<DoctorReport>("/doctor")
      .then((loaded) => {
        if (current) setReport(loaded);
      })
      .catch((error: unknown) => {
        if (!current) return;
        setProblem(
          errorText(error, "The data-quality report could not be loaded."),
        );
      });
    return () => {
      current = false;
    };
  }, [request]);

  return (
    <section aria-labelledby="data-quality-title" className="settings-block">
      <div className="settings-block-head">
        <div>
          <p className="section-label">Keep your ledger reliable</p>
          <h2 id="data-quality-title">Data Quality</h2>
        </div>
        <div className="settings-block-actions">
          <Link className="text-action" href="/coverage">History coverage</Link>
          <Link className="text-action" href="/investments">Investment reviews</Link>
        </div>
      </div>
      {problem && <p className="inline-alert" role="alert">{problem}</p>}
      {report && (
        <div className="data-quality-groups">
          {report.sections.map((section) => {
            const warnings = section.findings.filter((finding) => finding.level === "warning").length;
            return (
              <details className="data-quality-group" key={section.title} open={warnings > 0 && section.findings.length <= 2}>
                <summary>
                  <span>{section.title}</span>
                  <span className={warnings > 0 ? "data-quality-count is-warning" : "data-quality-count"}>
                    {warnings > 0 ? `${warnings} needs attention` : "No warnings"}
                  </span>
                </summary>
                <ul>
                  {section.findings.map((finding) => (
                    <li className={finding.level === "warning" ? "is-warning" : undefined} key={finding.message}>
                      <span aria-hidden="true">{MARK[finding.level]}</span> {finding.message}
                    </li>
                  ))}
                </ul>
              </details>
            );
          })}
        </div>
      )}
    </section>
  );
}
