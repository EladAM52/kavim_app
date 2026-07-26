import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';

import { type Direction, directionForLocale, type Locale, normalizeLocale } from '@/lib/rtl';

interface UseDirectionResult {
  locale: Locale;
  direction: Direction;
  isRtl: boolean;
  setLocale: (locale: Locale) => void;
  toggleLocale: () => void;
}

/**
 * Current locale and direction, plus the setters.
 *
 * The `<html dir>` attribute is updated by the i18n `languageChanged` listener,
 * so callers only need to change the language — never touch the DOM directly.
 */
export function useDirection(): UseDirectionResult {
  const { i18n } = useTranslation();
  const locale = normalizeLocale(i18n.resolvedLanguage);
  const direction = directionForLocale(locale);

  const setLocale = useCallback(
    (next: Locale) => {
      void i18n.changeLanguage(next);
    },
    [i18n],
  );

  const toggleLocale = useCallback(() => {
    setLocale(locale === 'he' ? 'en' : 'he');
  }, [locale, setLocale]);

  return { locale, direction, isRtl: direction === 'rtl', setLocale, toggleLocale };
}
