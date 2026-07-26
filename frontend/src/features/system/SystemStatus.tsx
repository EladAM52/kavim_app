import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { type ApiMeta, fetchApiMeta, fetchHealth, type HealthReady } from '@/api/client';
import { useDirection } from '@/hooks/useDirection';
import { cn } from '@/lib/cn';
import { LTR_EMBED_CLASS } from '@/lib/rtl';

type CheckState = 'ok' | 'unreachable' | 'checking';

function StatusDot({ state }: { state: CheckState }): React.JSX.Element {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-block size-2.5 shrink-0 rounded-full',
        state === 'ok' && 'bg-status-done',
        state === 'unreachable' && 'bg-status-blocked',
        state === 'checking' && 'animate-pulse bg-slate-400',
      )}
    />
  );
}

function CheckRow({ label, state }: { label: string; state: CheckState }): React.JSX.Element {
  const { t } = useTranslation();
  const text =
    state === 'checking'
      ? t('system.checking')
      : state === 'ok'
        ? t('system.ok')
        : t('system.unreachable');

  return (
    <div className="flex items-center gap-2 border-b border-slate-100 py-2 last:border-b-0">
      <StatusDot state={state} />
      <span className="text-sm text-slate-700">{label}</span>
      {/* Colour alone never conveys state — there is always a text label. */}
      <span
        className={cn(
          'ms-auto text-sm font-medium',
          state === 'ok' && 'text-emerald-700',
          state === 'unreachable' && 'text-red-700',
          state === 'checking' && 'text-slate-500',
        )}
      >
        {text}
      </span>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }): React.JSX.Element {
  return (
    <div className="flex items-baseline gap-2 border-b border-slate-100 py-2 last:border-b-0">
      <span className="text-sm text-slate-600">{label}</span>
      {/* Versions, timezones, and identifiers stay LTR inside Hebrew text. */}
      <span className={cn('ms-auto font-mono text-sm text-slate-900', LTR_EMBED_CLASS)}>
        {value}
      </span>
    </div>
  );
}

/**
 * Phase 0 acceptance screen.
 *
 * Proves four things at once: the SPA builds and mounts, i18n and RTL work, the
 * Vite proxy reaches FastAPI, and FastAPI reaches Postgres and Redis.
 */
export function SystemStatus(): React.JSX.Element {
  const { t } = useTranslation();
  const { locale, direction } = useDirection();

  const health = useQuery<HealthReady>({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval: 15_000,
    retry: 1,
  });

  const meta = useQuery<ApiMeta>({
    queryKey: ['api-meta'],
    queryFn: fetchApiMeta,
    retry: 1,
  });

  const apiState: CheckState = health.isPending
    ? 'checking'
    : health.isError
      ? 'unreachable'
      : 'ok';
  const dbState: CheckState = health.isPending
    ? 'checking'
    : (health.data?.checks.database ?? 'unreachable');
  const cacheState: CheckState = health.isPending
    ? 'checking'
    : (health.data?.checks.redis ?? 'unreachable');

  const degraded = apiState === 'unreachable' || dbState !== 'ok' || cacheState !== 'ok';

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">{t('system.title')}</h1>
        <p className="mt-1 text-sm text-slate-600">{t('system.subtitle')}</p>
      </div>

      {/* Single column on phones, two on tablet and up. */}
      <div className="grid gap-4 md:grid-cols-2">
        <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-2 text-sm font-semibold tracking-wide text-slate-500 uppercase">
            {t('system.title')}
          </h2>
          <CheckRow label={t('system.api')} state={apiState} />
          <CheckRow label={t('system.database')} state={dbState} />
          <CheckRow label={t('system.cache')} state={cacheState} />
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="mb-2 text-sm font-semibold tracking-wide text-slate-500 uppercase">
            {t('system.environment')}
          </h2>
          <InfoRow label={t('system.version')} value={meta.data?.version ?? '—'} />
          <InfoRow label={t('system.environment')} value={health.data?.environment ?? '—'} />
          <InfoRow label={t('system.locale')} value={locale} />
          <InfoRow label={t('system.timezone')} value={meta.data?.timezone ?? '—'} />
          <InfoRow label={t('system.direction')} value={direction.toUpperCase()} />
        </section>
      </div>

      {degraded && !health.isPending && (
        <p
          role="status"
          className="border-s-4 border-s-amber-400 bg-amber-50 p-3 text-sm text-amber-900"
        >
          {t('system.unreachableHint')}
        </p>
      )}
    </div>
  );
}
