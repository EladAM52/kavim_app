import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Link, useNavigate } from 'react-router-dom';

import { authApi, type TokenResponse } from '@/api/client';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Field } from '@/components/ui/Field';
import { useAuthStore } from '@/stores/auth';

import { AuthLayout } from './AuthLayout';
import { useAuthError } from './useAuthError';

export default function Login(): React.JSX.Element {
  const { t } = useTranslation('auth');
  const navigate = useNavigate();
  const signIn = useAuthStore((state) => state.signIn);
  const describeError = useAuthError('login');

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  const mutation = useMutation<TokenResponse, unknown, { email: string; password: string }>({
    mutationFn: (payload) => authApi.login(payload),
    onSuccess: (data) => {
      signIn(data.access_token, data.user);
      // `replace`, so Back does not return to a login form the user has passed.
      void navigate('/', { replace: true });
    },
    onError: (error) => {
      setMessage(describeError(error));
    },
    // No retry: a wrong password retried three times spends three of the ten
    // attempts before the lockout, and the user sees one failure.
    retry: false,
  });

  return (
    <AuthLayout
      title={t('login.title')}
      subtitle={t('login.subtitle')}
      footer={
        <Link to="/forgot-password" className="text-brand-700 font-medium hover:underline">
          {t('login.forgot')}
        </Link>
      }
    >
      {message && <Alert tone="error">{message}</Alert>}

      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          setMessage(null);
          mutation.mutate({ email, password });
        }}
      >
        <Field
          label={t('login.email')}
          type="email"
          name="email"
          autoComplete="username"
          inputMode="email"
          required
          ltrValue
          value={email}
          onChange={(event) => {
            setEmail(event.target.value);
          }}
        />

        <Field
          label={t('login.password')}
          type="password"
          name="password"
          autoComplete="current-password"
          required
          ltrValue
          value={password}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
        />

        <Button type="submit" fullWidth loading={mutation.isPending}>
          {t('login.submit')}
        </Button>
      </form>
    </AuthLayout>
  );
}
