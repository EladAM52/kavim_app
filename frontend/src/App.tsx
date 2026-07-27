import { DirectionProvider } from '@radix-ui/react-direction';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useMemo } from 'react';
import { RouterProvider } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { useDirection } from '@/hooks/useDirection';
import { router } from '@/router';

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Plant-floor Wi-Fi is unreliable, so cached data is better than a
        // spinner and a refetch storm on every reconnect.
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          // Never retry a client error. A 401 is handled by the refresh path in
          // `api/client.ts`, and retrying a 410 or a 422 just repeats a settled
          // answer — on the auth endpoints it also spends the rate-limit budget.
          if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
            return false;
          }
          return failureCount < 2;
        },
      },
    },
  });
}

export default function App(): React.JSX.Element {
  const { direction } = useDirection();
  const queryClient = useMemo(createQueryClient, []);

  return (
    <QueryClientProvider client={queryClient}>
      {/* Radix primitives read direction from context, not from the DOM, so
          dropdowns, popovers, and sliders need this to flip correctly. */}
      <DirectionProvider dir={direction}>
        <RouterProvider router={router} />
      </DirectionProvider>
    </QueryClientProvider>
  );
}
