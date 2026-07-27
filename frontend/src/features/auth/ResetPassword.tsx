import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useParams } from 'react-router-dom';

import { authApi } from '@/api/client';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Field } from '@/components/ui/Field';

import { AuthLayout } from './AuthLayout';
import { useAuthError } from './useAuthError';

const MIN_PASSWORD_LENGTH = 10;

export default function ResetPassword(): React.JSX.Element {
  const { t } = useTranslation('auth');
  const { token = '' } = useParams<{ token: string }>();
  const describeError = useAuthError('reset');

  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (value: string) => authApi.confirmPasswordReset(token, value),
    onError: (error) => {
      setMessage(describeError(error));
    },
    retry: false,
  });

  // No automatic sign-in. The reset revoked every session including this browser's,
  // so the honest next step is signing in with the new password.
  if (mutation.isSuccess) {
    return (
      <AuthLayout title={t('reset.title')}>
        <Alert tone="success">{t('reset.done')}</Alert>
        <Link to="/login" className="text-brand-700 text-sm font-medium hover:underline">
          {t('forgot.backToLogin')}
        </Link>
      </AuthLayout>
    );
  }

  const mismatch = confirm.length > 0 && password !== confirm;
  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;

  return (
    <AuthLayout title={t('reset.title')} subtitle={t('reset.subtitle')}>
      {message && <Alert tone="error">{message}</Alert>}

      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          setMessage(null);
          if (mismatch || tooShort) return;
          mutation.mutate(password);
        }}
      >
        <Field
          label={t('reset.password')}
          hint={t('register.passwordHint', { count: MIN_PASSWORD_LENGTH })}
          type="password"
          autoComplete="new-password"
          required
          minLength={MIN_PASSWORD_LENGTH}
          ltrValue
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
        />

        <Field
          label={t('reset.passwordConfirm')}
          type="password"
          autoComplete="new-password"
          required
          ltrValue
          error={mismatch ? t('register.mismatch') : undefined}
          value={confirm}
          onChange={(event) => {
            setConfirm(event.target.value);
          }}
        />

        <Button
          type="submit"
          fullWidth
          loading={mutation.isPending}
          disabled={mismatch || tooShort}
        >
          {t('reset.submit')}
        </Button>
      </form>
    </AuthLayout>
  );
}
