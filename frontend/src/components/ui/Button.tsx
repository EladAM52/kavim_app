import { cn } from '@/lib/cn';

type Variant = 'primary' | 'secondary' | 'ghost';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  /** Renders a spinner and disables the button. */
  loading?: boolean;
  fullWidth?: boolean;
}

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-brand-700 text-white hover:bg-brand-800 active:bg-brand-900',
  secondary: 'border border-slate-300 bg-white text-slate-800 hover:bg-slate-50',
  ghost: 'text-brand-700 hover:bg-brand-50',
};

/**
 * The one button.
 *
 * `loading` also sets `disabled`, because a submit button that still accepts
 * clicks while a request is in flight is how duplicate registrations happen — and
 * on a plant-floor phone the request can take seconds.
 */
export function Button({
  variant = 'primary',
  loading = false,
  fullWidth = false,
  className,
  disabled,
  children,
  ...props
}: ButtonProps): React.JSX.Element {
  return (
    <button
      // Explicit, because a button inside a form defaults to `submit` and that
      // has surprised everyone at least once.
      type={props.type ?? 'button'}
      disabled={disabled === true || loading}
      aria-busy={loading}
      className={cn(
        'touch-target inline-flex items-center justify-center gap-2 rounded-lg px-4',
        'text-sm font-semibold transition-colors',
        'disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        fullWidth && 'w-full',
        className,
      )}
      {...props}
    >
      {loading && (
        <span
          aria-hidden="true"
          className="size-4 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent"
        />
      )}
      {children}
    </button>
  );
}
