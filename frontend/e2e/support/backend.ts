/**
 * The seam between the browser tests and the real backend.
 *
 * Two things an end-to-end auth test cannot get from the UI alone: an invitation
 * to start from, and the verification code that was emailed. Both come from the
 * `invite` CLI, which refuses to run in production and calls the same
 * `invite_user` that `POST /admin/invitations` calls — so the test starts from a
 * genuine invitation rather than a fixture that can drift from the real one.
 *
 * The alternative was a second database driver in the frontend toolchain, or a
 * test-only endpoint in the application. Both are worse: one duplicates the
 * schema, the other ships a back door.
 */

import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);

const BACKEND_DIR = new URL('../../../backend', import.meta.url).pathname.replace(
  /^\/([A-Za-z]:)/,
  '$1',
);

async function invite(args: string[]): Promise<string> {
  const { stdout } = await run('uv', ['run', 'python', '-m', 'app.scripts.invite', ...args], {
    cwd: BACKEND_DIR,
    // Structlog writes human-readable lines to stdout alongside our JSON, so the
    // parser below takes the last line rather than the whole buffer.
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
    windowsHide: true,
  });
  return stdout;
}

/**
 * The last JSON object in the CLI's output.
 *
 * Structlog writes human-readable lines to the same stdout, so the payload is
 * the last line rather than the whole buffer. Scanning backwards also means a
 * future log line appended after the JSON would not silently break this.
 */
function lastJsonLine(stdout: string): Record<string, unknown> {
  const lines = stdout.trim().split(/\r?\n/);
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    const line = (lines[i] ?? '').trim();
    if (line.startsWith('{')) return JSON.parse(line) as Record<string, unknown>;
  }
  throw new Error(`no JSON in CLI output:\n${stdout}`);
}

export interface Invitation {
  id: string;
  email: string;
  token: string;
  url: string;
}

/** A fresh invitation, and the raw token the emailed link would carry. */
export async function createInvitation(email: string, role = 'WORKER'): Promise<Invitation> {
  return lastJsonLine(await invite([email, '--role', role, '--json'])) as unknown as Invitation;
}

/**
 * The code most recently queued for an address.
 *
 * Read from the outbox payload, because `otp_codes` stores only a digest — the
 * plaintext exists exactly once, in the message. This is the test standing in
 * for a mailbox.
 */
export async function latestOtp(email: string): Promise<string> {
  const { code } = lastJsonLine(await invite(['--otp', email]));
  if (typeof code !== 'string') throw new Error(`no code returned for ${email}`);
  return code;
}

/** A unique address per test run, so reruns never collide on a live invitation. */
export function uniqueEmail(prefix: string): string {
  const suffix = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${suffix}@example.com`;
}

export const TEST_PASSWORD = 'a-long-enough-passphrase';
