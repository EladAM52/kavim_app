import { Link, Outlet } from 'react-router-dom';
import { useTranslation } from 'react-i18next';

import { useAnyPermission } from '@/hooks/usePermission';

interface RequirePermissionProps {
  /** The route renders when the user holds at least one of these. */
  anyOf: readonly string[];
}

/**
 * Route guard for the admin area.
 *
 * Sits *inside* `RequireAuth`, never instead of it: this asks "may this person
 * see this screen", and it can only ask that once there is a person.
 *
 * A denied user gets a "no access" page rather than a redirect to `/`. A silent
 * bounce is indistinguishable from a broken link — somebody following a
 * colleague's URL needs to be told the URL is real and they are not allowed on
 * it, otherwise the next step is a support call.
 *
 * The server denies independently. If this guard were removed entirely, every
 * admin screen would render and every one of its requests would return 403.
 */
export function RequirePermission({ anyOf }: RequirePermissionProps): React.JSX.Element {
  const allowed = useAnyPermission(anyOf);

  if (!allowed) {
    return <Forbidden />;
  }

  return <Outlet />;
}

/**
 * The same check around a subtree rather than an `<Outlet />`.
 *
 * The admin tabs need this: they are children of one layout route, and each has
 * its own permission — `user:manage` for the user table, `audit:read` for the
 * log — so the guard has to wrap the element, not the route's outlet.
 */
export function PermissionGate({
  anyOf,
  children,
}: RequirePermissionProps & { children: React.ReactNode }): React.JSX.Element {
  const allowed = useAnyPermission(anyOf);
  return allowed ? <>{children}</> : <Forbidden />;
}

export function Forbidden(): React.JSX.Element {
  const { t } = useTranslation();

  return (
    <div className="mx-auto max-w-md py-16 text-center" role="alert">
      <h1 className="text-lg font-semibold text-slate-900">{t('forbidden.title')}</h1>
      <p className="mt-2 text-sm text-slate-600">{t('forbidden.body')}</p>
      <Link
        to="/"
        className="text-brand-700 mt-6 inline-block text-sm font-semibold underline underline-offset-4"
      >
        {t('forbidden.back')}
      </Link>
    </div>
  );
}
