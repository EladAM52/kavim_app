/**
 * The audit log (FR-208).
 *
 * Read-only, and the note at the top says so: the table is append-only in the
 * database itself — a trigger refuses `UPDATE` outright and refuses `DELETE`
 * without an explicit maintenance flag — so there is nothing here to edit and no
 * point offering it.
 *
 * `before`/`after` are arbitrary JSONB, so they render as formatted JSON inside a
 * disclosure rather than as columns. Rows are one line each until asked; an
 * auditor scanning three hundred entries for "who deactivated this account" needs
 * density first and detail second.
 */

import { useInfiniteQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { adminApi, type AuditPage, type AuditRow } from '@/api/admin';
import { Ltr } from '@/components/common/Ltr';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Field } from '@/components/ui/Field';
import { EmptyRow, Table, Td, Th, Tr } from '@/components/ui/Table';
import { useApiError } from '@/hooks/useApiError';
import { useDebounced } from '@/hooks/useDebounced';
import { formatDateTime } from '@/lib/datetime';

const COLUMN_COUNT = 5;

export function AuditLogView(): React.JSX.Element {
  const { t } = useTranslation(['admin', 'common']);
  const describeError = useApiError();

  const [action, setAction] = useState('');
  const [entityType, setEntityType] = useState('');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');

  const debouncedAction = useDebounced(action, 300);
  const debouncedEntity = useDebounced(entityType, 300);

  const filters = {
    action: debouncedAction || undefined,
    entity_type: debouncedEntity || undefined,
    // A date input yields `2026-07-28`; the API wants a datetime. Anchoring to
    // the start of the day keeps "from the 28th" inclusive.
    since: since ? `${since}T00:00:00` : undefined,
    until: until ? `${until}T23:59:59` : undefined,
  };

  const entries = useInfiniteQuery<AuditPage>({
    queryKey: ['admin', 'audit-log', filters],
    queryFn: ({ pageParam }) =>
      adminApi.listAuditLog({ ...filters, cursor: pageParam as string | undefined }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const rows = entries.data?.pages.flatMap((page) => page.items) ?? [];
  const filtered = action !== '' || entityType !== '' || since !== '' || until !== '';

  return (
    <section className="flex flex-col gap-4">
      <div>
        <h2 className="text-base font-semibold text-slate-900">{t('admin:audit.title')}</h2>
        <p className="mt-1 text-sm text-slate-600">{t('admin:audit.hint')}</p>
      </div>

      <div className="grid gap-3 sm:grid-cols-4">
        <Field
          label={t('admin:audit.filterAction')}
          value={action}
          ltrValue
          onChange={(event) => {
            setAction(event.target.value);
          }}
        />
        <Field
          label={t('admin:audit.filterEntity')}
          value={entityType}
          ltrValue
          onChange={(event) => {
            setEntityType(event.target.value);
          }}
        />
        <Field
          label={t('admin:audit.since')}
          type="date"
          value={since}
          ltrValue
          onChange={(event) => {
            setSince(event.target.value);
          }}
        />
        <Field
          label={t('admin:audit.until')}
          type="date"
          value={until}
          ltrValue
          onChange={(event) => {
            setUntil(event.target.value);
          }}
        />
      </div>

      {filtered && (
        <div>
          <Button
            variant="ghost"
            onClick={() => {
              setAction('');
              setEntityType('');
              setSince('');
              setUntil('');
            }}
          >
            {t('admin:audit.clear')}
          </Button>
        </div>
      )}

      {entries.isError && <Alert tone="error">{describeError(entries.error)}</Alert>}

      <Table caption={t('admin:audit.title')}>
        <thead>
          <tr>
            <Th>{t('admin:audit.columns.time')}</Th>
            <Th>{t('admin:audit.columns.action')}</Th>
            <Th>{t('admin:audit.columns.entity')}</Th>
            <Th>{t('admin:audit.columns.actor')}</Th>
            <Th>{t('admin:audit.columns.ip')}</Th>
          </tr>
        </thead>
        <tbody>
          {entries.isPending && (
            <EmptyRow colSpan={COLUMN_COUNT}>{t('common:state.loading')}</EmptyRow>
          )}
          {!entries.isPending && rows.length === 0 && (
            <EmptyRow colSpan={COLUMN_COUNT}>{t('admin:audit.empty')}</EmptyRow>
          )}
          {rows.map((entry) => (
            <Tr key={entry.id}>
              <Td className="whitespace-nowrap">
                <Ltr className="text-slate-600">{formatDateTime(entry.created_at)}</Ltr>
              </Td>
              <Td>
                <Ltr className="font-mono text-xs text-slate-800">{entry.action}</Ltr>
                <AuditDetails entry={entry} />
              </Td>
              <Td>
                <Ltr className="text-xs text-slate-600">{entry.entity_type}</Ltr>
              </Td>
              <Td className="text-slate-700">
                {entry.actor_name ?? (
                  <span className="text-slate-400">{t('admin:audit.system')}</span>
                )}
              </Td>
              <Td>
                <Ltr className="text-xs text-slate-500">{entry.ip ?? '—'}</Ltr>
              </Td>
            </Tr>
          ))}
        </tbody>
      </Table>

      {entries.hasNextPage && (
        <div>
          <Button
            variant="secondary"
            loading={entries.isFetchingNextPage}
            onClick={() => {
              void entries.fetchNextPage();
            }}
          >
            {t('admin:common.loadMore')}
          </Button>
        </div>
      )}
    </section>
  );
}

function AuditDetails({ entry }: { entry: AuditRow }): React.JSX.Element | null {
  const { t } = useTranslation('admin');

  if (!entry.before && !entry.after) return null;

  return (
    <details className="mt-1">
      <summary className="cursor-pointer text-xs text-slate-500">{t('audit.details')}</summary>
      <div className="mt-1 flex flex-col gap-2">
        {entry.before && <JsonBlock label={t('audit.before')} value={entry.before} />}
        {entry.after && <JsonBlock label={t('audit.after')} value={entry.after} />}
      </div>
    </details>
  );
}

function JsonBlock({ label, value }: { label: string; value: unknown }): React.JSX.Element {
  return (
    <div>
      <p className="text-[0.65rem] font-semibold tracking-wide text-slate-500 uppercase">{label}</p>
      {/* dir="ltr" on the pre itself: JSON with Hebrew string values renders its
          braces on the wrong side otherwise. */}
      <pre
        dir="ltr"
        className="mt-0.5 max-w-md overflow-x-auto rounded bg-slate-50 p-2 text-[0.7rem] text-slate-700"
      >
        {JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}
