/**
 * Routes.
 *
 * Auth screens are lazy-loaded: once a worker is signed in they never see them
 * again, so shipping them in the initial chunk costs every page load for a
 * one-time flow. The admin screens are lazy for the mirror-image reason — most
 * users can never open them, and the RoleMatrix is the heaviest screen in the
 * app. The bundle budget is 250 kB gzipped (NFR-02).
 *
 * The invite flow is three nested routes under one token rather than three
 * top-level ones, so the token stays in the path and a mid-flow reload resumes
 * instead of restarting.
 *
 * The admin subtree is guarded twice on purpose: `RequireAuth` establishes that
 * there *is* a user, `RequirePermission` decides whether this one may be here,
 * and each tab re-checks its own permission because the four screens do not
 * share one. None of it is security — the server declares `require_permission`
 * on every route and re-checks on every call (CLAUDE.md rule 2).
 */

import { lazy, Suspense } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';

import { ChunkFallback } from '@/components/common/ChunkFallback';
import { ShellLayout } from '@/components/layout/ShellLayout';
import { ADMIN_PERMISSIONS } from '@/features/admin/constants';
import { RequireAuth } from '@/features/auth/RequireAuth';
import { PermissionGate, RequirePermission } from '@/features/auth/RequirePermission';
import { SystemStatus } from '@/features/system/SystemStatus';
import { routerBasename } from '@/lib/basePath';

const Login = lazy(() => import('@/features/auth/Login'));
const InvitationLanding = lazy(() => import('@/features/auth/InvitationLanding'));
const OtpVerify = lazy(() => import('@/features/auth/OtpVerify'));
const Register = lazy(() => import('@/features/auth/Register'));
const ForgotPassword = lazy(() => import('@/features/auth/ForgotPassword'));
const ResetPassword = lazy(() => import('@/features/auth/ResetPassword'));

const AdminLayout = lazy(() =>
  import('@/features/admin/AdminLayout').then((module) => ({ default: module.AdminLayout })),
);
const AdminIndex = lazy(() =>
  import('@/features/admin/AdminLayout').then((module) => ({ default: module.AdminIndex })),
);
const UserTable = lazy(() =>
  import('@/features/admin/UserTable').then((module) => ({ default: module.UserTable })),
);
const RoleMatrix = lazy(() =>
  import('@/features/admin/RoleMatrix').then((module) => ({ default: module.RoleMatrix })),
);
const InvitationPanel = lazy(() =>
  import('@/features/admin/InvitationPanel').then((module) => ({
    default: module.InvitationPanel,
  })),
);
const AuditLogView = lazy(() =>
  import('@/features/admin/AuditLogView').then((module) => ({ default: module.AuditLogView })),
);

const lazyRoute = (element: React.ReactNode): React.JSX.Element => (
  <Suspense fallback={<ChunkFallback />}>{element}</Suspense>
);

export const router = createBrowserRouter(
  [
    // ── public ──────────────────────────────────────────────────────────────
    { path: '/login', element: lazyRoute(<Login />) },
    { path: '/forgot-password', element: lazyRoute(<ForgotPassword />) },
    { path: '/reset-password/:token', element: lazyRoute(<ResetPassword />) },

    // The path shape the invitation email links to — see `registration_url` in
    // `modules/auth/invitations.py`. Changing either side breaks the emailed link.
    { path: '/invite/:token', element: lazyRoute(<InvitationLanding />) },
    { path: '/invite/:token/verify', element: lazyRoute(<OtpVerify />) },
    { path: '/invite/:token/register', element: lazyRoute(<Register />) },

    // ── authenticated ───────────────────────────────────────────────────────
    {
      element: <RequireAuth />,
      children: [
        {
          element: <ShellLayout />,
          children: [
            { path: '/', element: <SystemStatus /> },

            {
              path: '/admin',
              element: <RequirePermission anyOf={ADMIN_PERMISSIONS} />,
              children: [
                {
                  element: lazyRoute(<AdminLayout />),
                  children: [
                    {
                      // `/admin` forwards to the first tab this user can open.
                      // A real index route: a pathless layout route only matches
                      // when one of its children does, so without this `/admin`
                      // rendered an empty outlet.
                      index: true,
                      element: lazyRoute(<AdminIndex />),
                    },
                    {
                      path: 'users',
                      element: (
                        <PermissionGate anyOf={['user:manage']}>
                          {lazyRoute(<UserTable />)}
                        </PermissionGate>
                      ),
                    },
                    {
                      path: 'roles',
                      element: (
                        <PermissionGate anyOf={['user:manage_permissions']}>
                          {lazyRoute(<RoleMatrix />)}
                        </PermissionGate>
                      ),
                    },
                    {
                      path: 'invitations',
                      element: (
                        <PermissionGate anyOf={['user:invite']}>
                          {lazyRoute(<InvitationPanel />)}
                        </PermissionGate>
                      ),
                    },
                    {
                      path: 'audit-log',
                      element: (
                        <PermissionGate anyOf={['audit:read']}>
                          {lazyRoute(<AuditLogView />)}
                        </PermissionGate>
                      ),
                    },
                  ],
                },
              ],
            },
          ],
        },
      ],
    },

    // Unknown paths go home rather than to a 404 screen: there are two
    // destinations for a signed-in user right now, and RequireAuth handles the
    // rest.
    { path: '*', element: <Navigate to="/" replace /> },
  ],
  // Every route above is written as if the app were at the origin root. The
  // basename is what makes that true under a reverse-proxy subpath: `/login`
  // resolves to `/kavim/login` without a single route knowing the prefix
  // exists. It is `/` in development, so nothing changes there.
  { basename: routerBasename() },
);
