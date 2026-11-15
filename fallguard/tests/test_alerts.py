import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from fallguard.alerts import AlertManager, NodeSender, fall_message

AT = datetime(2026, 11, 14, 14, 32, 5)


def test_fall_message_text():
    assert fall_message(AT, 8.0) == (
        "WiCare: possible fall detected at 14:32:05. "
        "Patient has been still for 8 s after a sudden movement."
    )


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def test_sends_then_cooldown_blocks():
    sent: list[str] = []

    async def sender(text: str) -> None:
        sent.append(text)

    clock = FakeClock()
    alerts = AlertManager(sender, cooldown_s=60, clock=clock)
    assert (await alerts.on_fall(AT, 8.0)).status == "sent"
    clock.now += 30
    assert (await alerts.on_fall(AT, 8.0)).status == "skipped_cooldown"
    clock.now += 31
    assert (await alerts.on_fall(AT, 8.0)).status == "sent"
    assert sent == [fall_message(AT, 8.0)] * 2


async def test_send_failure_reports_failed_and_no_cooldown():
    calls = {"n": 0}

    async def sender(text: str) -> None:
        calls["n"] += 1
        raise RuntimeError("photon unreachable")

    alerts = AlertManager(sender, cooldown_s=60, clock=FakeClock())
    result = await alerts.on_fall(AT, 8.0)
    assert result.status == "failed" and "photon unreachable" in result.detail
    await alerts.on_fall(AT, 8.0)
    assert calls["n"] == 2


async def test_no_recipient():
    assert (await AlertManager(None, 60).on_fall(AT, 8.0)).status == "no_recipient"


async def test_node_sender_timeout():
    sender = NodeSender(
        Path("x"), "+1", command=(sys.executable, "-c", "import time; time.sleep(5)"), timeout_s=0.2
    )
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        await sender("hi")
    assert time.monotonic() - started < 2


async def test_node_sender_passes_args(tmp_path):
    out = tmp_path / "args.json"
    code = f"import json,sys; open({str(out)!r},'w').write(json.dumps(sys.argv[1:]))"
    await NodeSender(Path("x"), "+1555", command=(sys.executable, "-c", code))("hi")
    assert json.loads(out.read_text()) == ["x", "+1555", "hi"]


async def test_node_sender_nonzero_exit_raises_with_stderr():
    code = "import sys; sys.stderr.write('bad credentials'); sys.exit(1)"
    with pytest.raises(RuntimeError, match="bad credentials"):
        await NodeSender(Path("x"), "+1", command=(sys.executable, "-c", code))("hi")
