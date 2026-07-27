/**
 * Placeholder while a lazy route chunk downloads.
 *
 * Blank rather than a spinner: from cache the chunk usually arrives in
 * milliseconds, and a spinner that appears and vanishes reads as jank. On a slow
 * plant-floor connection — the case where feedback actually helps — it renders
 * long enough to matter, and the route's own loading state takes over from there.
 *
 * Lives in its own file so `router.tsx` exports only the router, which is what
 * keeps Fast Refresh working.
 */
export function ChunkFallback(): React.JSX.Element {
  return <div className="min-h-dvh" aria-busy="true" />;
}
