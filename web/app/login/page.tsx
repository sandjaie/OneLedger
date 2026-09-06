import { apiUrl } from "@/lib/api/url";

type Props = { searchParams: Promise<{ reason?: string | string[] }> };

export default async function LoginPage({ searchParams }: Props) {
  const reason = (await searchParams).reason;
  const failed = reason === "authentication_failed";
  return (
    <main className="auth-shell">
      <section className="auth-card" aria-labelledby="login-title">
        <div className="brand"><span className="brand-mark" aria-hidden="true">OL</span><span>OneLedger</span></div>
        <p className="eyebrow">Private owner access</p>
        <h1 className="auth-title" id="login-title">Sign in to your workspace.</h1>
        <p className="lede">OneLedger uses your verified Google account to open the configured owner workspace.</p>
        {failed && <p className="inline-alert" role="alert">Sign-in could not be completed. Please try again.</p>}
        <a className="primary-action" href={apiUrl("/auth/login")}>Continue with Google</a>
      </section>
    </main>
  );
}
