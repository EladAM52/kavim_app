import { DirectionProvider } from '@radix-ui/react-direction';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useMemo } from 'react';

import { AppShell } from '@/components/layout/AppShell';
import { SystemStatus } from '@/features/system/SystemStatus';
import { useDirection } from '@/hooks/useDirection';

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Plant-floor Wi-Fi is unreliable, so cached data is better than a
        // spinner and a refetch storm on every reconnect.
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: 2,
        refetchOnWindowFocus: false,
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
        <AppShell>
          <SystemStatus />
        </AppShell>
      </DirectionProvider>
    </QueryClientProvider>
  );
}
