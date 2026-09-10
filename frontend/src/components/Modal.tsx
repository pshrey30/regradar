import { useEffect, useRef, type ReactNode } from 'react'

export interface ModalProps {
  isOpen: boolean
  onClose: () => void
  title?: string
  children: ReactNode
  // FE-06: the webhook-secret reveal step must not be dismissable by
  // backdrop click or Escape — only an explicit acknowledgment button
  // (rendered by the caller, inside children) may close it, since the
  // secret is never shown again after this modal closes.
  dismissable?: boolean
}

const _FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

export function Modal({ isOpen, onClose, title, children, dismissable = true }: ModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const previouslyFocusedRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!isOpen || !dismissable) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose, dismissable])

  // Focus management: move focus into the dialog on open (so keyboard and
  // screen-reader users land inside it rather than on whatever was behind
  // it), restore it to the trigger element on close, and keep Tab from
  // escaping to the page behind the backdrop while the dialog is open.
  useEffect(() => {
    if (!isOpen) return
    previouslyFocusedRef.current = document.activeElement as HTMLElement | null

    const dialog = dialogRef.current
    const firstFocusable = dialog?.querySelector<HTMLElement>(_FOCUSABLE_SELECTOR)
    ;(firstFocusable ?? dialog)?.focus()

    function handleTab(event: KeyboardEvent) {
      if (event.key !== 'Tab' || !dialog) return
      const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(_FOCUSABLE_SELECTOR))
      if (focusable.length === 0) {
        event.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleTab)

    return () => {
      document.removeEventListener('keydown', handleTab)
      previouslyFocusedRef.current?.focus()
    }
  }, [isOpen])

  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      role="presentation"
      onClick={dismissable ? onClose : undefined}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? 'modal-title' : undefined}
        tabIndex={-1}
        className="w-full max-w-md rounded-lg bg-white p-6 shadow-lg outline-none"
        onClick={(event) => event.stopPropagation()}
      >
        {title && (
          <h2 id="modal-title" className="mb-4 text-lg font-semibold text-slate-900">
            {title}
          </h2>
        )}
        {children}
      </div>
    </div>
  )
}
