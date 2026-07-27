import { useTranslation } from 'react-i18next';

import { useDirection } from '@/hooks/useDirection';
import { cn } from '@/lib/cn';
import type { Locale } from '@/lib/rtl';

const OPTIONS: { locale: Locale; labelKey: string }[] = [
  { locale: 'he', labelKey: 'language.hebrew' },
  { locale: 'en', labelKey: 'language.english' },
];

/** Which surface the toggle is sitting on. */
export type LanguageToggleTone = 'onBrand' | 'onLight';

interface LanguageToggleProps {
  /** `onBrand` for the teal app header, `onLight` for the pale auth pages. */
  tone?: LanguageToggleTone;
}

/**
 * Locale switcher.
 *
 * Changing the language also flips `<html dir>`, so this is the fastest way to
 * verify RTL correctness on any screen — which is why it stays visible in
 * development rather than being buried in a settings page.
 *
 * The two tones exist because "blend into the background, and pick out only the
 * selected locale with a white pill" needs opposite colours on opposite
 * surfaces. The auth pages previously got there by painting a `brand-700` block
 * behind the toggle so the dark-surface styling stayed legible — which produced
 * exactly the thing the design avoids: a hard-edged slab floating on a pale
 * page. Found by looking at a screenshot; no test can see it.
 */
export function LanguageToggle({ tone = 'onBrand' }: LanguageToggleProps = {}): React.JSX.Element {
  const { t } = useTranslation();
  const { locale, setLocale } = useDirection();
  const onLight = tone === 'onLight';

  return (
    <div
      role="group"
      aria-label={t('language.label')}
      className={cn(
        'inline-flex gap-1 rounded-lg',
        // On light, a barely-there track gives the white pill something to be
        // lighter *than*. On brand there is no track at all — the header is
        // already the contrast.
        onLight && 'bg-slate-200/60 p-0.5',
      )}
    >
      {OPTIONS.map(({ locale: option, labelKey }) => {
        const active = option === locale;
        return (
          <button
            key={option}
            type="button"
            lang={option}
            aria-current={active ? 'true' : undefined}
            onClick={() => {
              setLocale(option);
            }}
            className={cn(
              'touch-target rounded-md px-3 text-sm transition-colors',
              // Weight carries the selection as well as the background, so the
              // state survives high-contrast mode and greyscale printing.
              active && 'bg-white font-semibold shadow-sm',
              active && (onLight ? 'text-slate-900 ring-1 ring-slate-300' : 'text-brand-700'),
              // brand-100 is the same token the header tagline uses — readable on
              // brand-700 without going full white and competing with the pill.
              !active &&
                (onLight
                  ? 'font-medium text-slate-600 hover:bg-white/70 hover:text-slate-900'
                  : 'text-brand-100 font-medium hover:bg-white/10 hover:text-white'),
            )}
          >
            {t(labelKey)}
          </button>
        );
      })}
    </div>
  );
}
