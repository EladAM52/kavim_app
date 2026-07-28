/**
 * The role × permission matrix (FR-203).
 *
 * Five roles × thirty permissions, and the widest two-dimensional layout in the
 * project so far — which makes it the highest RTL risk so far. Three things
 * carry that risk and none of them may be written physically:
 *
 *   * the scroll container (`overflow-x-auto`), because in RTL the overflow runs
 *     towards the left and an unscrollable table pushes the page sideways;
 *   * the sticky permission column (`start-0`), which must pin to the right in
 *     Hebrew and the left in English;
 *   * the sticky header row, which is direction-agnostic and stays `top-0`.
 *
 * **Edits are staged, not live.** Every toggle is a whole-set PUT against a role
 * and the cache flush that follows it hits every user, so saving on each click
 * would fire thirty of those while an administrator makes up their mind. The
 * draft also makes the confirmation honest: it can say which roles change and
 * how many people that is, before anything happens.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Fragment, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { adminApi, type PermissionRow, type RoleRow } from '@/api/admin';
import { Ltr } from '@/components/common/Ltr';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { Table, Td, Th } from '@/components/ui/Table';
import { Toggle } from '@/components/ui/Toggle';
import { useApiError } from '@/hooks/useApiError';

/** Draft state: role id → the permission set the administrator wants. */
type Draft = Record<string, string[]>;

