import type { ReactNode } from "react";

export function ModuleGrid({ children }: { children: ReactNode }) {
  return <div className="module-grid">{children}</div>;
}

export function Module({ title, meta, children, footer }: { title: string; meta?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <section className="console-module">
      <header><h2>{title}</h2>{meta && <span>{meta}</span>}</header>
      <div className="console-module-content">{children}</div>
      {footer && <footer>{footer}</footer>}
    </section>
  );
}
