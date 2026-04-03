'use client';

/**
 * LocalTime — renders an ISO timestamp in the browser's local timezone.
 * Server components pass ISO strings; this client island converts to local time.
 */

interface LocalTimeProps {
  iso: string;
  /** Pass true for date+time (default), false for time-only. */
  dateAndTime?: boolean;
}

export function LocalTime({ iso, dateAndTime = true }: LocalTimeProps) {
  const date = new Date(iso);
  const display = dateAndTime
    ? date.toLocaleString()
    : date.toLocaleTimeString();
  return <time dateTime={iso}>{display}</time>;
}
