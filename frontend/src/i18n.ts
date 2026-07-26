/**
 * i18n bootstrap.
 *
 * Hebrew is the default and the fallback. Namespaces are split per feature area
 * so a screen only loads the strings it needs. Locale files are imported
 * statically in Phase 0; they move to lazy loading when the bundle grows.
 */

import i18next from 'i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { initReactI18next } from 'react-i18next';

import enCommon from '@/locales/en/common.json';
import enErrors from '@/locales/en/errors.json';
import heCommon from '@/locales/he/common.json';
import heErrors from '@/locales/he/errors.json';

import {
  applyDocumentDirection,
  DEFAULT_LOCALE,
  normalizeLocale,
  SUPPORTED_LOCALES,
} from './lib/rtl';

export const LOCALE_STORAGE_KEY = 'kavim.locale';

const resources = {
  he: { common: heCommon, errors: heErrors },
  en: { common: enCommon, errors: enErrors },
} as const;

export async function initI18n(): Promise<typeof i18next> {
  await i18next
    .use(LanguageDetector)
    .use(initReactI18next)
    .init({
      resources,
      fallbackLng: DEFAULT_LOCALE,
      supportedLngs: [...SUPPORTED_LOCALES],
      // "he-IL" and "he" must resolve to the same bundle.
      load: 'languageOnly',
      nonExplicitSupportedLngs: true,
      defaultNS: 'common',
      ns: ['common', 'errors'],
      interpolation: {
        // React escapes output already; double-escaping mangles Hebrew
        // punctuation and quotation marks.
        escapeValue: false,
      },
      detection: {
        // An explicit user choice wins over the browser's guess.
        order: ['localStorage', 'navigator'],
        lookupLocalStorage: LOCALE_STORAGE_KEY,
        caches: ['localStorage'],
      },
      returnNull: false,
      // A missing key must be loud in development and invisible in production.
      // Spread rather than pass `undefined` — `exactOptionalPropertyTypes` makes
      // an explicit undefined a type error here.
      ...(import.meta.env.DEV
        ? {
            saveMissing: true,
            missingKeyHandler: (_lngs: readonly string[], ns: string, key: string): void => {
              console.warn(`[i18n] missing key: ${ns}:${key}`);
            },
          }
        : {}),
    });

  applyDocumentDirection(normalizeLocale(i18next.resolvedLanguage));

  // Keep the document in sync on every subsequent language change.
  i18next.on('languageChanged', (lng) => {
    applyDocumentDirection(normalizeLocale(lng));
  });

  return i18next;
}

export default i18next;
