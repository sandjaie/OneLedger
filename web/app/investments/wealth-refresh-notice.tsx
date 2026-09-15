"use client";

import Link from "next/link";

/** Recording or correcting an investment event, a holding price, or funding changes what Wealth
 * history would compute, but never recomputes it — history stays exactly what the last refresh
 * left it. Every screen that makes such a change shows this same notice, so the owner is never
 * left thinking a stale wealth figure already reflects what they just recorded. */
export function WealthRefreshNotice({ message }: { message: string }) {
  return (
    <p className="investment-feedback" role="status">
      {message} <Link href="/wealth">Refresh Wealth</Link> to bring history up to date.
    </p>
  );
}
