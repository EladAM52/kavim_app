import { useTranslation } from 'react-i18next';

import { useDirection } from '@/hooks/useDirection';
import { cn } from '@/lib/cn';
import type { Locale } from '@/lib/rtl';

const OPTIONS: { locale: Locale; labelKey: string }[] = [
  { locale: 'he', labelKey: 'language.hebrew' },
  { locale: 'en', labelKey: 'language.english' },
];

/**
 * Locale switcher.
 *
 * Changing the language also flips `<html dir>`, so this is the fastest way to
 * verify RTL correctness on any screen — which is why it stays visible in
 * development rather than being buried in a settings page.
 */
export function LanguageToggle(): React.JSX.Element {
  const { t } = useTranslation();
  const { locale, setLocale } = useDirection();

  // No track and no border: the group blends into whatever it sits on (the
  // brand-700 header today), and only the selected locale is picked out with a
  // white pill.
  return (
    <div role="group" aria-label={t('language.label')} className="inline-flex gap-1 rounded-lg">
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
              // brand-100 is the same token the header tagline uses — known
              // readable on brand-700 without going full white and competing
              // with the selected pill.
              active
                ? 'text-brand-700 bg-white font-semibold shadow-sm'
                : 'text-brand-100 font-medium hover:bg-white/10 hover:text-white',
            )}
          >
            {t(labelKey)}
          </button>
        );
      })}
    </div>
  );
}
