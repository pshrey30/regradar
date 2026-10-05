import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'

import { ApiError, apiFetch } from '../lib/api'
import { Button } from './Button'
import { Input } from './Input'
import { Modal } from './Modal'

interface ManualAlertResponse {
  status: string
  recipient: string
  error_message: string | null
}

// Admin-only: POST /v1/filings/{id}/alert sends this filing's alert to any
// email address, independent of the org's configured delivery settings
// (Slack/webhook/whatever) — see that route's own docstring. Shared between
// FilingDetail (one filing at a time) and Activity (one filing per row).
export function SendAlertModal({
  filingId,
  isOpen,
  onClose,
}: {
  filingId: string
  isOpen: boolean
  onClose: () => void
}) {
  const [email, setEmail] = useState('')

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<ManualAlertResponse>(`/v1/filings/${filingId}/alert`, {
        method: 'POST',
        body: JSON.stringify({ email }),
      }),
  })

  function handleClose() {
    setEmail('')
    mutation.reset()
    onClose()
  }

  const sent = mutation.isSuccess

  return (
    <Modal isOpen={isOpen} onClose={handleClose} title="Send alert to an email">
      {sent ? (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600">
            Alert sent to <strong>{mutation.data?.recipient}</strong>.
          </p>
          <Button type="button" onClick={handleClose}>
            Done
          </Button>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <p className="text-sm text-slate-600">
            Send this filing&rsquo;s alert to any email address, independent of the org&rsquo;s
            configured delivery settings.
          </p>
          <Input
            type="email"
            label="Recipient email"
            placeholder="someone@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          {mutation.isError && (
            <p className="text-sm text-risk-critical">
              {mutation.error instanceof ApiError
                ? mutation.error.message
                : 'Something went wrong sending this alert.'}
            </p>
          )}
          <div className="flex justify-end gap-3">
            <Button type="button" variant="secondary" onClick={handleClose}>
              Cancel
            </Button>
            <Button
              type="button"
              disabled={!email}
              loading={mutation.isPending}
              onClick={() => mutation.mutate()}
            >
              Send alert
            </Button>
          </div>
        </div>
      )}
    </Modal>
  )
}
