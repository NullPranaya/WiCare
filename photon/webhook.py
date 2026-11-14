"""Inbound Photon 2.0 (Beta) webhook events, signed with Standard Webhooks:
signature = base64(HMAC_SHA256(base64decode(secret without 'whsec_'), f'{id}.{timestamp}.{raw body}')),
header 'webhook-signature' = space-separated 'v1,<b64>' entries, 5 min timestamp tolerance."""
import base64, hashlib, hmac, time

# exact replies to "Reply 1: YES / Reply 2: NO" (AWAY / 3 still understood, not advertised)
EXACT = {**dict.fromkeys(['1', 'yes', 'y', 'yeah', 'yep', 'ok', 'okay', "i'm okay", 'im okay', "i'm ok", 'im ok',
                          "i'm fine", 'im fine', 'fine'], 'acknowledged'),
         **dict.fromkeys(['2', 'no', 'n', 'nope', 'help'], 'help_requested'),
         **dict.fromkeys(['3', 'away'], 'away')}

def exact_intent(text):
    return EXACT.get(text.strip().strip('.!').lower().replace('\u2019', "'"))

def verify(headers, raw: bytes, secret, tolerance=300):
    wid, ts, sigs = headers.get('webhook-id'), headers.get('webhook-timestamp'), headers.get('webhook-signature')
    if not (secret and wid and ts and sigs):
        return False
    try:
        if abs(time.time() - int(ts)) > tolerance: return False
        key = base64.b64decode(secret.removeprefix('whsec_'))
    except ValueError:
        return False
    expected = base64.b64encode(hmac.new(key, f'{wid}.{ts}.'.encode() + raw, hashlib.sha256).digest()).decode()
    return any(s.partition(',')[0] == 'v1' and hmac.compare_digest(s.partition(',')[2], expected)
               for s in sigs.split())

def _find_message(obj, depth=0):
    """The event wrapper shape is not documented outside the authed event catalog, so locate the
    PlatformMessage object (has 'sentBy' and 'content') wherever it sits in the event."""
    if depth > 4 or not isinstance(obj, dict): return None
    if isinstance(obj.get('sentBy'), dict) and isinstance(obj.get('content'), dict): return obj
    for v in obj.values():
        m = _find_message(v, depth + 1)
        if m: return m
    return None

def normalize(payload):
    """Photon event -> flat dict. None for non-text, non-message, or our own (agent) messages."""
    m = _find_message(payload)
    if not m: return None
    c, by = m['content'], m['sentBy']
    if c.get('type') != 'text' or by.get('kind') == 'agent': return None
    return dict(message_id=m.get('id'), sender_id=by.get('handle'), sender_user_id=by.get('id'),
                space_id=(m.get('space') or {}).get('id'), text=(c.get('text') or '').strip(),
                received_at=m.get('createdAt'))
