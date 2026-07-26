import '@testing-library/jest-dom/vitest';

import { cleanup } from '@testing-library/react';
import { afterEach, beforeEach } from 'vitest';

import i18next, { initI18n, LOCALE_STORAGE_KEY } from '@/i18n';
import { DEFAULT_LOCALE } from '@/lib/rtl';

// i18n is a singleton, so initialize it once for the whole suite.
await initI18n();

beforeEach(async () => {
  // Reset to the application default before every test.
  //
  // Two reasons this is not optional: jsdom reports navigator.language as
  // "en-US", so the language detector picks English rather than the app's
  // Hebrew default; and i18next is a singleton, so a test that switches
  // language would otherwise leak that choice into the next one.
  localStorage.removeItem(LOCALE_STORAGE_KEY);
  if (i18next.resolvedLanguage !== DEFAULT_LOCALE) {
    await i18next.changeLanguage(DEFAULT_LOCALE);
  }
});

afterEach(() => {
  cleanup();
});
