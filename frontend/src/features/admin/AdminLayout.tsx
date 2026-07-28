/**
 * The admin area frame.
 *
 * Four screens with four different permissions, which is why the tab strip is
 * built from what the user actually holds rather than rendered whole and
 * disabled. An auditor holds `audit:read` and nothing else — showing them three
 * tabs that answer 403 would read as a broken screen, not as a boundary.
 *
 * `/admin` itself has no content: `AdminIndex` forwards to the first tab the
 * user can open, so the same link works for an administrator and an auditor.
 */

import { useTranslation } from 'react-i18next';
import { NavLink, Navigate, Outlet } from 'react-router-dom';

import { useAnyPermission } from '@/hooks/usePermission';
import { cn } from '@/lib/cn';

import { ADMIN_TABS, type AdminTab } from './constants';

/**
 * The tabs this user may open, in declaration order.
 *
 * One `useAnyPermission` per tab, spelled out: hooks cannot be called in a loop
 * over a list, even one that is a module constant.
 */
function useVisibleTabs(): AdminTab[] {
  const canManageUsers = useAnyPermission(['user:manage']);
  const canManagePermissions = useAnyPermission(['user:manage_permissions']);
  const canInvite = useAnyPermission(['user:invite']);
  const canReadAudit = useAnyPermission(['audit:read']);

  const held: Record<string, boolean> = {
    'user:manage': canManageUsers,
    'user:manage_permissions': canManagePermissions,
    'user:invite': canInvite,
    'audit:read': canReadAudit,
  };

  return ADMIN_TABS.filter((tab) => held[tab.permission] === true);
}

/**
 * `/admin` → the first tab this user can open.
 *
 * A real index route rather than a `pathname === '/admin'` branch inside
 * `AdminLayout`. A pathless layout route only matches when one of its children
 * does, so with no index route `/admin` matched the guard above and rendered its
 * empty outlet — a blank page, which is how this was found.
 */
export function AdminIndex(): React.JSX.Element {
  const first = useVisibleTabs()[0];
  // No tabs at all cannot happen behind the route guard, but rendering the
  // outlet is the harmless branch if it ever does.
  return first ? <Navigate to={first.path} replace /> : <Outlet />;
}

export function AdminLayout(): React.JSX.Element {
  const { t } = useTranslation();
  const visible = useVisibleTabs();

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-xl font-semibold text-slate-900">{t('admin:title')}</h1>

      {/* overflow-x-auto because four Hebrew tab labels do not fit a 320px
          phone, and a tab strip that wraps looks like two rows of buttons. */}
      <nav aria-label={t('admin:title')} className="-mx-4 overflow-x-auto px-4">
        <ul className="flex min-w-max gap-1 border-b border-slate-200">
          {visible.map((tab) => (
            <li key={tab.path}>
              <NavLink
                to={tab.path}
                className={({ isActive }) =>
                  cn(
                    'inline-flex items-center border-b-2 px-3 py-2 text-sm font-medium whitespace-nowrap',
                    isActive
                      ? 'border-brand-600 text-brand-800'
                      : 'border-transparent text-slate-600 hover:text-slate-900',
                  )
                }
              >
                {t(tab.labelKey)}
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>

      <Outlet />
    </div>
  );
}
