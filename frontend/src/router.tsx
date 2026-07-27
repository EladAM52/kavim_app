/**
 * Routes.
 *
 * Auth screens are lazy-loaded: once a worker is signed in they never see them
 * again, so shipping them in the initial chunk costs every page load for a
 * one-time flow. The bundle budget is 250 kB gzipped (NFR-02).
 *
 * The invite flow is three nested routes under one token rather than three
 * top-level ones, so the token stays in the path and a mid-flow reload resumes
 * instead of restarting.
 */

import { lazy, Suspense } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';

import { ChunkFallback } from '@/components/common/ChunkFallback';
import { AppShell } from '@/components/layout/AppShell';
import { RequireAuth } from '@/features/auth/RequireAuth';
import { SystemStatus } from '@/features/system/SystemStatus';

const Login = lazy(() => import('@/features/auth/Login'));
const InvitationLanding = lazy(() => import('@/features/auth/InvitationLanding'));
const OtpVerify = lazy(() => import('@/features/auth/OtpVerify'));
const Register = lazy(() => import('@/features/auth/Register'));
const ForgotPassword = lazy(() => import('@/features/auth/ForgotPassword'));
const ResetPassword = lazy(() => import('@/features/auth/ResetPassword'));

const lazyRoute = (element: React.ReactNode): React.JSX.Element => (
  <Suspense fallback={<ChunkFallback />}>{element}</Suspense>
);

export const router = createBrowserRouter([
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
        path: '/',
        element: (
          <AppShell>
            <SystemStatus />
          </AppShell>
        ),
      },
    ],
  },

  // Unknown paths go home rather than to a 404 screen: there is one destination
  // for a signed-in user right now, and RequireAuth handles the rest.
  { path: '*', element: <Navigate to="/" replace /> },
]);
