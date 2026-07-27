import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Navigate, useLocation, useNavigate } from 'react-router-dom';

import { authApi, type RegisterRequest, type TokenResponse } from '@/api/client';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Field } from '@/components/ui/Field';
import { normalizeLocale } from '@/lib/rtl';
import { useAuthStore } from '@/stores/auth';

import { AuthLayout } from './AuthLayout';
import { useAuthError } from './useAuthError';

const MIN_PASSWORD_LENGTH = 10;

interface TicketState {
  ticket?: string;
  email?: string;
}

/**
 * Step 3 of 3 — set a password and get signed in.
 *
 * There is **no email field**. The address comes from the invitation server-side,
 * and the API rejects a submitted one outright, so rendering an input would offer
 * a choice that does not exist.
 */
export default function Register(): React.JSX.Element {
  const { t, i18n } = useTranslation('auth');
  const navigate = useNavigate();
  const location = useLocation();
  const signIn = useAuthStore((state) => state.signIn);
  const describeError = useAuthError('register');

  const state = (location.state ?? {}) as TicketState;

  const [fullName, setFullName] = useState('');
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [message, setMessage] = useState<string | null>(null);

  const mutation = useMutation<TokenResponse, unknown, RegisterRequest>({
    mutationFn: (payload) => authApi.register(payload),
    onSuccess: (data) => {
      signIn(data.access_token, data.user);
      void navigate('/', { replace: true });
    },
    onError: (error) => {
      setMessage(describeError(error));
    },
    retry: false,
  });

  // Reached directly, without passing OTP verification. Nothing here works without
  // a ticket, so send them back to the start rather than showing a dead form.
  if (!state.ticket) {
    return <Navigate to="/login" replace />;
  }

  const mismatch = confirm.length > 0 && password !== confirm;
  const tooShort = password.length > 0 && password.length < MIN_PASSWORD_LENGTH;

  return (
    <AuthLayout
      title={t('register.title')}
      step={{ current: 3, total: 3 }}
      subtitle={t('register.subtitle')}
    >
      {message && <Alert tone="error">{message}</Alert>}

      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          setMessage(null);
          if (mismatch || tooShort) return;

          mutation.mutate({
            registration_ticket: state.ticket ?? '',
            full_name: fullName,
            password,
            // Spread rather than pass undefined: `exactOptionalPropertyTypes`
            // treats an explicit undefined as a type error.
            ...(phone.trim() ? { phone: phone.trim() } : {}),
            locale: normalizeLocale(i18n.resolvedLanguage),
          });
        }}
      >
        <Field
          label={t('register.fullName')}
          name="name"
          autoComplete="name"
          required
          minLength={2}
          value={fullName}
          onChange={(event) => {
            setFullName(event.target.value);
          }}
        />

        <Field
          label={t('register.phone')}
          hint={t('register.phoneHint')}
          name="tel"
          type="tel"
          autoComplete="tel"
          inputMode="tel"
          ltrValue
          value={phone}
          onChange={(event) => {
            setPhone(event.target.value);
          }}
        />

        <Field
          label={t('register.password')}
          hint={t('register.passwordHint', { count: MIN_PASSWORD_LENGTH })}
          type="password"
          name="new-password"
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
          label={t('register.passwordConfirm')}
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
          disabled={mismatch || tooShort || fullName.trim().length < 2}
        >
          {t('register.submit')}
        </Button>
      </form>
    </AuthLayout>
  );
}
