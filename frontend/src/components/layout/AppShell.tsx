import { useTranslation } from 'react-i18next';

import { LanguageToggle } from '@/components/common/LanguageToggle';
import { useBreakpoint } from '@/hooks/useBreakpoint';

interface AppShellProps {
  children: React.ReactNode;
}

/**
 * Application frame.
 *
 * Phase 0 is the header and main region only. The sidebar (desktop) and bottom
 * tab bar (mobile) arrive with routing in Phase 3 — the breakpoint hook is
 * already wired so those are additive rather than a restructure.
 */
export function AppShell({ children }: AppShellProps): React.JSX.Element {
  const { t } = useTranslation();
  const { breakpoint } = useBreakpoint();

  return (
    <div className="flex min-h-dvh flex-col">
      {/* Keyboard users must be able to bypass the header (SPEC NFR-05). */}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:m-2 focus:rounded focus:bg-white focus:px-4 focus:py-2 focus:shadow-lg"
      >
        {t('nav.skipToContent')}
      </a>

      <header className="bg-brand-700 sticky top-0 z-40 text-white shadow-sm">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4">
          <div className="flex min-w-0 flex-col">
            <span className="truncate text-base leading-tight font-semibold">{t('app.name')}</span>
            <span className="text-brand-100 truncate text-xs leading-tight">
              {t('app.tagline')}
            </span>
          </div>

          {/* ms-auto is the logical equivalent of margin-left in LTR and
              margin-right in RTL, so the toggle stays on the trailing edge. */}
          <div className="ms-auto flex items-center gap-2">
            <span
              className="hidden rounded bg-white/15 px-2 py-1 font-mono text-xs sm:inline"
              title="active breakpoint"
            >
              {breakpoint}
            </span>
            <LanguageToggle />
          </div>
        </div>
      </header>

      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
        {children}
      </main>

      <footer className="border-t border-slate-200 py-4 text-center text-xs text-slate-500">
        {t('app.name')} · {t('system.subtitle')}
      </footer>
    </div>
  );
}
