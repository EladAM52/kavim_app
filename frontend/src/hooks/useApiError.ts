/**
 * `ApiError` → a line of copy, for screens outside the auth flow.
 *
 * The auth screens have `useAuthError`, which maps the same codes to
 * flow-specific wording ("that code has expired" rather than "gone"). Everything
 * else wants the generic sentence for the code, which is exactly what the
 * `errors` namespace holds.
 *
 * The server's `detail` is never rendered. It is English and unlocalized, so
 * showing it puts a Latin sentence in the middle of a Hebrew screen — the
 * failure Playwright asserts against in `auth.spec.ts`.
 */

import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';

import { ApiError } from '@/api/client';

export function useApiError(): (error: unknown) => string {
  const { t } = useTranslation('errors');

  return useCallback(
    (error: unknown): string => {
      if (!(error instanceof ApiError)) return t('generic');
      if (error.isNetworkError) return t('network');

      if (error.code === 'rate_limited') {
        const seconds = error.problem?.retry_after_seconds ?? 60;
        return t('rate_limited', { seconds });
      }

      // An unknown code degrades to the generic line rather than to a missing-key
      // placeholder or raw server text.
      return t(error.code, { defaultValue: t('generic') });
    },
    [t],
  );
}
