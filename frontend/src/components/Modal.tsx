import { useEffect, type ReactNode } from 'react'

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

export function Modal({ isOpen, onClose, title, children, dismissable = true }: ModalProps) {
  useEffect(() => {
    if (!isOpen || !dismissable) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose, dismissable])

  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4"
      role="presentation"
      onClick={dismissable ? onClose : undefined}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? 'modal-title' : undefined}
        className="w-full max-w-md rounded-lg bg-white p-6 shadow-lg"
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
