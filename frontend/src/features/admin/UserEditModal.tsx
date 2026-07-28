/**
 * Role and status, for one user (FR-202, FR-206).
 *
 * Only the fields that changed are sent. A PATCH carrying the unchanged role
 * would still write an audit row saying the role was set — and an audit log full
 * of no-op role changes is one nobody reads.
 */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import {
  adminApi,
  type AdminUserRow,
  type AdminUserUpdate,
  type RoleKey,
  type UserStatus,
} from '@/api/admin';
import { Ltr } from '@/components/common/Ltr';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Select } from '@/components/ui/Select';
import { useApiError } from '@/hooks/useApiError';
import { useAuthStore } from '@/stores/auth';

import { ROLE_KEYS, USER_STATUSES } from './constants';

interface UserEditModalProps {
  user: AdminUserRow;
  onClose: () => void;
  onSaved: () => void;
}

export function UserEditModal({ user, onClose, onSaved }: UserEditModalProps): React.JSX.Element {
  const { t } = useTranslation(['admin', 'common']);
  const describeError = useApiError();
  const queryClient = useQueryClient();
  const isSelf = useAuthStore((state) => state.user?.id) === user.id;

  const [roleKey, setRoleKey] = useState<string>(user.roles[0] ?? '');
  const [status, setStatus] = useState<string>(user.status);

  const save = useMutation({
    mutationFn: (payload: AdminUserUpdate) => adminApi.updateUser(user.id, payload),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['admin', 'users'] });
      onSaved();
    },
  });

  const changed: AdminUserUpdate = {
    ...(roleKey && roleKey !== user.roles[0] ? { role_key: roleKey as RoleKey } : {}),
    ...(status !== user.status ? { status: status as UserStatus } : {}),
  };
  const dirty = Object.keys(changed).length > 0;

  return (
    <Modal
      open
      onClose={onClose}
      title={t('admin:users.editTitle', { name: user.full_name })}
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            {t('common:actions.cancel')}
          </Button>
          <Button
            loading={save.isPending}
            disabled={!dirty}
            onClick={() => {
              save.mutate(changed);
            }}
          >
            {t('common:actions.save')}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Ltr className="text-sm text-slate-600">{user.email}</Ltr>

        {save.isError && <Alert tone="error">{describeError(save.error)}</Alert>}

        <Select
          label={t('admin:users.editRole')}
          value={roleKey}
          disabled={isSelf}
          onChange={(event) => {
            setRoleKey(event.target.value);
          }}
          options={ROLE_KEYS.map((key) => ({ value: key, label: t(`admin:roleKeys.${key}`) }))}
        />

        <Select
          label={t('admin:users.editStatus')}
          value={status}
          disabled={isSelf}
          onChange={(event) => {
            setStatus(event.target.value);
          }}
          options={USER_STATUSES.map((key) => ({
            value: key,
            label: t(`admin:userStatus.${key}`),
          }))}
        />

        {/* The server refuses a self role change and a self deactivation with a
            409 (`admin/users.py`). Disabling the controls says so before the
            attempt instead of after it. */}
        {isSelf && <p className="text-xs text-slate-500">{t('admin:users.editHint')}</p>}
      </div>
    </Modal>
  );
}
