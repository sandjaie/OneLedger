export type MetricDatum = {
  label: string;
  value: string;
  detail?: string;
};

export type MetricStripProps = {
  label: string;
  items: readonly MetricDatum[];
};

export function MetricStrip({ label, items }: MetricStripProps) {
  return (
    <section aria-label={label} className="metric-strip">
      <dl>
        {items.map((item) => (
          <div key={item.label}>
            <dt>{item.label}</dt>
            <dd>{item.value}</dd>
            {item.detail && <p>{item.detail}</p>}
          </div>
        ))}
      </dl>
    </section>
  );
}
