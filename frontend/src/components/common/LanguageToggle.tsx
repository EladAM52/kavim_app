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

  return (
    <div
      role="group"
      aria-label={t('language.label')}
      className="inline-flex overflow-hidden rounded-lg border border-slate-300 bg-white"
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
              'touch-target px-3 text-sm font-medium transition-colors',
              'border-e border-slate-200 last:border-e-0',
              active
                ? 'bg-brand-700 text-white'
                : 'text-slate-700 hover:bg-slate-50 active:bg-slate-100',
            )}
          >
            {t(labelKey)}
          </button>
        );
      })}
    </div>
  );
}
