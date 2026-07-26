import { useEffect, useState } from 'react';

/**
 * Viewport class, used to choose a layout rather than to tweak one.
 *
 * Below `md` the board grid is replaced by the card list — a different
 * component, not a narrower table (SPEC §10.4). That decision needs to be made
 * in JavaScript, because rendering both and hiding one would mean fetching and
 * virtualizing rows a phone will never display.
 */

const BREAKPOINTS = {
  sm: 640,
  md: 768,
  lg: 1024,
  xl: 1280,
} as const;

export type Breakpoint = 'mobile' | 'tablet' | 'desktop' | 'wide';

function classify(width: number): Breakpoint {
  if (width < BREAKPOINTS.md) return 'mobile';
  if (width < BREAKPOINTS.lg) return 'tablet';
  if (width < BREAKPOINTS.xl) return 'desktop';
  return 'wide';
}

interface UseBreakpointResult {
  breakpoint: Breakpoint;
  isMobile: boolean;
  isTablet: boolean;
  isDesktop: boolean;
  /** True below `lg` — the boundary for touch-first affordances. */
  isTouchLayout: boolean;
}

export function useBreakpoint(): UseBreakpointResult {
  const [breakpoint, setBreakpoint] = useState<Breakpoint>(() =>
    typeof window === 'undefined' ? 'desktop' : classify(window.innerWidth),
  );

  useEffect(() => {
    // matchMedia rather than a resize listener: it fires only when a boundary is
    // actually crossed, instead of on every pixel of a drag.
    const query = window.matchMedia(
      `(min-width: ${BREAKPOINTS.md}px), (min-width: ${BREAKPOINTS.lg}px), (min-width: ${BREAKPOINTS.xl}px)`,
    );
    const update = (): void => {
      setBreakpoint(classify(window.innerWidth));
    };
    update();
    query.addEventListener('change', update);
    window.addEventListener('orientationchange', update);
    return () => {
      query.removeEventListener('change', update);
      window.removeEventListener('orientationchange', update);
    };
  }, []);

  return {
    breakpoint,
    isMobile: breakpoint === 'mobile',
    isTablet: breakpoint === 'tablet',
    isDesktop: breakpoint === 'desktop' || breakpoint === 'wide',
    isTouchLayout: breakpoint === 'mobile' || breakpoint === 'tablet',
  };
}
