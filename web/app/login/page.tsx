import { redirect } from "next/navigation";

import { apiUrl } from "@/lib/api/url";

type Props = { searchParams: Promise<{ reason?: string | string[] }> };

export default async function LoginPage({ searchParams }: Props) {
  const reason = (await searchParams).reason;
  const failed = reason === "authentication_failed";
  if (!failed) redirect(apiUrl("/auth/login"));
  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="brand"><span className="brand-mark" aria-hidden="true">OL</span><span>OneLedger</span></div>
        <p className="eyebrow">Private owner access</p>
        <h1 className="auth-title" id="login-title">Workspace access needs another try.</h1>
        <p className="lede">OneLedger could not open the configured owner workspace.</p>
        <p className="inline-alert" role="alert">Check that the service is available, then try again.</p>
        <a className="primary-action" href={apiUrl("/auth/login")}>Try again</a>
      </section>
    </main>
  );
}
