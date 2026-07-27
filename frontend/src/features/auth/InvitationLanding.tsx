import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { authApi, type InvitationPreview } from '@/api/client';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { LTR_EMBED_CLASS } from '@/lib/rtl';

import { AuthLayout } from './AuthLayout';
import { useAuthError } from './useAuthError';

/**
 * Where an invited worker lands from the email link (step 1 of 3).
 *
 * The invited address is shown **read-only**. It is not an input, and there is no
 * field to change it — the account is bound to the invited address server-side, so
 * offering an editable field would promise something the API refuses (SPEC §8.1).
 */
export default function InvitationLanding(): React.JSX.Element {
  const { t } = useTranslation('auth');
  const navigate = useNavigate();
  const { token = '' } = useParams<{ token: string }>();
  const describeError = useAuthError('invitation');
  const [message, setMessage] = useState<string | null>(null);

  const invitation = useQuery<InvitationPreview>({
    queryKey: ['invitation', token],
    queryFn: () => authApi.readInvitation(token),
    // A 410 or 404 is a final answer about this link; retrying cannot change it.
    retry: false,
    enabled: token.length > 0,
  });

  const requestCode = useMutation({
    mutationFn: () => authApi.requestOtp(token),
    onSuccess: () => {
      void navigate(`/invite/${encodeURIComponent(token)}/verify`);
    },
    onError: (error) => {
      setMessage(describeError(error));
    },
    retry: false,
  });

  if (invitation.isPending) {
    return (
      <AuthLayout title={t('invitation.title')}>
        <p className="text-sm text-slate-600">{t('invitation.loading')}</p>
      </AuthLayout>
    );
  }

  if (invitation.isError) {
    return (
      <AuthLayout title={t('invitation.title')}>
        <Alert tone="error">{describeError(invitation.error)}</Alert>
        <Link to="/login" className="text-brand-700 text-sm font-medium hover:underline">
          {t('forgot.backToLogin')}
        </Link>
      </AuthLayout>
    );
  }

  const data = invitation.data;

  return (
    <AuthLayout
      title={t('invitation.title')}
      step={{ current: 1, total: 3 }}
      subtitle={t('invitation.emailReadOnly')}
    >
      {message && <Alert tone="error">{message}</Alert>}

      <dl className="divide-y divide-slate-100 rounded-lg border border-slate-200">
        <ReadOnlyRow label={t('login.email')} value={data.email} ltr />
        <ReadOnlyRow label={t('invitation.invitedAs')} value={data.role_label} />
        <ReadOnlyRow label={t('invitation.invitedBy')} value={data.invited_by_name} />
        <ReadOnlyRow
          label={t('invitation.expiresAt')}
          value={new Date(data.expires_at).toLocaleString(undefined, {
            dateStyle: 'short',
            timeStyle: 'short',
          })}
          ltr
        />
      </dl>

      <Button
        fullWidth
        loading={requestCode.isPending}
        onClick={() => {
          requestCode.mutate();
        }}
      >
        {t('invitation.start')}
      </Button>
    </AuthLayout>
  );
}

function ReadOnlyRow({
  label,
  value,
  ltr = false,
}: {
  label: string;
  value: string;
  ltr?: boolean;
}): React.JSX.Element {
  return (
    <div className="flex items-baseline gap-3 px-3 py-2.5">
      <dt className="text-sm text-slate-600">{label}</dt>
      {/* ms-auto keeps the value on the trailing edge in both directions. */}
      <dd
        className={`ms-auto text-sm font-medium text-slate-900 ${ltr ? LTR_EMBED_CLASS : ''}`}
        dir={ltr ? 'ltr' : undefined}
      >
        {value}
      </dd>
    </div>
  );
}
