/**
 * Invitations (FR-101, FR-111).
 *
 * This screen is what retires `python -m app.scripts.invite`: until now the only
 * way to get a person into the system was a shell on the server.
 *
 * The delivery note under the form is not decoration. Mail leaves through the
 * outbox sweeper on a 30-second beat (ADR-005), so "sent" here means "queued and
 * committed", and an administrator watching an inbox needs to know that before
 * they conclude it failed and resend — which is rate limited to five per address
 * per fifteen minutes precisely because that is the instinct.
 */

import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  adminApi,
  type EmailLocale,
  type InvitationPage,
  type InvitationRow,
  type RoleKey,
} from '@/api/admin';
import { Ltr } from '@/components/common/Ltr';
import { Alert } from '@/components/ui/Alert';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Field } from '@/components/ui/Field';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { EmptyRow, Table, Td, Th, Tr } from '@/components/ui/Table';
import { useApiError } from '@/hooks/useApiError';
import { formatDateTime } from '@/lib/datetime';

import { EMAIL_LOCALES, INVITATION_STATUSES, ROLE_KEYS } from './constants';

const COLUMN_COUNT = 6;

const STATUS_TONE = {
  pending: 'info',
  consumed: 'success',
  revoked: 'neutral',
  expired: 'warning',
} as const;

