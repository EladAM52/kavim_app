import { cn } from '@/lib/cn';
import { LTR_EMBED_CLASS } from '@/lib/rtl';

interface LtrProps {
  children: React.ReactNode;
  className?: string;
}

/**
 * An LTR island inside RTL text.
 *
 * Email addresses, timestamps, IP addresses, and permission keys are all
 * left-to-right sequences. Rendered inside a Hebrew paragraph without isolation
 * the bidi algorithm reorders their punctuation — `12/07` shows as `07/12`, and
 * `user:manage` picks up the surrounding direction at its colon.
 */
export function Ltr({ children, className }: LtrProps): React.JSX.Element {
  return (
    <span dir="ltr" className={cn(LTR_EMBED_CLASS, 'inline-block', className)}>
      {children}
    </span>
  );
}
