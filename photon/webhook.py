"""Inbound messages. Two accepted senders of webhooks:
1) our local spectrum-ts bridge (shared secret header), 2) Spectrum Cloud native webhooks
(X-Spectrum-Signature = 'v0=' + HMAC_SHA256(secret, 'v0:<timestamp>:<raw body>'), 5 min tolerance)."""
import hashlib, hmac, time

EXACT = {'ok': 'acknowledged', '1': 'acknowledged', 'help': 'help_requested', '2': 'help_requested',
         'away': 'away', '3': 'away'}

def verify_spectrum(headers, raw: bytes, secret, tolerance=300):
    ts, sig = headers.get('x-spectrum-timestamp'), headers.get('x-spectrum-signature')
    if not (secret and ts and sig and headers.get('x-spectrum-event')):
        return False
    try:
        if abs(time.time() - int(ts)) > tolerance: return False
    except ValueError:
        return False
    expected = 'v0=' + hmac.new(secret.encode(), b'v0:' + ts.encode() + b':' + raw, hashlib.sha256).hexdigest()
    return len(expected) == len(sig) and hmac.compare_digest(expected, sig)

def verify_bridge(headers, secret):
    got = headers.get('x-bridge-secret', '')
    return bool(secret) and hmac.compare_digest(got, secret)

def normalize(payload):
    """Spectrum 'messages' event -> flat dict. Returns None for non-text content."""
    if payload.get('event') != 'messages': return None
    m = payload.get('message') or {}
    c = m.get('content') or {}
    if c.get('type') != 'text': return None
    return dict(message_id=m.get('id'), sender_id=(m.get('sender') or {}).get('id'),
                space_id=(payload.get('space') or {}).get('id'), text=(c.get('text') or '').strip(),
                received_at=m.get('timestamp'))

def exact_intent(text):
    return EXACT.get(text.strip().strip('.!').lower())
