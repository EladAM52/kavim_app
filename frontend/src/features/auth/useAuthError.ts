/**
 * Turn an `ApiError` into copy a worker can act on.
 *
 * Branches on `ApiError.code`, never on the message. The backend's messages are
 * English, unlocalized, and deliberately vague for security reasons — showing them
 * would leak both the language and, in the login case, the reasoning.
 *
 * Anything unrecognised falls back to a generic line rather than surfacing raw
 * server text, so a new backend error code degrades to "try again" instead of
 * showing a Hebrew speaker an English sentence.
 */

import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/api/client';

/** Where the error happened — the same code means different things per screen. */
export type AuthErrorContext = 'login' | 'otp' | 'register' | 'invitation' | 'reset';

export function useAuthError(context: AuthErrorContext): (error: unknown) => string {
  const { t } = useTranslation('auth');

  return useCallback(
    (error: unknown): string => {
      if (!(error instanceof ApiError)) {
        return t('common.genericError');
      }

      if (error.isNetworkError) {
        return t('common.networkError');
      }

      switch (error.code) {
        case 'unauthenticated':
          // On the register screen a 401 means the registration ticket died, not
          // that credentials were wrong.
          return context === 'register' ? t('register.expiredTicket') : t('login.invalid');

        case 'account_locked': {
          // The backend puts the remaining time in `extra`; round up so "0
          // minutes" never appears.
          const seconds = error.problem?.retry_after_seconds;
          const minutes = typeof seconds === 'number' && seconds > 0 ? Math.ceil(seconds / 60) : 15;
          return t('login.locked', { minutes });
        }

        case 'rate_limited':
          return context === 'otp' ? t('otp.tooMany') : t('login.tooMany');

        case 'gone':
          if (context === 'invitation') return t('invitation.expired');
          if (context === 'reset') return t('reset.expired');
          return t('otp.expired');

        case 'not_found':
          return context === 'invitation' ? t('invitation.notFound') : t('common.genericError');

        case 'bad_request':
          return context === 'otp' ? t('otp.invalid') : t('common.genericError');

        case 'validation_failed':
          // Field-level messages are rendered next to their inputs; this is the
          // summary line above the form.
          return error.fieldErrors[0]?.message ?? t('common.genericError');

        default:
          return t('common.genericError');
      }
    },
    [context, t],
  );
}