export function InvitationPanel(): React.JSX.Element {
  const { t, i18n } = useTranslation(['admin', 'common']);
  const describeError = useApiError();
  const queryClient = useQueryClient();

  const [email, setEmail] = useState('');
  const [roleKey, setRoleKey] = useState<RoleKey>('WORKER');
  /**
   * The language of the *mail*, which is the invitee's, not the sender's.
   *
   * It defaults to the language the administrator is reading — usually right,
   * since they work at the same plant — but it is a field rather than an
   * inference, because the one case that matters is the one the default gets
   * wrong: a Hebrew-speaking manager inviting an English-speaking contractor.
   *
   * Sending it explicitly also stops the value depending on the browser's
   * `Accept-Language`, which the app's language toggle does not change.
   */
  const [locale, setLocale] = useState<EmailLocale>(i18n.language.startsWith('he') ? 'he' : 'en');
  const [statusFilter, setStatusFilter] = useState('');
  const [revoking, setRevoking] = useState<InvitationRow | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const invitations = useInfiniteQuery<InvitationPage>({
    queryKey: ['admin', 'invitations', statusFilter],
    queryFn: ({ pageParam }) =>
      adminApi.listInvitations({
        status: statusFilter || undefined,
        cursor: pageParam as string | undefined,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const refresh = async (): Promise<void> => {
    await queryClient.invalidateQueries({ queryKey: ['admin', 'invitations'] });
  };

  const create = useMutation({
    mutationFn: () =>
      adminApi.createInvitation({ email, role_key: roleKey, project_ids: [], locale }),
    onSuccess: async (row) => {
      setNotice(t('admin:invitations.sent', { email: row.email }));
      setEmail('');
      await refresh();
    },
  });

  const resend = useMutation({
    mutationFn: (invitation: InvitationRow) => adminApi.resendInvitation(invitation.id),
    onSuccess: async () => {
      setNotice(t('admin:invitations.resendDone'));
      await refresh();
    },
  });

  const revoke = useMutation({
    mutationFn: (invitation: InvitationRow) => adminApi.revokeInvitation(invitation.id),
    onSuccess: async () => {
      setNotice(t('admin:invitations.revoked'));
      setRevoking(null);
      await refresh();
    },
  });

  const rows = invitations.data?.pages.flatMap((page) => page.items) ?? [];
  const failure = create.error ?? resend.error ?? revoke.error ?? invitations.error;

  return (
    <section className="flex flex-col gap-5">
      <form
        className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white p-4"
        onSubmit={(event) => {
          event.preventDefault();
          setNotice(null);
          create.mutate();
        }}
      >
        <h2 className="text-base font-semibold text-slate-900">{t('admin:invitations.invite')}</h2>

        <div className="grid gap-3 sm:grid-cols-3">
          <Field
            label={t('admin:invitations.email')}
            type="email"
            required
            ltrValue
            autoComplete="off"
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
            }}
          />
          <Select
            label={t('admin:invitations.role')}
            value={roleKey}
            onChange={(event) => {
              setRoleKey(event.target.value as RoleKey);
            }}
            options={ROLE_KEYS.map((key) => ({ value: key, label: t(`admin:roleKeys.${key}`) }))}
          />
          <Select
            label={t('admin:invitations.locale')}
            value={locale}
            onChange={(event) => {
              setLocale(event.target.value as EmailLocale);
            }}
            options={EMAIL_LOCALES.map((key) => ({
              value: key,
              label: t(`common:language.${key === 'he' ? 'hebrew' : 'english'}`),
            }))}
          />
        </div>

        <p className="text-xs text-slate-500">{t('admin:invitations.localeHint')}</p>
        <p className="text-xs text-slate-500">{t('admin:invitations.deliveryNote')}</p>

        <div>
          <Button type="submit" loading={create.isPending} disabled={email.trim() === ''}>
            {t('admin:invitations.send')}
          </Button>
        </div>
      </form>

      {notice && <Alert tone="success">{notice}</Alert>}
      {failure && <Alert tone="error">{describeError(failure)}</Alert>}

      <div className="sm:max-w-xs">
        <Select
          label={t('admin:invitations.filterStatus')}
          placeholder={t('admin:invitations.allStatuses')}
          value={statusFilter}
          onChange={(event) => {
            setStatusFilter(event.target.value);
          }}
          options={INVITATION_STATUSES.map((key) => ({
            value: key,
            label: t(`admin:invitationStatus.${key}`),
          }))}
        />
      </div>

      <Table caption={t('admin:invitations.title')}>
        <thead>
          <tr>
            <Th>{t('admin:invitations.columns.email')}</Th>
            <Th>{t('admin:invitations.columns.role')}</Th>
            <Th>{t('admin:invitations.columns.status')}</Th>
            <Th>{t('admin:invitations.columns.invitedBy')}</Th>
            <Th>{t('admin:invitations.columns.expires')}</Th>
            <Th>{t('admin:invitations.columns.actions')}</Th>
          </tr>
        </thead>
        <tbody>
          {invitations.isPending && (
            <EmptyRow colSpan={COLUMN_COUNT}>{t('common:state.loading')}</EmptyRow>
          )}
          {!invitations.isPending && rows.length === 0 && (
            <EmptyRow colSpan={COLUMN_COUNT}>{t('admin:invitations.empty')}</EmptyRow>
          )}
          {rows.map((invitation) => (
            <Tr key={invitation.id}>
              <Td>
                <Ltr className="text-slate-800">{invitation.email}</Ltr>
              </Td>
              <Td>
                {t(`admin:roleKeys.${invitation.role_key}`, { defaultValue: invitation.role_key })}
              </Td>
              <Td>
                <Badge tone={STATUS_TONE[invitation.status]}>
                  {t(`admin:invitationStatus.${invitation.status}`)}
                </Badge>
              </Td>
              <Td className="text-slate-600">{invitation.invited_by_name ?? '—'}</Td>
              <Td>
                <Ltr className="text-slate-600">{formatDateTime(invitation.expires_at)}</Ltr>
              </Td>
              <Td>
                {/* Only a pending invitation can be resent or revoked; the
                    others have nothing left to act on. */}
                {invitation.status === 'pending' ? (
                  <div className="flex flex-wrap gap-1">
                    <Button
                      variant="ghost"
                      className="px-2 text-xs"
                      loading={resend.isPending && resend.variables.id === invitation.id}
                      onClick={() => {
                        setNotice(null);
                        resend.mutate(invitation);
                      }}
                    >
                      {t('admin:invitations.resend')}
                    </Button>
                    <Button
                      variant="ghost"
                      className="px-2 text-xs"
                      onClick={() => {
                        setNotice(null);
                        setRevoking(invitation);
                      }}
                    >
                      {t('admin:invitations.revoke')}
                    </Button>
                  </div>
                ) : (
                  <span className="text-slate-400">—</span>
                )}
              </Td>
            </Tr>
          ))}
        </tbody>
      </Table>

      {invitations.hasNextPage && (
        <div>
          <Button
            variant="secondary"
            loading={invitations.isFetchingNextPage}
            onClick={() => {
              void invitations.fetchNextPage();
            }}
          >
            {t('admin:common.loadMore')}
          </Button>
        </div>
      )}

      {revoking && (
        <Modal
          open
          onClose={() => {
            setRevoking(null);
          }}
          title={t('admin:invitations.revokeTitle', { email: revoking.email })}
          description={t('admin:invitations.revokeBody')}
          footer={
            <>
              <Button
                variant="secondary"
                onClick={() => {
                  setRevoking(null);
                }}
              >
                {t('common:actions.cancel')}
              </Button>
              <Button
                loading={revoke.isPending}
                onClick={() => {
                  revoke.mutate(revoking);
                }}
              >
                {t('common:actions.confirm')}
              </Button>
            </>
          }
        >
          <Ltr className="text-sm text-slate-600">{revoking.email}</Ltr>
        </Modal>
      )}
    </section>
  );
}
