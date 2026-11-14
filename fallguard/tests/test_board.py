import httpx

from fallguard.board import BoardClient, BoardHealth, BoardStatus, MotionSample, parse_sse

ENDPOINT = "http://board.test/espectre/v1"

CAPTURED = (
    "retry: 3000\n"
    ": connected\n"
    "\n"
    "event: motion\n"
    'data: {"timestamp_ms":1889616,"state":"idle","score":0.0148352}\n'
    "\n"
    "event: motion\n"
    'data: {"timestamp_ms":1890119,"state":"idle","score":0.0224423}\n'
    "\n"
)


def test_parse_sse_motion_blocks():
    events = list(parse_sse(CAPTURED.splitlines()))
    assert events == [
        ("motion", {"timestamp_ms": 1889616, "state": "idle", "score": 0.0148352}),
        ("motion", {"timestamp_ms": 1890119, "state": "idle", "score": 0.0224423}),
    ]


def test_parse_sse_skips_invalid_json():
    lines = ["event: motion", "data: {not json", "", "event: motion", 'data: {"score": 0.5}', ""]
    assert list(parse_sse(lines)) == [("motion", {"score": 0.5})]


async def _take(agen, n):
    out = []
    async for item in agen:
        out.append(item)
        if len(out) == n:
            break
    return out


async def test_stream_yields_connected_then_samples():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=CAPTURED, headers={"content-type": "text/event-stream"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = BoardClient("b1", ENDPOINT, http=http, clock=lambda: 42.0, backoff_s=(0,))
        items = await _take(client.stream(), 3)
    assert items[0] == BoardStatus("b1", True, "connected")
    assert items[1] == MotionSample("b1", 42.0, 0.0148352, 1889616)
    assert items[2].score == 0.0224423
    assert seen[0].url == httpx.URL(ENDPOINT + "/events")
    assert seen[0].headers["origin"] == "https://test.espectre.dev"
    assert seen[0].headers["accept"] == "text/event-stream"


async def test_reconnects_after_refusal():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, text=CAPTURED)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = BoardClient("b1", ENDPOINT, http=http, backoff_s=(0,))
        items = await _take(client.stream(), 3)
    assert items[0].connected is False and "503" in items[0].detail
    assert items[1] == BoardStatus("b1", True, "connected")
    assert isinstance(items[2], MotionSample)


async def test_stream_close_reports_disconnect_and_retries():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="" if calls["n"] == 1 else CAPTURED)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        items = await _take(BoardClient("b1", ENDPOINT, http=http, backoff_s=(0,)).stream(), 4)
    assert [type(i).__name__ for i in items] == ["BoardStatus", "BoardStatus", "BoardStatus", "MotionSample"]
    assert items[1].connected is False


async def test_health_reads_fields():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["origin"] == "https://test.espectre.dev"
        if request.url.path.endswith("/diagnostics"):
            # Without a field selection the board returns its field catalog, not values.
            if request.url.params.get("fields") != '["csi_admitted_pps","csi_occupancy"]':
                return httpx.Response(200, json={"fields": [{"name": "csi_occupancy"}]})
            return httpx.Response(200, json={"csi_admitted_pps": 73.9, "csi_occupancy": 0.74})
        return httpx.Response(200, json={"ready": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await BoardClient("b1", ENDPOINT, http=http).health() == BoardHealth("b1", 73.9, 0.74, True)


async def test_health_errors_give_none():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as http:
        assert await BoardClient("b1", ENDPOINT, http=http).health() == BoardHealth("b1", None, None, None)