export function RoleMatrix(): React.JSX.Element {
  const { t, i18n } = useTranslation(['admin', 'common']);
  const describeError = useApiError();
  const queryClient = useQueryClient();
  const hebrew = i18n.language.startsWith('he');

  const [draft, setDraft] = useState<Draft>({});
  const [confirming, setConfirming] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const permissions = useQuery({
    queryKey: ['admin', 'permissions'],
    queryFn: adminApi.listPermissions,
    // The registry is a code constant on the server; it cannot change under a
    // session.
    staleTime: Infinity,
  });

  const roles = useQuery({
    queryKey: ['admin', 'roles'],
    queryFn: adminApi.listRoles,
  });

  const save = useMutation({
    mutationFn: async (dirty: readonly RoleRow[]): Promise<void> => {
      // Sequential, not `Promise.all`. Each PUT flushes the whole permission
      // cache server-side; firing five at once turns one flush storm into five
      // and makes a partial failure impossible to report accurately.
      for (const role of dirty) {
        const keys = draft[role.id];
        if (!keys) continue;
        await adminApi.replaceRolePermissions(role.id, keys);
      }
    },
    onSettled: async () => {
      // Refetch even on failure: an earlier role in the loop may have saved
      // before a later one was refused, and the screen must show what is true.
      await queryClient.invalidateQueries({ queryKey: ['admin', 'roles'] });
    },
    onSuccess: () => {
      setDraft({});
      setConfirming(false);
      setNotice(t('admin:matrix.saved'));
    },
  });

  const roleRows = roles.data ?? [];
  const permissionRows = permissions.data ?? [];

  const held = (role: RoleRow): string[] => draft[role.id] ?? role.permission_keys;

  const toggle = (role: RoleRow, key: string): void => {
    setNotice(null);
    const current = held(role);
    const next = current.includes(key)
      ? current.filter((entry) => entry !== key)
      : [...current, key];
    setDraft((previous) => ({ ...previous, [role.id]: next }));
  };

  const dirtyRoles = roleRows.filter((role) => {
    const staged = draft[role.id];
    if (!staged) return false;
    return !sameSet(staged, role.permission_keys);
  });

  const changeCount = dirtyRoles.reduce((total, role) => {
    const staged = draft[role.id] ?? [];
    return (
      total + difference(staged, role.permission_keys) + difference(role.permission_keys, staged)
    );
  }, 0);

  const groups = groupByResource(permissionRows);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex-1">
          <h2 className="text-base font-semibold text-slate-900">{t('admin:matrix.title')}</h2>
          <p className="mt-1 text-sm text-slate-600">{t('admin:matrix.hint')}</p>
        </div>
        <div className="flex items-center gap-2">
          {changeCount > 0 && (
            <span className="text-sm font-medium text-amber-700">
              {t('admin:matrix.unsaved', { count: changeCount })}
            </span>
          )}
          <Button
            variant="secondary"
            disabled={dirtyRoles.length === 0}
            onClick={() => {
              setDraft({});
            }}
          >
            {t('admin:matrix.discard')}
          </Button>
          <Button
            disabled={dirtyRoles.length === 0}
            onClick={() => {
              setConfirming(true);
            }}
          >
            {t('admin:matrix.save')}
          </Button>
        </div>
      </div>

      {notice && <Alert tone="success">{notice}</Alert>}
      {roles.isError && <Alert tone="error">{describeError(roles.error)}</Alert>}
      {permissions.isError && <Alert tone="error">{describeError(permissions.error)}</Alert>}
      {save.isError && <Alert tone="error">{describeError(save.error)}</Alert>}

      {(roles.isPending || permissions.isPending) && (
        <p className="text-sm text-slate-500">{t('common:state.loading')}</p>
      )}

      {roleRows.length > 0 && permissionRows.length > 0 && (
        <Table caption={t('admin:matrix.title')}>
          <thead className="sticky top-0 z-20">
            <tr>
              {/* The corner cell has to be sticky on both axes, otherwise it
                  scrolls out from under the permission column. */}
              <Th className="sticky start-0 z-30 min-w-56 bg-slate-50">
                {t('admin:matrix.permission')}
              </Th>
              {roleRows.map((role) => (
                <Th key={role.id} className="min-w-32 text-center">
                  <span className="block">{hebrew ? role.label_he : role.label_en}</span>
                  <span className="block text-[0.65rem] font-normal text-slate-500 normal-case">
                    {t('admin:matrix.holders', { count: role.user_count })}
                  </span>
                </Th>
              ))}
            </tr>
          </thead>

          <tbody>
            {groups.map(([resource, rows]) => (
              <Fragment key={resource}>
                <tr>
                  <th
                    scope="colgroup"
                    colSpan={roleRows.length + 1}
                    className="sticky start-0 border-b border-slate-200 bg-slate-100 px-3 py-1.5 text-start text-xs font-semibold text-slate-700"
                  >
                    {t(`admin:matrix.resources.${resource}`, { defaultValue: resource })}
                  </th>
                </tr>

                {rows.map((permission) => (
                  <tr key={permission.key} className="hover:bg-slate-50/70">
                    <th
                      scope="row"
                      className="sticky start-0 z-10 border-b border-slate-100 bg-white px-3 py-2 text-start font-normal"
                    >
                      <span className="block text-slate-800">
                        {hebrew ? permission.description_he : permission.description_en}
                      </span>
                      <Ltr className="text-xs text-slate-500">{permission.key}</Ltr>
                    </th>

                    {roleRows.map((role) => (
                      <Td key={role.id} className="text-center">
                        <div className="flex justify-center">
                          <Toggle
                            checked={held(role).includes(permission.key)}
                            onChange={() => {
                              toggle(role, permission.key);
                            }}
                            label={`${permission.key} — ${hebrew ? role.label_he : role.label_en}`}
                          />
                        </div>
                      </Td>
                    ))}
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </Table>
      )}

      <Modal
        open={confirming}
        onClose={() => {
          setConfirming(false);
        }}
        title={t('admin:matrix.confirmTitle')}
        description={t('admin:matrix.confirmBody')}
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setConfirming(false);
              }}
            >
              {t('common:actions.cancel')}
            </Button>
            <Button
              loading={save.isPending}
              onClick={() => {
                save.mutate(dirtyRoles);
              }}
            >
              {t('common:actions.save')}
            </Button>
          </>
        }
      >
        <ul className="flex flex-col gap-1 text-sm">
          {dirtyRoles.map((role) => (
            <li key={role.id} className="flex items-center justify-between gap-3">
              <span className="font-medium text-slate-800">
                {hebrew ? role.label_he : role.label_en}
              </span>
              <span className="text-slate-600">
                {t('admin:matrix.holders', { count: role.user_count })}
              </span>
            </li>
          ))}
        </ul>
      </Modal>
    </section>
  );
}

function sameSet(left: readonly string[], right: readonly string[]): boolean {
  if (left.length !== right.length) return false;
  const rightSet = new Set(right);
  return left.every((entry) => rightSet.has(entry));
}

/** How many entries of `left` are missing from `right`. */
function difference(left: readonly string[], right: readonly string[]): number {
  const rightSet = new Set(right);
  return left.filter((entry) => !rightSet.has(entry)).length;
}

/**
 * Grouped by `resource`, in the order the API returned them.
 *
 * `resource` is the grouping key rather than the key's prefix — `column:manage`,
 * `group:manage`, and `template:manage` all carry `structure` so they render
 * under one heading.
 */
function groupByResource(rows: readonly PermissionRow[]): [string, PermissionRow[]][] {
  const groups = new Map<string, PermissionRow[]>();
  for (const row of rows) {
    const bucket = groups.get(row.resource);
    if (bucket) {
      bucket.push(row);
    } else {
      groups.set(row.resource, [row]);
    }
  }
  return [...groups.entries()];
}
