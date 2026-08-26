# API Contract

This document will hold the finalized REST API contract (endpoints, request/response schemas,
and error envelope) as the API-* tickets land. The webhook verification section below is
complete as of SEC-03; the rest of this file is still a placeholder.

## Verifying webhook signatures (SEC-03)

Every filing-alert webhook RegRadar sends carries two headers:

- `X-RegRadar-Timestamp` — the Unix timestamp (seconds), as a decimal string, at the moment the
  request was signed.
- `X-RegRadar-Signature` — `<algorithm>=<hex_hmac>`, e.g. `sha256=5257a869e7...`. The algorithm
  name matches `WEBHOOK_HMAC_ALGORITHM` (`sha256` by default).

The signature is computed over the **timestamp and the raw request body concatenated**, not the
body alone — this is deliberate: it's what lets a receiver reject a replayed request even though
its signature is genuinely valid for that body, because the timestamp binds each signature to one
moment in time. The exact bytes signed are:

```
signed_payload = f"{timestamp}.".encode() + raw_body
signature      = hmac_sha256(webhook_secret, signed_payload).hexdigest()
```

`webhook_secret` is the value returned exactly once in `POST /v1/webhooks`'s creation response
(API-08) — store it; it is never returned again by any other endpoint.

### Verification algorithm a receiving server must run

1. Read `X-RegRadar-Timestamp` and the `sha256=...` value from `X-RegRadar-Signature`, plus the
   **raw** request body — bytes exactly as received on the wire, before any JSON
   parsing/re-serialization (re-serializing can reorder keys or change whitespace, which changes
   the bytes being signed and makes a correct signature look invalid).
2. Reject the request if the timestamp is more than **5 minutes** (300 seconds) from your own
   current time, in either direction. Too old means a replayed request; noticeably in the future
   is a clock-skew or forgery smell either way — reject both, don't just check "not expired."
3. Recompute the expected signature using your stored `webhook_secret` and the algorithm above.
4. Compare the recomputed signature to the received one using a constant-time comparison (e.g.
   Python's `hmac.compare_digest`, `crypto.timingSafeEqual` in Node) — a naive `==` string
   compare leaks timing information an attacker can use to guess the signature byte-by-byte.
5. Only if steps 2 and 4 both pass, trust and process the payload.

### Reference implementation

`src/regradar/delivery/webhook_dispatcher.py`'s `verify_webhook_signature()` is the canonical
implementation of the algorithm above — use it directly if your receiver is Python, or port its
logic line-for-line otherwise:

```python
def verify_webhook_signature(
    secret: str,
    timestamp: str | int,
    body: bytes,
    signature: str,
    *,
    algorithm: str = "sha256",
    tolerance_seconds: int = 300,
) -> bool:
    """Returns False (never raises) on any of: a malformed timestamp, a
    timestamp outside tolerance_seconds of now (past or future), or a
    signature that doesn't match."""
```

Call it with the values from step 1 above:

```python
is_valid = verify_webhook_signature(
    secret=your_stored_webhook_secret,
    timestamp=request.headers["X-RegRadar-Timestamp"],
    body=request.raw_body,
    signature=request.headers["X-RegRadar-Signature"].split("=", 1)[1],
)
```
