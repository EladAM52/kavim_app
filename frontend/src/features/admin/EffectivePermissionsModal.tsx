/**
 * FR-210 — "why can this person edit this?", answered layer by layer.
 *
 * The endpoint deliberately returns the inputs as well as the result, and this
 * screen renders them the same way. An administrator opening it already knows
 * the effective set is wrong; what they need to see is which layer produced it.
 *
 * Layers 2 and 3 are defined per project, and there is no project picker yet —
 * `modules/projects` is Phase 4. Until then the call is made without a
 * `project_id` and the response says so rather than guessing a project.
 */

import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { adminApi, type AdminUserRow } from '@/api/admin';
import { Ltr } from '@/components/common/Ltr';
import { Alert } from '@/components/ui/Alert';
import { Badge } from '@/components/ui/Badge';
import { Modal } from '@/components/ui/Modal';
import { useApiError } from '@/hooks/useApiError';
import { formatDateTime } from '@/lib/datetime';

interface EffectivePermissionsModalProps {
  user: AdminUserRow;
  onClose: () => void;
}

export function EffectivePermissionsModal({
  user,
  onClose,
}: EffectivePermissionsModalProps): React.JSX.Element {
  const { t, i18n } = useTranslation(['admin', 'common']);
  const describeError = useApiError();

  const trace = useQuery({
    queryKey: ['admin', 'effective-permissions', user.id],
    queryFn: () => adminApi.effectivePermissions(user.id),
  });

  return (
    <Modal
      open
      onClose={onClose}
      title={t('admin:users.permissionsTitle', { name: user.full_name })}
      className="sm:max-w-2xl"
    >
      {trace.isPending && <p className="text-sm text-slate-500">{t('common:state.loading')}</p>}
      {trace.isError && <Alert tone="error">{describeError(trace.error)}</Alert>}

      {trace.data && (
        <div className="flex flex-col gap-5 text-sm">
          <Section title={t('admin:trace.roles')}>
            <div className="flex flex-wrap gap-1">
              {trace.data.roles.map((role) => (
                <Badge key={role} tone="info">
                  {t(`admin:roleKeys.${role}`, { defaultValue: role })}
                </Badge>
              ))}
            </div>
          </Section>

          <Section title={t('admin:trace.layer1')}>
            <KeyList keys={trace.data.layer1_role_permissions} />
          </Section>

          <Section title={t('admin:trace.layer2')}>
            {trace.data.layer2_project_level === null ? (
              <p className="text-slate-500">{t('admin:trace.layer2None')}</p>
            ) : (
              <>
                <Badge tone="neutral">{trace.data.layer2_project_level}</Badge>
                <KeyList keys={trace.data.layer2_level_permissions} />
              </>
            )}
          </Section>

          <Section title={t('admin:trace.layer3')}>
            {trace.data.layer3_columns.length === 0 ? (
              <p className="text-slate-500">{t('admin:trace.layer2None')}</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {trace.data.layer3_columns.map((column) => (
                  <li key={column.key} className="flex items-center gap-2">
                    <Badge tone={column.editable ? 'success' : 'neutral'}>
                      {column.editable ? t('admin:trace.editable') : t('admin:trace.notEditable')}
                    </Badge>
                    <span className="text-slate-800">
                      {i18n.language.startsWith('he') ? column.label_he : column.label_en}
                    </span>
                    <Ltr className="text-xs text-slate-500">{column.key}</Ltr>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title={t('admin:trace.effective')}>
            <KeyList keys={trace.data.effective} />
          </Section>

          <p className="text-xs text-slate-400">
            {t('admin:trace.computedAt', { time: formatDateTime(trace.data.computed_at) ?? '' })}
          </p>
        </div>
      )}
    </Modal>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold tracking-wide text-slate-500 uppercase">{title}</h3>
      {children}
    </section>
  );
}

/** Permission keys are `resource:action[:qualifier]` — LTR, always. */
function KeyList({ keys }: { keys: readonly string[] }): React.JSX.Element {
  if (keys.length === 0) {
    return <p className="text-slate-500">—</p>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {keys.map((key) => (
        <Ltr
          key={key}
          className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-xs text-slate-700"
        >
          {key}
        </Ltr>
      ))}
    </div>
  );
}
