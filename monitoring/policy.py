"""Deterministic event policy. The agent never overrides this."""
from dataclasses import dataclass

LIVE_SOURCES = {'live_score', 'live_csi', 'espectre_live'}
DEMO_SOURCES = {'wifall_replay', 'synthetic', 'room_replay', 'espectre_replay'}

@dataclass
class Decision:
    action: str     # sensor_unavailable | monitor | routine_checkin | update_existing
    reason: str

def decide(event, health, *, opt_in, demo_mode, expected_active, open_checkin):
    src = event['source']
    if not health.get('ready') or not health.get('fresh') or health.get('mode') != 'motion':
        return Decision('sensor_unavailable', f"sensor {health.get('status', 'not ready')}; no inference about the person")
    if src not in LIVE_SOURCES | DEMO_SOURCES:
        return Decision('monitor', f'unknown source {src}')
    if src in DEMO_SOURCES and not demo_mode:
        return Decision('monitor', 'replay/synthetic events never message anyone outside demo mode')
    if not opt_in:
        return Decision('monitor', 'monitored person has not opted in')
    if open_checkin:
        return Decision('update_existing', f"check-in {open_checkin['id']} already open; evidence updated, no new message")
    if event['event_type'] == 'burst_then_stillness':
        return Decision('routine_checkin', 'short burst of strong movement, then stillness; unconfirmed, check in')
    if event['event_type'] == 'abrupt_motion_then_inactivity':
        return Decision('routine_checkin', 'abrupt movement followed by low activity; unconfirmed, check in')
    if event['event_type'] == 'prolonged_low_activity':
        if expected_active:
            return Decision('routine_checkin', 'unusual low activity in expected-active context')
        return Decision('monitor', 'low activity outside expected-active context')
    return Decision('monitor', 'normal activity')
