import { useId } from 'react';

import { cn } from '@/lib/cn';

export interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps extends Omit<React.SelectHTMLAttributes<HTMLSelectElement>, 'id'> {
  label: string;
  options: readonly SelectOption[];
  /** Leading entry for "no filter". Omit for a required choice. */
  placeholder?: string;
  error?: string | undefined;
  /** Hide the label visually but keep it for assistive technology. */
  labelHidden?: boolean;
}

/**
 * A native `<select>`, deliberately.
 *
 * A custom listbox would need its own RTL handling, its own focus management,
 * and its own touch behaviour, and it would still be worse on the plant-floor
 * phones this has to work on — Android renders the native picker full screen
 * with 48px rows, which is exactly what a gloved hand needs.
 */
export function Select({
  label,
  options,
  placeholder,
  error,
  labelHidden = false,
  className,
  ...props
}: SelectProps): React.JSX.Element {
  const id = useId();
  const errorId = `${id}-error`;

  return (
    <div className="flex flex-col gap-1.5">
      <label
        htmlFor={id}
        className={cn('text-sm font-medium text-slate-800', labelHidden && 'sr-only')}
      >
        {label}
      </label>

      <select
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
        className={cn(
          'touch-target rounded-lg border bg-white px-3 text-base',
          'focus:outline-brand-600 focus:outline-2 focus:outline-offset-0',
          error ? 'border-red-500' : 'border-slate-300',
          className,
        )}
        {...props}
      >
        {placeholder !== undefined && <option value="">{placeholder}</option>}
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>

      {error && (
        <p id={errorId} role="alert" className="text-xs font-medium text-red-700">
          {error}
        </p>
      )}
    </div>
  );
}
