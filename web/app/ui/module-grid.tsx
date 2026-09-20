import type { ReactNode } from "react";

export function ModuleGrid({ children }: { children: ReactNode }) {
  return <div className="module-grid">{children}</div>;
}

export function Module({ title, meta, children, footer }: { title: string; meta?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  // Named so the module is a landmark a keyboard or screen-reader user can reach directly,
  // the way `AttentionRow` names its own; the visible heading stays the module's title.
  return (
    <section aria-label={title} className="console-module">
      <div className="console-module-head"><h2>{title}</h2>{meta && <span>{meta}</span>}</div>
      <div className="console-module-content">{children}</div>
      {footer && <div className="console-module-foot">{footer}</div>}
    </section>
  );
}
