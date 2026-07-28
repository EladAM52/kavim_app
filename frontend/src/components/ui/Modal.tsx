/**
 * Modal dialog.
 *
 * Hand-rolled rather than `<dialog showModal()>`: jsdom does not implement
 * `showModal`, so every component test touching a dialog would have to be an
 * e2e test instead. The behaviours the native element gives for free are
 * reimplemented here and each one is load-bearing:
 *
 *   * **Escape closes.** A confirmation the keyboard cannot dismiss is a trap.
 *   * **Focus is trapped.** Tabbing out of an open modal lands on controls the
 *     overlay is covering, which a screen-reader user cannot see is inert.
 *   * **Focus returns.** The trigger regains focus on close, so a keyboard user
 *     does not restart from the top of the document.
 *   * **The page behind does not scroll.** On iOS a scrolling background under a
 *     fixed overlay drags the modal off screen.
 */

import { useCallback, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/cn';

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: React.ReactNode;
  /** Action row. Rendered on the trailing edge, so it mirrors with direction. */
  footer?: React.ReactNode;
  className?: string;
}

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  className,
}: ModalProps): React.JSX.Element | null {
  const { t } = useTranslation();
  const panelRef = useRef<HTMLDivElement>(null);
  const restoreTo = useRef<HTMLElement | null>(null);

  const focusables = useCallback((): HTMLElement[] => {
    const panel = panelRef.current;
    if (!panel) return [];
    return Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
  }, []);

  useEffect(() => {
    if (!open) return;

    restoreTo.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    // The panel itself is focused rather than its first control: for a
    // destructive confirmation, landing on "confirm" is how a stray Enter
    // deletes something.
    panelRef.current?.focus();

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
        return;
      }

      if (event.key !== 'Tab') return;

      const items = focusables();
      if (items.length === 0) {
        event.preventDefault();
        return;
      }

      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) return;

      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === panelRef.current)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown, true);

    return (): void => {
      document.removeEventListener('keydown', onKeyDown, true);
      document.body.style.overflow = previousOverflow;
      restoreTo.current?.focus();
    };
  }, [open, onClose, focusables]);

  if (!open) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-4">
      {/* The backdrop is a button so a pointer click closes and so it is not an
          invisible click target for keyboard users — it is aria-hidden and out
          of the tab order, because Escape is the keyboard route out. */}
      <button
        type="button"
        aria-hidden="true"
        tabIndex={-1}
        className="absolute inset-0 bg-slate-900/40"
        onClick={onClose}
      />

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        aria-describedby={description ? 'modal-description' : undefined}
        tabIndex={-1}
        className={cn(
          'relative flex max-h-[90dvh] w-full flex-col rounded-t-2xl bg-white shadow-xl',
          'sm:max-w-lg sm:rounded-2xl',
          className,
        )}
      >
        <div className="flex items-start gap-3 border-b border-slate-200 px-4 py-3">
          <h2 className="text-base font-semibold text-slate-900">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('actions.close')}
            className="touch-target ms-auto -me-2 -mt-2 rounded-lg text-slate-500 hover:bg-slate-100"
          >
            <span aria-hidden="true" className="text-xl leading-none">
              ×
            </span>
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4">
          {description && (
            <p id="modal-description" className="mb-3 text-sm text-slate-600">
              {description}
            </p>
          )}
          {children}
        </div>

        {footer && (
          <div className="flex flex-wrap justify-end gap-2 border-t border-slate-200 px-4 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
