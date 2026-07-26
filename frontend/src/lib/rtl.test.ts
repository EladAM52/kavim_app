import { describe, expect, it } from 'vitest';

import {
  applyDocumentDirection,
  directionForLocale,
  inlineSign,
  isSupportedLocale,
  normalizeLocale,
  physicalToLogical,
} from './rtl';

describe('directionForLocale', () => {
  it('treats Hebrew as RTL', () => {
    expect(directionForLocale('he')).toBe('rtl');
  });

  it('treats English as LTR', () => {
    expect(directionForLocale('en')).toBe('ltr');
  });

  it('handles regional tags', () => {
    // The browser reports "he-IL", not "he" — a naive equality check on the
    // full tag would silently fall through to LTR and break the whole layout.
    expect(directionForLocale('he-IL')).toBe('rtl');
    expect(directionForLocale('en-US')).toBe('ltr');
  });

  it('is case insensitive', () => {
    expect(directionForLocale('HE-il')).toBe('rtl');
  });

  it('defaults unknown locales to LTR', () => {
    expect(directionForLocale('fr')).toBe('ltr');
  });
});

describe('normalizeLocale', () => {
  it('reduces a regional tag to a supported locale', () => {
    expect(normalizeLocale('he-IL')).toBe('he');
    expect(normalizeLocale('en-GB')).toBe('en');
  });

  it('falls back to Hebrew for unsupported or missing values', () => {
    expect(normalizeLocale('fr')).toBe('he');
    expect(normalizeLocale(null)).toBe('he');
    expect(normalizeLocale(undefined)).toBe('he');
    expect(normalizeLocale('')).toBe('he');
  });
});

describe('isSupportedLocale', () => {
  it('accepts only the configured locales', () => {
    expect(isSupportedLocale('he')).toBe(true);
    expect(isSupportedLocale('en')).toBe(true);
    expect(isSupportedLocale('ar')).toBe(false);
  });
});

describe('applyDocumentDirection', () => {
  it('sets lang and dir on the document element', () => {
    expect(applyDocumentDirection('he')).toBe('rtl');
    expect(document.documentElement.getAttribute('dir')).toBe('rtl');
    expect(document.documentElement.getAttribute('lang')).toBe('he');

    expect(applyDocumentDirection('en')).toBe('ltr');
    expect(document.documentElement.getAttribute('dir')).toBe('ltr');
    expect(document.documentElement.getAttribute('lang')).toBe('en');
  });
});

describe('physicalToLogical', () => {
  it('maps directly in LTR', () => {
    expect(physicalToLogical('left', 'ltr')).toBe('start');
    expect(physicalToLogical('right', 'ltr')).toBe('end');
  });

  it('inverts in RTL', () => {
    // This is what makes drag-and-drop and arrow-key navigation behave in
    // Hebrew: physical "left" is the logical *end* of the row.
    expect(physicalToLogical('left', 'rtl')).toBe('end');
    expect(physicalToLogical('right', 'rtl')).toBe('start');
  });
});

describe('inlineSign', () => {
  it('inverts horizontal deltas in RTL', () => {
    expect(inlineSign('ltr')).toBe(1);
    expect(inlineSign('rtl')).toBe(-1);
  });
});
