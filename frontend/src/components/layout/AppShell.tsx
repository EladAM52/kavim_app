import { useTranslation } from 'react-i18next';
import { NavLink } from 'react-router-dom';

import { LanguageToggle } from '@/components/common/LanguageToggle';
import { UserMenu } from '@/components/layout/UserMenu';
import { ADMIN_PERMISSIONS } from '@/features/admin/constants';
import { useAnyPermission } from '@/hooks/usePermission';
import { useBreakpoint } from '@/hooks/useBreakpoint';
import { cn } from '@/lib/cn';

interface AppShellProps {
  children: React.ReactNode;
}

/**
 * Application frame.
 *
 * Phase 3 adds the primary nav and the user menu. The mobile bottom tab bar
 * arrives with the board in Phase 5 — there are two destinations right now, and
 * a tab bar for two links takes 60px of vertical space off a phone for nothing.
 */
export function AppShell({ children }: AppShellProps): React.JSX.Element {
  const { t } = useTranslation();
  const { breakpoint } = useBreakpoint();
  const canAdminister = useAnyPermission(ADMIN_PERMISSIONS);

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

          <nav
            aria-label={t('nav.skipToContent')}
            className="ms-4 hidden items-center gap-1 sm:flex"
          >
            <HeaderLink to="/">{t('nav.myTasks')}</HeaderLink>
            {canAdminister && <HeaderLink to="/admin">{t('nav.admin')}</HeaderLink>}
          </nav>

          {/* ms-auto is the logical equivalent of margin-left in LTR and
              margin-right in RTL, so the controls stay on the trailing edge. */}
          <div className="ms-auto flex items-center gap-2">
            {/* A development affordance, not a product feature: it ships only in
                dev builds, where knowing the active breakpoint is worth a chip. */}
            {import.meta.env.DEV && (
              <span
                className="hidden rounded bg-white/15 px-2 py-1 font-mono text-xs sm:inline"
                title="active breakpoint"
              >
                {breakpoint}
              </span>
            )}
            <LanguageToggle />
            <UserMenu />
          </div>
        </div>

        {/* Below `sm` the nav moves under the brand rather than disappearing —
            the admin area has to be reachable from a phone. */}
        <div className="mx-auto flex max-w-6xl gap-1 px-4 pb-2 sm:hidden">
          <HeaderLink to="/">{t('nav.myTasks')}</HeaderLink>
          {canAdminister && <HeaderLink to="/admin">{t('nav.admin')}</HeaderLink>}
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

function HeaderLink({
  to,
  children,
}: {
  to: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <NavLink
      to={to}
      // Without `end`, "/" matches every path and both links render active.
      end={to === '/'}
      className={({ isActive }) =>
        cn(
          'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
          isActive ? 'bg-white/20 text-white' : 'text-brand-50 hover:bg-white/10',
        )
      }
    >
      {children}
    </NavLink>
  );
}
