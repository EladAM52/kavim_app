/**
 * Direction handling.
 *
 * Hebrew is the primary locale, so direction is a first-class concern rather
 * than a late-stage adjustment (SPEC §10.3). Everything direction-related lives
 * here so there is exactly one place to reason about it.
 */

export type Direction = 'ltr' | 'rtl';
export type Locale = 'he' | 'en';

export const SUPPORTED_LOCALES: readonly Locale[] = ['he', 'en'] as const;
export const DEFAULT_LOCALE: Locale = 'he';

/** Locales written right-to-left. Extend here, not at call sites. */
const RTL_LOCALES = new Set<string>(['he', 'ar', 'fa', 'ur']);

export function directionForLocale(locale: string): Direction {
  // Handles regional tags too: "he-IL" → "he".
  const base = locale.toLowerCase().split('-')[0] ?? '';
  return RTL_LOCALES.has(base) ? 'rtl' : 'ltr';
}

export function isSupportedLocale(value: string): value is Locale {
  return (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

export function normalizeLocale(value: string | null | undefined): Locale {
  if (!value) return DEFAULT_LOCALE;
  const base = value.toLowerCase().split('-')[0] ?? '';
  return isSupportedLocale(base) ? base : DEFAULT_LOCALE;
}

/**
 * Apply the locale to the document.
 *
 * Sets `lang` and `dir` on `<html>`, which is what makes CSS logical properties
 * resolve correctly and what screen readers use to pick a voice.
 */
export function applyDocumentDirection(locale: string): Direction {
  const direction = directionForLocale(locale);
  const root = document.documentElement;
  root.setAttribute('lang', locale);
  root.setAttribute('dir', direction);
  return direction;
}

/**
 * Wrap text that must stay LTR inside an RTL paragraph.
 *
 * Numbers, dates, times, phone numbers, emails, and IDs all need this — a bare
 * "12/07" reads as "07/12" to a Hebrew reader, which is a data-integrity
 * problem, not a cosmetic one. Pair with the `.ltr-embed` class.
 */
export const LTR_EMBED_CLASS = 'ltr-embed';

/** Class that mirrors directional icons (chevrons, arrows, undo) under RTL. */
export const MIRROR_CLASS = 'rtl-mirror';

/**
 * Map a physical direction to a logical one for the current document.
 *
 * Needed for the few places where a physical answer is unavoidable — drag
 * deltas, scroll offsets, keyboard arrow handling in the board grid.
 */
export function physicalToLogical(
  physical: 'left' | 'right',
  direction: Direction,
): 'start' | 'end' {
  if (direction === 'rtl') return physical === 'left' ? 'end' : 'start';
  return physical === 'left' ? 'start' : 'end';
}

/** Horizontal sign multiplier: +1 in LTR, -1 in RTL. */
export function inlineSign(direction: Direction): 1 | -1 {
  return direction === 'rtl' ? -1 : 1;
}
