import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';

import { authApi, type InvitationPreview, type RegistrationTicket } from '@/api/client';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Field } from '@/components/ui/Field';

import { AuthLayout } from './AuthLayout';
import { useAuthError } from './useAuthError';

/**
 * Step 2 of 3 — prove control of the invited mailbox.
 *
 * The ticket returned here is handed to the register screen through router state
 * rather than the URL: a 15-minute credential in an address bar ends up in
 * history, in screenshots, and in whatever the user pastes into chat.
 */
export default function OtpVerify(): React.JSX.Element {
  const { t } = useTranslation('auth');
  const navigate = useNavigate();
  const { token = '' } = useParams<{ token: string }>();
  const describeError = useAuthError('otp');

  const [code, setCode] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [resent, setResent] = useState(false);

  // Only to show which address the code went to. Cached from the landing screen,
  // so this is normally free.
  const invitation = useQuery<InvitationPreview>({
    queryKey: ['invitation', token],
    queryFn: () => authApi.readInvitation(token),
    retry: false,
    enabled: token.length > 0,
  });

  const verify = useMutation<RegistrationTicket, unknown, string>({
    mutationFn: (value) => authApi.verifyOtp(token, value),
    onSuccess: (ticket) => {
      void navigate(`/invite/${encodeURIComponent(token)}/register`, {
        state: { ticket: ticket.registration_ticket, email: ticket.email },
        replace: true,
      });
    },
    onError: (error) => {
      setMessage(describeError(error));
      // Clear the field: the next attempt is a fresh 6 digits, and leaving the
      // wrong ones in place invites resubmitting them.
      setCode('');
    },
    retry: false,
  });

  const resend = useMutation({
    mutationFn: () => authApi.requestOtp(token),
    onSuccess: () => {
      setResent(true);
      setMessage(null);
      setCode('');
    },
    onError: (error) => {
      setMessage(describeError(error));
    },
    retry: false,
  });

  return (
    <AuthLayout
      title={t('otp.title')}
      step={{ current: 2, total: 3 }}
      subtitle={t('otp.subtitle', { email: invitation.data?.email ?? '' })}
      footer={<p className="text-slate-500">{t('otp.notArrived')}</p>}
    >
      {message && <Alert tone="error">{message}</Alert>}
      {resent && !message && <Alert tone="success">{t('otp.resent')}</Alert>}

      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          setMessage(null);
          setResent(false);
          verify.mutate(code);
        }}
      >
        <Field
          label={t('otp.code')}
          hint={t('otp.codeHint')}
          // numeric, not `type="number"`: a number input strips leading zeros and
          // shows spinner arrows, and a code beginning 0 is perfectly valid.
          inputMode="numeric"
          autoComplete="one-time-code"
          pattern="\d{6}"
          maxLength={6}
          required
          autoFocus
          ltrValue
          className="text-center font-mono text-2xl tracking-[0.4em]"
          value={code}
          onChange={(event) => {
            setCode(event.target.value.replace(/\D/g, '').slice(0, 6));
          }}
        />

        <Button type="submit" fullWidth loading={verify.isPending} disabled={code.length !== 6}>
          {t('otp.submit')}
        </Button>

        <Button
          variant="ghost"
          fullWidth
          loading={resend.isPending}
          onClick={() => {
            resend.mutate();
          }}
        >
          {t('otp.resend')}
        </Button>
      </form>
    </AuthLayout>
  );
}
