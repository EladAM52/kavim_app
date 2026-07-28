import { Outlet } from 'react-router-dom';

import { AppShell } from '@/components/layout/AppShell';

/**
 * The signed-in frame as a layout route.
 *
 * A layout route rather than wrapping each page in `<AppShell>`: the shell holds
 * the nav, the language toggle, and the user menu, and remounting it on every
 * navigation would close an open menu and re-run the breakpoint listener.
 */
export function ShellLayout(): React.JSX.Element {
  return (
    <AppShell>
      <Outlet />
    </AppShell>
  );
}
