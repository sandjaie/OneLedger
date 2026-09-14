"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { type apiFetch, ApiError } from "@/lib/api/client";
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
          error instanceof ApiError ? error.detail : "The data-quality report could not be loaded.",
        );
      });
    return () => {
      current = false;
    };
  }, [request]);

  return (
    <section aria-labelledby="data-quality-title" className="settings-block">
      <h2 id="data-quality-title">Data Quality</h2>
      <p><Link href="/coverage">View history coverage</Link></p>
      {problem && <p className="inline-alert" role="alert">{problem}</p>}
      {report?.sections.map((section) => (
        <div key={section.title}>
          <p className="section-label">{section.title}</p>
          <ul>
            {section.findings.map((finding) => (
              <li key={finding.message}>
                {MARK[finding.level]} {finding.message}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
