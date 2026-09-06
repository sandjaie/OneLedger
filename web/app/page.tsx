export default function Home() {
  return (
    <div className="site-shell">
      <header className="masthead">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            OL
          </span>
          <span>OneLedger</span>
        </div>
        <p className="phase-label">Foundation / Phase 0</p>
      </header>

      <main className="hero">
        <section className="intro" aria-labelledby="foundation-title">
          <p className="eyebrow">Private financial operating system</p>
          <h1 id="foundation-title">A sound ledger starts below the surface.</h1>
          <p className="lede">
            The web workspace is ready. Secure owner access and financial
            views will arrive in the next Phase 0 deliveries.
          </p>
          <p className="status-line">
            <span className="status-dot" aria-hidden="true" />
            Foundation ready
          </p>
        </section>

        <aside className="build-record" aria-labelledby="build-record-title">
          <div className="record-heading">
            <h2 id="build-record-title">Build record</h2>
            <span>Phase 0</span>
          </div>
          <ol className="record-list">
            <li>
              <span className="record-number">01</span>
              <span className="record-name">Web workspace</span>
              <span className="record-state ready">Ready</span>
            </li>
            <li>
              <span className="record-number">02</span>
              <span className="record-name">Secure owner access</span>
              <span className="record-state">Next</span>
            </li>
            <li>
              <span className="record-number">03</span>
              <span className="record-name">Ledger views</span>
              <span className="record-state">Planned</span>
            </li>
          </ol>
        </aside>
      </main>

      <footer className="footer">
        <p>No account data is connected on this page.</p>
        <p className="edition">Foundation edition</p>
      </footer>
    </div>
  );
}
