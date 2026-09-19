import type { ReactNode } from "react";

export type DecisionBandProps = {
  tone: "neutral" | "attention" | "danger" | "success";
  title: string;
  summary: string;
  action?: ReactNode;
  children?: ReactNode;
};

export function DecisionBand({ tone, title, summary, action, children }: DecisionBandProps) {
  return (
    <section aria-label={title} className="decision-band" data-tone={tone}>
      <div className="decision-band-copy">
        <p className="decision-band-kicker">Owner decision</p>
        <h2>{title}</h2>
        <p>{summary}</p>
        {children}
      </div>
      {action && <div className="decision-band-action">{action}</div>}
    </section>
  );
}
