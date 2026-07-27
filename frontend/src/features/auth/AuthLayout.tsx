import { useTranslation } from 'react-i18next';

import { LanguageToggle } from '@/components/common/LanguageToggle';

interface AuthLayoutProps {
  title: string;
  subtitle?: string | undefined;
  /** Renders "Step 2 of 3" above the heading when the screen is part of a sequence. */
  step?: { current: number; total: number } | undefined;
  children: React.ReactNode;
  footer?: React.ReactNode;
}

/**
 * Frame for every unauthenticated screen.
 *
 * Centred and narrow rather than the app shell: there is no navigation to offer
 * someone who is not signed in, and a worker meeting this on a phone should see
 * one column and nothing else.
 *
 * The language toggle stays visible here on purpose — a worker handed an English
 * browser needs to reach Hebrew *before* being able to read anything.
 */
export function AuthLayout({
  title,
  subtitle,
  step,
  children,
  footer,
}: AuthLayoutProps): React.JSX.Element {
  const { t } = useTranslation('auth');

  return (
    <div className="flex min-h-dvh flex-col bg-slate-50">
      <header className="flex items-center justify-between px-4 py-3">
        <span className="text-brand-800 text-lg font-bold">{t('app.name', { ns: 'common' })}</span>
        <LanguageToggleOnLight />
      </header>

      <main className="flex flex-1 items-start justify-center px-4 pb-10">
        <div className="w-full max-w-md">
          <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
            {step && (
              <p className="text-brand-700 mb-2 text-xs font-semibold">
                {t('common.step', { current: step.current, total: step.total })}
              </p>
            )}
            <h1 className="text-xl font-semibold text-slate-900">{title}</h1>
            {subtitle && <p className="mt-1 text-sm text-slate-600">{subtitle}</p>}

            <div className="mt-5 flex flex-col gap-4">{children}</div>
          </div>

          {footer && <div className="mt-4 text-center text-sm">{footer}</div>}
        </div>
      </main>
    </div>
  );
}

/**
 * The toggle is styled for the teal app header, where the unselected state is a
 * light tint. On this light background that would be invisible, so it gets a
 * bordered container to sit in.
 */
function LanguageToggleOnLight(): React.JSX.Element {
  return (
    <div className="bg-brand-700 rounded-lg p-0.5">
      <LanguageToggle />
    </div>
  );
}
