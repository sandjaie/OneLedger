/** An ISO calendar date as "4 Sept 2026", read in UTC so no timezone shifts the day. */
export function dateLabel(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}T00:00:00Z`));
}

/** A "YYYY-MM" pay month as "Jan 2026", read in UTC so no timezone shifts the month. */
export function monthLabel(value: string) {
  return new Intl.DateTimeFormat("en-GB", {
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${value}-01T00:00:00Z`));
}
