import { useEffect, useRef } from 'react';
import { Navigate, Outlet, useLocation } from 'react-router-dom';

import { refreshSession } from '@/api/client';
import { useAuthStore } from '@/stores/auth';

/**
 * Gate for authenticated routes, and the app's boot sequence.
 *
 * The access token lives in memory only, so a reload starts with nothing. The
 * httpOnly refresh cookie is the only durable evidence of a session, and only the
 * server can read it — hence one `/auth/refresh` on boot before deciding whether
 * the user is signed in.
 *
 * Status `unknown` means "we have not asked yet" and must render neither the app
 * nor a redirect: bouncing to /login before the refresh answers would sign out
 * every user on every reload.
 */
export function RequireAuth(): React.JSX.Element {
  const status = useAuthStore((state) => state.status);
  const location = useLocation();
  const attempted = useRef(false);

  useEffect(() => {
    // Once per mount. `refreshSession` is single-flight anyway, but this keeps
    // StrictMode's double-invoke from queueing a second call.
    if (status === 'unknown' && !attempted.current) {
      attempted.current = true;
      void refreshSession();
    }
  }, [status]);

  if (status === 'unknown') {
    return <BootScreen />;
  }

  if (status === 'anonymous') {
    // Remember where they were headed so login can return them there.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <Outlet />;
}

/**
 * Deliberately almost empty.
 *
 * This shows for one round trip. A spinner that appears and vanishes in 80ms
 * reads as a flicker, so there is only a stable frame and an accessible label.
 */
function BootScreen(): React.JSX.Element {
  return (
    <div className="flex min-h-dvh items-center justify-center" role="status" aria-live="polite">
      <span className="sr-only">Loading</span>
      <span className="border-brand-600 size-8 animate-spin rounded-full border-2 border-t-transparent" />
    </div>
  );
}
