import { useEffect, useRef, useState } from 'react'

import { filingStatusWebSocketUrl } from '../lib/filingStatus'

// A WebSocket backed by a real Postgres LISTEN/NOTIFY trigger (migration
// 0016), not Supabase Realtime — see that migration's docstring for why.
// Reconnects if the connection drops for any reason other than an
// intentional unmount. Returns null until the first message arrives (the
// server sends the filing's current status immediately on connect), so
// callers should fall back to whatever status they already have (e.g. from
// a REST fetch) until this resolves. `onStatusChange` fires only when the
// status genuinely changes from a previously-seen value, not on the
// initial connect message — kept in a ref so passing a new inline function
// each render doesn't force a reconnect.
export function useLiveFilingStatus(
  filingId: string | undefined,
  onStatusChange?: (status: string) => void,
): string | null {
  const [status, setStatus] = useState<string | null>(null)
  const onStatusChangeRef = useRef(onStatusChange)
  useEffect(() => {
    onStatusChangeRef.current = onStatusChange
  })

  useEffect(() => {
    if (!filingId) return
    let cancelled = false
    let socket: WebSocket | null = null

    function connect() {
      if (cancelled) return
      socket = new WebSocket(filingStatusWebSocketUrl(filingId as string))
      socket.onmessage = (event) => {
        const payload = JSON.parse(event.data) as { status: string }
        setStatus((previous) => {
          if (previous !== null && previous !== payload.status) {
            onStatusChangeRef.current?.(payload.status)
          }
          return payload.status
        })
      }
      socket.onclose = () => {
        if (!cancelled) setTimeout(connect, 2000)
      }
    }
    connect()

    return () => {
      cancelled = true
      socket?.close()
    }
  }, [filingId])

  return status
}
