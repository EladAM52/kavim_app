/**
 * Timestamp rendering.
 *
 * The backend stores `TIMESTAMPTZ` in UTC and every API response is UTC
 * (CLAUDE.md rule 8). The plant is in `Asia/Jerusalem` and workers reading "last
 * login 06:14" mean their own clock, so the conversion happens here — in one
 * place, rather than wherever a component happens to need it.
 *
 * `formatInTimeZone` rather than the browser's local zone: a manager checking
 * the audit log from a laptop still set to UTC must not read different times
 * than the supervisor standing at the line.
 */

import { format as formatWithZone } from 'date-fns-tz';
import { toZonedTime } from 'date-fns-tz';

export const APP_TIME_ZONE = 'Asia/Jerusalem';

/** `dd/MM/yyyy HH:mm`, always LTR, always Jerusalem. */
export function formatDateTime(value: string | null | undefined): string | null {
  const zoned = zonedOrNull(value);
  return zoned ? formatWithZone(zoned, 'dd/MM/yyyy HH:mm', { timeZone: APP_TIME_ZONE }) : null;
}

export function formatDate(value: string | null | undefined): string | null {
  const zoned = zonedOrNull(value);
  return zoned ? formatWithZone(zoned, 'dd/MM/yyyy', { timeZone: APP_TIME_ZONE }) : null;
}

function zonedOrNull(value: string | null | undefined): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  // An unparseable timestamp is a data problem, not a reason to render `Invalid
  // Date` into a table cell.
  if (Number.isNaN(parsed.getTime())) return null;
  return toZonedTime(parsed, APP_TIME_ZONE);
}
