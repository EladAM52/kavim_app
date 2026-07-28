/**
 * User administration (FR-201, FR-202, FR-206, FR-207).
 *
 * Keyset pagination, so there is a "load more" and no page numbers: the API
 * returns a cursor and deliberately no total (SPEC §9.1). A page-number control
 * would have to invent one.
 */

import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { adminApi, type AdminUserRow, type UserPage } from '@/api/admin';
import { Ltr } from '@/components/common/Ltr';
import { Alert } from '@/components/ui/Alert';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Field } from '@/components/ui/Field';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { EmptyRow, Table, Td, Th, Tr } from '@/components/ui/Table';
import { useApiError } from '@/hooks/useApiError';
import { useDebounced } from '@/hooks/useDebounced';
import { formatDateTime } from '@/lib/datetime';
import { useAuthStore } from '@/stores/auth';

import { EffectivePermissionsModal } from './EffectivePermissionsModal';
import { UserEditModal } from './UserEditModal';
import { ROLE_KEYS, USER_STATUSES } from './constants';

const COLUMN_COUNT = 6;

export function UserTable(): React.JSX.Element {
  const { t } = useTranslation(['admin', 'common']);
  const describeError = useApiError();
  const queryClient = useQueryClient();
  const currentUserId = useAuthStore((state) => state.user?.id);

  const [search, setSearch] = useState('');
  const [role, setRole] = useState('');
  const [status, setStatus] = useState('');
  // Typing in the search box must not fire a request per keystroke: the list is
  // behind `user:manage` and every call is an authorization resolution.
  const debouncedSearch = useDebounced(search, 300);

  const [editing, setEditing] = useState<AdminUserRow | null>(null);
  const [inspecting, setInspecting] = useState<AdminUserRow | null>(null);
  const [loggingOut, setLoggingOut] = useState<AdminUserRow | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const filters = {
    q: debouncedSearch || undefined,
    role: role || undefined,
    status: status || undefined,
  };

  const users = useInfiniteQuery<UserPage>({
    queryKey: ['admin', 'users', filters],
    queryFn: ({ pageParam }) =>
      adminApi.listUsers({ ...filters, cursor: pageParam as string | undefined }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });

  const forceLogout = useMutation({
    mutationFn: (user: AdminUserRow) => adminApi.forceLogout(user.id),
    onSuccess: async () => {
      setNotice(t('admin:users.forceLogoutDone'));
      setLoggingOut(null);
      await queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
    },
  });

  const rows = users.data?.pages.flatMap((page) => page.items) ?? [];

  return (
    <section className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-3">
        <Field
          label={t('admin:users.search')}
          type="search"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
          }}
        />
        <Select
          label={t('admin:users.filterRole')}
          placeholder={t('admin:users.allRoles')}
          value={role}
          onChange={(event) => {
            setRole(event.target.value);
          }}
          options={ROLE_KEYS.map((key) => ({ value: key, label: t(`admin:roleKeys.${key}`) }))}
        />
        <Select
          label={t('admin:users.filterStatus')}
          placeholder={t('admin:users.allStatuses')}
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
          }}
          options={USER_STATUSES.map((key) => ({
            value: key,
            label: t(`admin:userStatus.${key}`),
          }))}
        />
      </div>

      {notice && <Alert tone="success">{notice}</Alert>}
      {users.isError && <Alert tone="error">{describeError(users.error)}</Alert>}
      {forceLogout.isError && <Alert tone="error">{describeError(forceLogout.error)}</Alert>}

      <Table caption={t('admin:users.title')}>
        <thead>
          <tr>
            <Th>{t('admin:users.columns.name')}</Th>
            <Th>{t('admin:users.columns.email')}</Th>
            <Th>{t('admin:users.columns.role')}</Th>
            <Th>{t('admin:users.columns.status')}</Th>
            <Th>{t('admin:users.columns.lastLogin')}</Th>
            <Th>{t('admin:users.columns.actions')}</Th>
          </tr>
        </thead>
        <tbody>
          {users.isPending && (
            <EmptyRow colSpan={COLUMN_COUNT}>{t('common:state.loading')}</EmptyRow>
          )}
          {!users.isPending && rows.length === 0 && (
            <EmptyRow colSpan={COLUMN_COUNT}>{t('admin:users.empty')}</EmptyRow>
          )}
          {rows.map((user) => (
            <Tr key={user.id}>
              <Td className="font-medium text-slate-900">
                {user.full_name}
                {user.id === currentUserId && (
                  <span className="ms-1 text-xs font-normal text-slate-500">
                    {t('admin:common.you')}
                  </span>
                )}
              </Td>
              <Td>
                <Ltr className="text-slate-700">{user.email}</Ltr>
              </Td>
              <Td>{user.roles.map((key) => t(`admin:roleKeys.${key}`)).join(', ')}</Td>
              <Td>
                <UserStatusBadge user={user} />
              </Td>
              <Td>
                {user.last_login_at ? (
                  <Ltr className="text-slate-600">{formatDateTime(user.last_login_at)}</Ltr>
                ) : (
                  <span className="text-slate-400">{t('admin:common.never')}</span>
                )}
              </Td>
              <Td>
                <div className="flex flex-wrap gap-1">
                  <Button
                    variant="ghost"
                    className="px-2 text-xs"
                    onClick={() => {
                      setNotice(null);
                      setEditing(user);
                    }}
                  >
                    {t('admin:users.edit')}
                  </Button>
                  <Button
                    variant="ghost"
                    className="px-2 text-xs"
                    onClick={() => {
                      setNotice(null);
                      setLoggingOut(user);
                    }}
                  >
                    {t('admin:users.forceLogout')}
                  </Button>
                  <Button
                    variant="ghost"
                    className="px-2 text-xs"
                    onClick={() => {
                      setNotice(null);
                      setInspecting(user);
                    }}
                  >
                    {t('admin:users.permissions')}
                  </Button>
                </div>
              </Td>
            </Tr>
          ))}
        </tbody>
      </Table>

      {users.hasNextPage && (
        <div>
          <Button
            variant="secondary"
            loading={users.isFetchingNextPage}
            onClick={() => {
              void users.fetchNextPage();
            }}
          >
            {t('admin:common.loadMore')}
          </Button>
        </div>
      )}

      {editing && (
        <UserEditModal
          user={editing}
          onClose={() => {
            setEditing(null);
          }}
          onSaved={() => {
            setEditing(null);
            setNotice(t('admin:users.saved'));
          }}
        />
      )}

      {inspecting && (
        <EffectivePermissionsModal
          user={inspecting}
          onClose={() => {
            setInspecting(null);
          }}
        />
      )}

      {loggingOut && (
        <Modal
          open
          onClose={() => {
            setLoggingOut(null);
          }}
          title={t('admin:users.forceLogoutTitle', { name: loggingOut.full_name })}
          description={t('admin:users.forceLogoutBody')}
          footer={
            <>
              <Button
                variant="secondary"
                onClick={() => {
                  setLoggingOut(null);
                }}
              >
                {t('common:actions.cancel')}
              </Button>
              <Button
                loading={forceLogout.isPending}
                onClick={() => {
                  forceLogout.mutate(loggingOut);
                }}
              >
                {t('common:actions.confirm')}
              </Button>
            </>
          }
        >
          <Ltr className="text-sm text-slate-600">{loggingOut.email}</Ltr>
        </Modal>
      )}
    </section>
  );
}

function UserStatusBadge({ user }: { user: AdminUserRow }): React.JSX.Element {
  const { t } = useTranslation('admin');

  // Lockout is a separate state from status: a locked account is still `active`,
  // it just cannot sign in for the next few minutes (FR-109). Collapsing the two
  // would tell an administrator to reactivate an account that is not deactivated.
  const locked = user.locked_until !== null && new Date(user.locked_until) > new Date();
  if (locked) {
    return (
      <Badge tone="warning">
        {t('userStatus.locked')}
        {' · '}
        <Ltr>{formatDateTime(user.locked_until)}</Ltr>
      </Badge>
    );
  }

  const tone = user.status === 'active' ? 'success' : user.status === 'invited' ? 'info' : 'danger';
  return <Badge tone={tone}>{t(`userStatus.${user.status}`)}</Badge>;
}
