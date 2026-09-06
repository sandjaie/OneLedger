export default function UnauthorizedPage() {
  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="unauthorized-title">
        <div className="brand"><span className="brand-mark" aria-hidden="true">OL</span><span>OneLedger</span></div>
        <p className="eyebrow">Owner access required</p>
        <h1 className="auth-title" id="unauthorized-title">This account is not authorized</h1>
        <p className="lede">Use the Google account configured for this private workspace.</p>
        <a className="primary-action" href="/login">Sign in again</a>
      </section>
    </main>
  );
}
