import type { ReactNode } from "react";
import { CircleIcon } from "lucide-react";

export function StatusLabel({ children, tone = "neutral" }: { children: ReactNode; tone?: "attention" | "danger" | "neutral" | "success" }) {
  return <span className="status-label" data-tone={tone}><CircleIcon aria-hidden="true" />{children}</span>;
}
