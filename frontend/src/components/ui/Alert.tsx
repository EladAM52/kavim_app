import { cn } from '@/lib/cn';

type Tone = 'error' | 'warning' | 'success' | 'info';

interface AlertProps {
  tone?: Tone;
  title?: string;
  children: React.ReactNode;
  className?: string;
}

// border-s, not border-l: the accent sits on the leading edge, so it moves to the
// right in Hebrew and the left in English. Written physically it would read as a
// stray line in RTL (CLAUDE.md rule 1).
const TONES: Record<Tone, string> = {
  error: 'border-s-red-500 bg-red-50 text-red-900',
  warning: 'border-s-amber-400 bg-amber-50 text-amber-900',
  success: 'border-s-emerald-500 bg-emerald-50 text-emerald-900',
  info: 'border-s-brand-500 bg-brand-50 text-brand-900',
};

export function Alert({
  tone = 'info',
  title,
  children,
  className,
}: AlertProps): React.JSX.Element {
  return (
    <div
      // assertive for errors: a failed sign-in must interrupt, because the user is
      // waiting on it. Everything else is polite.
      role={tone === 'error' ? 'alert' : 'status'}
      className={cn('border-s-4 p-3 text-sm', TONES[tone], className)}
    >
      {title && <p className="mb-1 font-semibold">{title}</p>}
      {children}
    </div>
  );
}
