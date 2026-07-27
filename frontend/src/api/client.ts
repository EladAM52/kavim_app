/**
 * HTTP client.
 *
 * One place that knows about base URLs, headers, the RFC 7807 error shape the
 * backend returns (SPEC §9.1), and session refresh.
 *
 * **The single-flight refresh is the subtle part.** A board screen can fire six
 * queries at once. When an access token expires they all get a 401 together, and
 * six parallel calls to `/auth/refresh` would each rotate the token — five of
 * them presenting a value the others just invalidated. The backend reads that as
 * token replay and revokes the entire family (SPEC §8.2), logging the user out
 * for doing nothing wrong. So the first 401 starts a refresh and every other
 * waiter awaits the same promise.
 */

import type { components } from '@/api/generated/types';
import { getAccessToken, setAccessToken, useAuthStore } from '@/stores/auth';

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1';

/** Field-level validation detail from a 422. */
export interface ApiFieldError {
  field: string;
  message: string;
  type?: string;
}

/** The backend's problem+json body. */
export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  instance?: string;
  code: string;
  errors?: ApiFieldError[];
  request_id?: string;
  current_version?: number;
  current_value?: unknown;
  /** Seconds until an `account_locked` lock lifts (SPEC §8.3). */
  retry_after_seconds?: number;
}

export class ApiError extends Error {
  readonly status: number;
  /** Stable machine code — use this for branching, never the message. */
  readonly code: string;
  readonly fieldErrors: ApiFieldError[];
  readonly requestId: string | undefined;
  readonly problem: ProblemDetail | undefined;

  constructor(
    status: number,
    code: string,
    message: string,
    options: { fieldErrors?: ApiFieldError[]; requestId?: string; problem?: ProblemDetail } = {},
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.fieldErrors = options.fieldErrors ?? [];
    this.requestId = options.requestId;
    this.problem = options.problem;
  }

  /** Key into the `errors` i18n namespace. Never show `message` to a user. */
  get translationKey(): string {
    return `errors:${this.code}`;
  }

  get isNetworkError(): boolean {
    return this.status === 0;
  }

  /** 409 on a cell write — the caller offers "keep mine / take theirs". */
  get isVersionConflict(): boolean {
    return this.code === 'version_conflict';
  }
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown;
  /** Optimistic concurrency version for cell writes (SPEC §9.1). */
  ifMatch?: string | number;
  /** Makes a retried POST safe after a network drop. */
  idempotencyKey?: string;
  locale?: string;
  signal?: AbortSignal;
  /**
   * Send no `Authorization` header and never attempt a refresh.
   *
   * For the endpoints that establish a session in the first place. Without it,
   * a failed login would trigger a refresh attempt and then retry the login —
   * turning one wrong password into two, which burns the rate-limit budget at
   * double speed.
   */
  anonymous?: boolean;
}

/** Endpoints that must never trigger the refresh-and-retry path. */
const AUTH_ENTRY_PATHS = [
  '/auth/login',
  '/auth/refresh',
  '/auth/register',
  '/auth/otp/',
  '/auth/invitations/',
  '/auth/password-reset/',
];

const isAuthEntryPath = (path: string): boolean =>
  AUTH_ENTRY_PATHS.some((entry) => path.startsWith(entry));

async function parseProblem(response: Response): Promise<ApiError> {
  let problem: ProblemDetail | undefined;
  try {
    const parsed: unknown = await response.json();
    if (parsed && typeof parsed === 'object' && 'code' in parsed) {
      problem = parsed as ProblemDetail;
    }
  } catch {
    // Not JSON — a proxy error page or an empty body.
  }

  return new ApiError(
    response.status,
    problem?.code ?? `http_${response.status}`,
    problem?.detail ?? response.statusText,
    {
      ...(problem?.errors ? { fieldErrors: problem.errors } : {}),
      ...(problem?.request_id ? { requestId: problem.request_id } : {}),
      ...(problem ? { problem } : {}),
    },
  );
}

/**
 * The in-flight refresh, or `null`.
 *
 * Module scope, so every caller in the tab shares one. Resolves to the new access
 * token, or `null` when the session is genuinely over.
 */
let refreshInFlight: Promise<string | null> | null = null;

/**
 * Exchange the httpOnly cookie for a new access token.
 *
 * Concurrent callers get the same promise — see the module docstring for why that
 * matters rather than being a nicety.
 */
export function refreshSession(): Promise<string | null> {
  refreshInFlight ??= (async (): Promise<string | null> => {
    try {
      const refreshed = await rawRequest<TokenResponse>('/auth/refresh', {
        method: 'POST',
        anonymous: true,
      });
      useAuthStore.getState().signIn(refreshed.access_token, refreshed.user);
      return refreshed.access_token;
    } catch {
      // Any failure means no usable session: an expired cookie, a revoked family,
      // or the server being unreachable. All three end the same way.
      useAuthStore.getState().markAnonymous();
      return null;
    } finally {
      // Cleared in `finally` so a later 401 can start a fresh attempt. Leaving a
      // settled promise here would cache the failure for the tab's lifetime.
      refreshInFlight = null;
    }
  })();

  return refreshInFlight;
}

