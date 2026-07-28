/**
 * Table primitives.
 *
 * Not a data-grid component with a `columns` prop: the four admin screens want
 * different cells, different empty states, and one of them (RoleMatrix) is a
 * two-dimensional grid with a sticky header row *and* a sticky leading column.
 * A configuration-driven table would have to grow an option for each of those,
 * so these stay as plain elements with the RTL-safe defaults baked in.
 *
 * The scroll container is the load-bearing part. A wide table inside an RTL
 * document overflows towards the *left*, and without an explicit
 * `overflow-x-auto` wrapper it pushes the whole page sideways instead of
 * scrolling itself.
 */

import { cn } from '@/lib/cn';

interface TableProps {
  children: React.ReactNode;
  /** Announced to screen readers; visually hidden. */
  caption?: string;
  className?: string;
}

export function Table({ children, caption, className }: TableProps): React.JSX.Element {
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
      <table className={cn('w-full border-collapse text-sm', className)}>
        {caption && <caption className="sr-only">{caption}</caption>}
        {children}
      </table>
    </div>
  );
}

type CellProps = React.ThHTMLAttributes<HTMLTableCellElement>;

/**
 * `text-start`, never `text-left` — the column headings have to follow the
 * document direction (CLAUDE.md rule 1).
 */
export function Th({ className, scope = 'col', ...props }: CellProps): React.JSX.Element {
  return (
    <th
      scope={scope}
      className={cn(
        'border-b border-slate-200 bg-slate-50 px-3 py-2 text-start',
        'text-xs font-semibold tracking-wide text-slate-600 uppercase',
        className,
      )}
      {...props}
    />
  );
}

export function Td({
  className,
  ...props
}: React.TdHTMLAttributes<HTMLTableCellElement>): React.JSX.Element {
  return (
    <td
      className={cn('border-b border-slate-100 px-3 py-2 text-start align-middle', className)}
      {...props}
    />
  );
}

export function Tr({
  className,
  ...props
}: React.HTMLAttributes<HTMLTableRowElement>): React.JSX.Element {
  return <tr className={cn('hover:bg-slate-50/70', className)} {...props} />;
}

interface EmptyRowProps {
  colSpan: number;
  children: React.ReactNode;
}

/** One place for "nothing matched", so every table says it the same way. */
export function EmptyRow({ colSpan, children }: EmptyRowProps): React.JSX.Element {
  return (
    <tr>
      <td colSpan={colSpan} className="px-3 py-10 text-center text-sm text-slate-500">
        {children}
      </td>
    </tr>
  );
}
