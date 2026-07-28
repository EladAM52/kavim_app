import { useEffect, useState } from 'react';

/**
 * Trailing debounce for a filter value.
 *
 * The search box on the user table is behind `user:manage`, so every keystroke
 * that reaches the server costs a permission resolution and a filtered scan.
 * 300ms is short enough to feel immediate and long enough that typing an eight
 * letter name is one request rather than eight.
 */
export function useDebounced<T>(value: T, delayMs = 300): T {
  const [settled, setSettled] = useState(value);

  useEffect(() => {
    const timer = setTimeout(() => {
      setSettled(value);
    }, delayMs);
    return (): void => {
      clearTimeout(timer);
    };
  }, [value, delayMs]);

  return settled;
}
