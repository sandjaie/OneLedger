export function EyeIcon({ hidden }: { hidden: boolean }) {
  return (
    <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24">
      <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6Z" />
      <circle cx="12" cy="12" r="2.75" />
      {hidden && <path d="m4 4 16 16" />}
    </svg>
  );
}
