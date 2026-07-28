import { cn } from '@/lib/cn';

interface ToggleProps {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** Accessible name. Required — a bare switch says nothing to a screen reader. */
  label: string;
  /** Render the label as visible text beside the switch. */
  showLabel?: boolean;
  disabled?: boolean;
  className?: string;
}

/**
 * A switch, not a checkbox.
 *
 * `role="switch"` with `aria-checked` because the RoleMatrix reads its state out
 * loud thirty times a screen, and "switch, on" is shorter and less ambiguous than
 * "checkbox, checked" when the label is a permission key.
 *
 * The knob moves with `translate-x` under an explicit `dir`-aware sign rather
 * than a physical class: in RTL the track is mirrored, so a hardcoded positive
 * translation would push the knob *out* of the track. `rtl:-translate-x-full` is
 * Tailwind's direction variant, which is a logical construct even though the
 * property it sets is not.
 */
export function Toggle({
  checked,
  onChange,
  label,
  showLabel = false,
  disabled = false,
  className,
}: ToggleProps): React.JSX.Element {
  const control = (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={showLabel ? undefined : label}
      disabled={disabled}
      onClick={() => {
        onChange(!checked);
      }}
      className={cn(
        'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors',
        'focus-visible:outline-brand-600 focus-visible:outline-2 focus-visible:outline-offset-2',
        checked ? 'bg-brand-600' : 'bg-slate-300',
        disabled && 'cursor-not-allowed opacity-50',
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'inline-block size-5 rounded-full bg-white shadow transition-transform',
          // ms-0.5 keeps the knob off the leading edge in both directions.
          'ms-0.5',
          checked && 'translate-x-5 rtl:-translate-x-5',
        )}
      />
    </button>
  );

  if (!showLabel) {
    return control;
  }

  return (
    <label className="inline-flex items-center gap-2 text-sm text-slate-800">
      {control}
      <span>{label}</span>
    </label>
  );
}
