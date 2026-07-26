/**
 * HTTP client.
 *
 * One place that knows about base URLs, headers, and the RFC 7807 error shape
 * the backend returns (SPEC §9.1). Auth token handling and refresh-on-401 land
 * in Phase 2 at the marked seam.
 */

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
}

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

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, ifMatch, idempotencyKey, locale, headers, ...init } = options;

  const finalHeaders = new Headers(headers);
  finalHeaders.set('Accept', 'application/json');
  if (locale) finalHeaders.set('Accept-Language', locale);
  if (ifMatch !== undefined) finalHeaders.set('If-Match', String(ifMatch));
  if (idempotencyKey) finalHeaders.set('Idempotency-Key', idempotencyKey);
  if (body !== undefined && !(body instanceof FormData)) {
    finalHeaders.set('Content-Type', 'application/json');
  }

  // ── Phase 2 seam ──────────────────────────────────────────────────────
  // Attach `Authorization: Bearer <access token from the in-memory store>`
  // here, and wrap the call below in refresh-on-401 with a single-flight
  // guard so concurrent 401s trigger exactly one refresh.

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
