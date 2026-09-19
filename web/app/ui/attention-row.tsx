import type { ReactNode } from "react";
import { CircleAlertIcon, CircleCheckIcon, CircleIcon, TriangleAlertIcon } from "lucide-react";

export type AttentionRowProps = {
  tone: "attention" | "danger" | "neutral" | "success";
  title: string;
  detail?: string;
  action?: ReactNode;
};

const ICONS = {
  attention: TriangleAlertIcon,
  danger: CircleAlertIcon,
  neutral: CircleIcon,
  success: CircleCheckIcon,
};

export function AttentionRow({ tone, title, detail, action }: AttentionRowProps) {
  const Icon = ICONS[tone];
  return (
    <section aria-label={title} className="attention-row" data-tone={tone}>
      <div className="attention-row-main">
        <Icon aria-hidden="true" />
        <div><h2>{title}</h2>{detail && <p>{detail}</p>}</div>
      </div>
      {action && <div className="attention-row-action">{action}</div>}
    </section>
  );
}
