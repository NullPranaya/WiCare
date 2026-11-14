"""Patient reply -> intent, no LLM. Exact OK/HELP/AWAY (1/2/3) are deterministic. Free text with any
help signal escalates; everything else is ambiguous (clarify + caregiver review). Free text never closes."""
import re
from photon.webhook import exact_intent

HELP = re.compile(r"\b(help|fell|fall(en)?|hurt|pain|injur\w*|can'?t get up|cannot get up|can not get up|"
                  r"emergency|ambulance|911|bleeding|dizzy|stuck)\b", re.I)

def interpret(text):
    intent = exact_intent(text)
    if intent:
        return dict(intent=intent, source='exact')
    if HELP.search(text):
        return dict(intent='help_requested', source='keyword')
    return dict(intent='ambiguous', source='default')
