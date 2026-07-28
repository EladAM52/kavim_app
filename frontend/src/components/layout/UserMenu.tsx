/**
 * The user menu, and the first place in the UI that can sign out.
 *
 * Phase 2 shipped `authApi.logout` with no button calling it — the shell had
 * nowhere to put one. It lands here.
 *
 * **Sign-out clears the session even when the request fails.** The refresh
 * cookie is httpOnly, so only the server can revoke it; if that call does not
 * land, the durable half of the session survives. But the user asked to be
 * signed out, and leaving them signed in on a shared plant-floor tablet because
 * of a network blip is the worse of the two failures. The access token dies
 * either way, and the refresh family expires on its own.
 */

import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';

import { authApi } from '@/api/client';
import { useAuthStore } from '@/stores/auth';

export function UserMenu(): React.JSX.Element | null {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const signOut = useAuthStore((state) => state.signOut);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const closeTimer = useRef<number | null>(null);

  if (!user) return null;

  const initials = user.full_name.trim().charAt(0) || '?';

  const handleSignOut = async (): Promise<void> => {
    setBusy(true);
    try {
      await authApi.logout();
    } catch {
      // Deliberately swallowed — see the module docstring.
    } finally {
      signOut();
      setBusy(false);
      setOpen(false);
      void navigate('/login', { replace: true });
    }
  };

  return (
    <div
      className="relative"
      onBlur={(event) => {
        // A menu that stays open after focus leaves it covers the header on a
        // phone. The timeout lets focus land on the next element first,
        // otherwise clicking the sign-out button closes the menu before the
        // click registers.
        if (event.currentTarget.contains(event.relatedTarget)) return;
        closeTimer.current = window.setTimeout(() => {
          setOpen(false);
        }, 0);
      }}
      onFocus={() => {
        if (closeTimer.current !== null) {
          window.clearTimeout(closeTimer.current);
          closeTimer.current = null;
        }
      }}
    >
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t('user.menu')}
        onClick={() => {
          setOpen((previous) => !previous);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Escape') setOpen(false);
        }}
        className="touch-target inline-flex items-center justify-center rounded-full bg-white/15 px-3 text-sm font-semibold text-white"
      >
        <span aria-hidden="true">{initials}</span>
      </button>

      {open && (
        // end-0, not right-0: the panel hangs from the trailing edge, which is
        // the left in Hebrew (CLAUDE.md rule 1).
        <div
          role="menu"
          className="absolute end-0 z-50 mt-1 w-56 rounded-lg border border-slate-200 bg-white p-2 shadow-lg"
        >
          <p className="px-2 pb-2 text-xs text-slate-500">
            {t('user.signedInAs')}{' '}
            <span dir="ltr" className="ltr-embed block font-medium text-slate-800">
              {user.email}
            </span>
          </p>
          <button
            type="button"
            role="menuitem"
            disabled={busy}
            onClick={() => {
              void handleSignOut();
            }}
            className="touch-target w-full rounded-md px-2 text-start text-sm font-medium text-slate-800 hover:bg-slate-100 disabled:opacity-50"
          >
            {t('user.signOut')}
          </button>
        </div>
      )}
    </div>
  );
}
