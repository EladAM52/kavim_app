import { useId } from 'react';

import { cn } from '@/lib/cn';
import { LTR_EMBED_CLASS } from '@/lib/rtl';

interface FieldProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'id'> {
  label: string;
  /** Shown below the input and wired up via `aria-describedby`. */
  error?: string | undefined;
  hint?: string | undefined;
  /**
   * Keep the value left-to-right inside an RTL form.
   *
   * Emails, phone numbers, and codes are LTR sequences. Rendered RTL they read
   * back in the wrong order, which for a verification code means the user types
   * what they see and it is wrong.
   */
  ltrValue?: boolean;
}

export function Field({
  label,
  error,
  hint,
  ltrValue = false,
  className,
  required,
  ...props
}: FieldProps): React.JSX.Element {
  const id = useId();
  const errorId = `${id}-error`;
  const hintId = `${id}-hint`;

  const describedBy = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ');

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-medium text-slate-800">
        {label}
        {required === true && (
          // The asterisk is decorative; `required` on the input is what assistive
          // technology actually reports.
          <span aria-hidden="true" className="text-red-600">
            {' *'}
          </span>
        )}
      </label>

      <input
        id={id}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        dir={ltrValue ? 'ltr' : undefined}
        className={cn(
          'touch-target rounded-lg border bg-white px-3 text-base',
          // text-base, not text-sm: iOS Safari zooms the viewport on focus for
          // anything under 16px, which on a phone throws the layout sideways.
          'focus:outline-brand-600 focus:outline-2 focus:outline-offset-0',
          error ? 'border-red-500' : 'border-slate-300',
          ltrValue && LTR_EMBED_CLASS,
          className,
        )}
        {...props}
      />

      {hint && !error && (
        <p id={hintId} className="text-xs text-slate-500">
          {hint}
        </p>
      )}
      {error && (
        // role="alert" so a validation failure is announced, not just coloured.
        <p id={errorId} role="alert" className="text-xs font-medium text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