/** One HTTP round trip. No refresh handling — that lives in `request`. */
async function rawRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, ifMatch, idempotencyKey, locale, anonymous, headers, ...init } = options;

  const finalHeaders = new Headers(headers);
  finalHeaders.set('Accept', 'application/json');
  if (locale) finalHeaders.set('Accept-Language', locale);
  if (ifMatch !== undefined) finalHeaders.set('If-Match', String(ifMatch));
  if (idempotencyKey) finalHeaders.set('Idempotency-Key', idempotencyKey);
  if (body !== undefined && !(body instanceof FormData)) {
    finalHeaders.set('Content-Type', 'application/json');
  }

  const token = getAccessToken();
  if (!anonymous && token) {
    finalHeaders.set('Authorization', `Bearer ${token}`);
  }

  const url = path.startsWith('http') ? path : `${API_BASE}${path}`;

  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: finalHeaders,
      // Required for the refresh-token cookie.
      credentials: 'include',
      body: body === undefined ? null : body instanceof FormData ? body : JSON.stringify(body),
    });
  } catch {
    // fetch rejects only on a network-level failure, never on a 4xx/5xx.
    throw new ApiError(0, 'network', 'Network request failed');
  }

  if (!response.ok) {
    throw await parseProblem(response);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const skipRefresh = options.anonymous === true || isAuthEntryPath(path);

  try {
    return await rawRequest<T>(path, options);
  } catch (error) {
    if (!(error instanceof ApiError) || error.status !== 401 || skipRefresh) {
      throw error;
    }

    const token = await refreshSession();
    if (token === null) {
      throw error;
    }

    // Retried exactly once. A second 401 after a *successful* refresh is not a
    // token problem, so looping would just hammer the endpoint.
    setAccessToken(token);
    return await rawRequest<T>(path, options);
  }
}

export const api = {
  get: <T>(path: string, options?: RequestOptions): Promise<T> =>
    request<T>(path, { ...options, method: 'GET' }),
  post: <T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> =>
    request<T>(path, { ...options, method: 'POST', body }),
  patch: <T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> =>
    request<T>(path, { ...options, method: 'PATCH', body }),
  put: <T>(path: string, body?: unknown, options?: RequestOptions): Promise<T> =>
    request<T>(path, { ...options, method: 'PUT', body }),
  delete: <T>(path: string, options?: RequestOptions): Promise<T> =>
    request<T>(path, { ...options, method: 'DELETE' }),
};

// ── health (Phase 0) ──────────────────────────────────────────────────────
export interface HealthReady {
  status: 'ready' | 'not_ready';
  version: string;
  environment: string;
  checks: { database: 'ok' | 'unreachable'; redis: 'ok' | 'unreachable' };
}

export interface ApiMeta {
  name: string;
  version: string;
  api_version: string;
  locales: string[];
  default_locale: string;
  timezone: string;
}

/**
 * Readiness, including the degraded case.
 *
 * Two reasons this bypasses `request()`:
 *   1. `/health/*` sits at the origin root, not under `/api/v1`.
 *   2. A 503 body is the *useful* one — it names which dependency is down — so
 *      it must be returned, not thrown away as an error.
 */
export async function fetchHealth(): Promise<HealthReady> {
  let response: Response;
  try {
    response = await fetch('/health/ready', {
      headers: { Accept: 'application/json' },
    });
  } catch {
    throw new ApiError(0, 'network', 'Cannot reach the API server');
  }

  if (response.status !== 200 && response.status !== 503) {
    throw await parseProblem(response);
  }
  return (await response.json()) as HealthReady;
}

export const fetchApiMeta = (): Promise<ApiMeta> => api.get<ApiMeta>('/');

// ── auth (Phase 2) ────────────────────────────────────────────────────────
// Types come from the generated OpenAPI schema, so a backend field rename is a
// compile error here rather than an undefined at runtime.
export type TokenResponse = components['schemas']['TokenResponse'];
export type InvitationPreview = components['schemas']['InvitationPreview'];
export type RegistrationTicket = components['schemas']['RegistrationTicket'];
export type RegisterRequest = components['schemas']['RegisterRequest'];
export type LoginRequest = components['schemas']['LoginRequest'];

export const authApi = {
  /** `410` when spent or expired, `404` when the token is unknown (FR-102). */
  readInvitation: (token: string): Promise<InvitationPreview> =>
    api.get<InvitationPreview>(`/auth/invitations/${encodeURIComponent(token)}`, {
      anonymous: true,
    }),

  requestOtp: (token: string): Promise<unknown> =>
    api.post<unknown>('/auth/otp/request', { token }, { anonymous: true }),

  verifyOtp: (token: string, code: string): Promise<RegistrationTicket> =>
    api.post<RegistrationTicket>('/auth/otp/verify', { token, code }, { anonymous: true }),

  register: (payload: RegisterRequest): Promise<TokenResponse> =>
    api.post<TokenResponse>('/auth/register', payload, { anonymous: true }),

  login: (payload: LoginRequest): Promise<TokenResponse> =>
    api.post<TokenResponse>('/auth/login', payload, { anonymous: true }),

  logout: (): Promise<unknown> => api.post<unknown>('/auth/logout', undefined, {}),

  requestPasswordReset: (email: string): Promise<unknown> =>
    api.post<unknown>('/auth/password-reset/request', { email }, { anonymous: true }),

  confirmPasswordReset: (token: string, password: string): Promise<unknown> =>
    api.post<unknown>('/auth/password-reset/confirm', { token, password }, { anonymous: true }),
};
