/**
 * The refresh path (SPEC §8.2).
 *
 * The single-flight guard is the property worth testing, and it is worth testing
 * precisely because getting it wrong is not a performance bug: six parallel
 * refreshes each rotate the token, five of them presenting a value the others
 * just spent, and the backend correctly reads that as replay and revokes the whole
 * family. The user gets logged out for loading a board.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError, refreshSession, request } from '@/api/client';
import { getAccessToken, setAccessToken, useAuthStore } from '@/stores/auth';

const TOKEN_RESPONSE = {
  access_token: 'fresh-access-token',
  token_type: 'bearer',
  expires_in_seconds: 900,
  user: {
    id: 'user-1',
    email: 'worker@example.com',
    full_name: 'Worker',
    locale: 'he',
    roles: ['WORKER'],
    permissions: ['task:read'],
  },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function problemResponse(status: number, code: string): Response {
  return new Response(JSON.stringify({ status, code, title: code, detail: code, type: code }), {
    status,
    headers: { 'Content-Type': 'application/problem+json' },
  });
}

/**
 * `fetch`'s first argument is a union, and `String()` on a `Request` produces
 * `[object Object]` — which would silently match nothing and make the assertions
 * below pass for the wrong reason.
 */
function urlOf(input: unknown): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.href;
  if (input instanceof Request) return input.url;
  return '';
}

describe('api client auth', () => {
  beforeEach(() => {
    setAccessToken(null);
    useAuthStore.setState({ status: 'unknown', user: null });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('attaches the bearer token from the in-memory store', async () => {
    setAccessToken('in-memory-token');
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await request('/projects');

    const headers = (fetchMock.mock.calls[0]?.[1] as RequestInit).headers as Headers;
    expect(headers.get('Authorization')).toBe('Bearer in-memory-token');
  });

  it('sends no Authorization header when there is no token', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await request('/projects');

    const headers = (fetchMock.mock.calls[0]?.[1] as RequestInit).headers as Headers;
    expect(headers.has('Authorization')).toBe(false);
  });

  it('refreshes once and replays the original request after a 401', async () => {
    setAccessToken('expired-token');
    const fetchMock = vi
      .fn()
      // 1. the original call fails
      .mockResolvedValueOnce(problemResponse(401, 'unauthenticated'))
      // 2. the refresh succeeds
      .mockResolvedValueOnce(jsonResponse(TOKEN_RESPONSE))
      // 3. the replay succeeds
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await request<{ ok: boolean }>('/projects');

    expect(result).toEqual({ ok: true });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(getAccessToken()).toBe('fresh-access-token');
    expect(useAuthStore.getState().status).toBe('authenticated');

    // The replay carried the *new* token, not the expired one.
    const replayHeaders = (fetchMock.mock.calls[2]?.[1] as RequestInit).headers as Headers;
    expect(replayHeaders.get('Authorization')).toBe('Bearer fresh-access-token');
  });

  it('issues exactly one refresh for many concurrent 401s', async () => {
    setAccessToken('expired-token');

    // Each path 401s on its first call and succeeds afterwards, mimicking a token
    // that expired while several queries were in flight.
    const attempts = new Map<string, number>();

    const fetchMock = vi.fn((input: string | URL | Request): Promise<Response> => {
      const url = urlOf(input);

      if (url.includes('/auth/refresh')) {
        // Deliberately slow, so every caller is still waiting when it resolves. An
        // instant response would let them serialise and hide a missing guard.
        return new Promise((resolve) => {
          setTimeout(() => {
            resolve(jsonResponse(TOKEN_RESPONSE));
          }, 20);
        });
      }

      const count = (attempts.get(url) ?? 0) + 1;
      attempts.set(url, count);
      return Promise.resolve(
        count === 1 ? problemResponse(401, 'unauthenticated') : jsonResponse({ ok: true }),
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    await Promise.all([
      request('/a'),
      request('/b'),
      request('/c'),
      request('/d'),
      request('/e'),
      request('/f'),
    ]);

    const refreshCalls = fetchMock.mock.calls.filter(([input]) =>
      urlOf(input).includes('/auth/refresh'),
    );
    expect(refreshCalls).toHaveLength(1);
  });

  it('gives up and marks the session anonymous when the refresh fails', async () => {
    setAccessToken('expired-token');
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(problemResponse(401, 'unauthenticated'))
      .mockResolvedValueOnce(problemResponse(401, 'unauthenticated'));
    vi.stubGlobal('fetch', fetchMock);

    await expect(request('/projects')).rejects.toBeInstanceOf(ApiError);

    expect(useAuthStore.getState().status).toBe('anonymous');
    expect(getAccessToken()).toBeNull();
    // Original + refresh. No replay, because there is nothing to replay with.
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not attempt a refresh when login itself returns 401', async () => {
    const fetchMock = vi.fn().mockResolvedValue(problemResponse(401, 'unauthenticated'));
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      request('/auth/login', { method: 'POST', body: { email: 'a@b.co', password: 'wrong' } }),
    ).rejects.toBeInstanceOf(ApiError);

    // Exactly one call. Refreshing after a wrong password would retry the login
    // and spend two of the ten attempts before lockout for one user mistake.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('allows a later 401 to start a fresh refresh', async () => {
    // The in-flight promise is cleared in `finally`; caching a settled failure
    // would break refresh for the rest of the tab's life.
    setAccessToken('expired-token');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(problemResponse(401, 'unauthenticated')));
    expect(await refreshSession()).toBeNull();

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(TOKEN_RESPONSE)));
    expect(await refreshSession()).toBe('fresh-access-token');
  });

  it('does not refresh on a 403, only on a 401', async () => {
    setAccessToken('valid-token');
    const fetchMock = vi.fn().mockResolvedValue(problemResponse(403, 'permission_denied'));
    vi.stubGlobal('fetch', fetchMock);

    await expect(request('/projects')).rejects.toMatchObject({ code: 'permission_denied' });

    // A 403 means the token is fine and the permission is not. Refreshing would
    // return the same permissions and the same 403.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
