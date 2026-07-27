import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link } from 'react-router-dom';

import { authApi } from '@/api/client';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Field } from '@/components/ui/Field';

import { AuthLayout } from './AuthLayout';
import { useAuthError } from './useAuthError';

export default function ForgotPassword(): React.JSX.Element {
  const { t } = useTranslation('auth');
  const describeError = useAuthError('login');

  const [email, setEmail] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (value: string) => authApi.requestPasswordReset(value),
    onError: (error) => {
      setMessage(describeError(error));
    },
    retry: false,
  });

  // The success copy says "if that address is registered" and is shown on success
  // regardless of whether an account exists — matching the backend's 202, which is
  // deliberately identical either way (SPEC §8.3). Saying "sent!" only for real
  // addresses would rebuild the enumeration oracle the API avoids.
  if (mutation.isSuccess) {
    return (
      <AuthLayout title={t('forgot.title')}>
        <Alert tone="success">{t('forgot.sent')}</Alert>
        <Link to="/login" className="text-brand-700 text-sm font-medium hover:underline">
          {t('forgot.backToLogin')}
        </Link>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title={t('forgot.title')}
      subtitle={t('forgot.subtitle')}
      footer={
        <Link to="/login" className="text-brand-700 font-medium hover:underline">
          {t('forgot.backToLogin')}
        </Link>
      }
    >
      {message && <Alert tone="error">{message}</Alert>}

      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          setMessage(null);
          mutation.mutate(email);
        }}
      >
        <Field
          label={t('forgot.email')}
          type="email"
          autoComplete="username"
          inputMode="email"
          required
          ltrValue
          value={email}
          onChange={(event) => {
            setEmail(event.target.value);
          }}
        />

        <Button type="submit" fullWidth loading={mutation.isPending}>
          {t('forgot.submit')}
        </Button>
      </form>
    </AuthLayout>
  );
}
