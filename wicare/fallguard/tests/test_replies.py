from datetime import UTC, datetime

import httpx
import pytest

from fallguard.replies import PhotonReplies, parse_reply

NURSE = "+15555550100"
NOW = datetime(2026, 11, 15, 2, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "reply"),
    [("1", "acknowledge"), (" 1 ", "acknowledge"), ("1.", "acknowledge"), ("Acknowledge", "acknowledge"),
     ("2", "ignore"), ("ignore!", "ignore"), ("12", None), ("ok", None), ("", None)],
)
def test_parse_reply(text, reply):
    assert parse_reply(text) == reply


def message(mid, text, handle=NURSE, kind="text"):
    return {"id": mid, "sentBy": {"handle": handle}, "content": {"type": kind, "format": "plain", "text": text}}


def client_for(pages, calls):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        token = request.url.params.get("pageToken")
        return httpx.Response(200, json=pages[token])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_no_poll_before_arm():
    calls = []
    replies = PhotonReplies("pho_prj_x", "pho_sk_y", NURSE, http=client_for({None: {"items": []}}, calls),
                            now=lambda: NOW)
    assert await replies.poll() == [] and calls == []


async def test_reads_nurse_replies_once_across_pages():
    pages = {
        None: {"items": [message("m1", "hello"), message("m2", "1", handle="+1 (999) 000-0000")],
               "nextPageToken": "p2"},
        "p2": {"items": [message("m3", "2"), message("m4", "1", kind="image")]},
    }
    calls = []
    replies = PhotonReplies("pho_prj_x", "pho_sk_y", "+1 555-555-0100", http=client_for(pages, calls),
                            now=lambda: NOW)
    replies.arm()
    assert await replies.poll() == ["ignore"]
    assert await replies.poll() == []  # already seen
    first = calls[0]
    assert first.url.path == "/v1/projects/pho_prj_x/messages"
    assert first.headers["Authorization"] == "Bearer pho_sk_y"
    assert first.url.params["direction"] == "received"
    assert first.url.params["createdSince"] == "2026-11-15T01:59:50.000Z"
    assert calls[1].url.params["pageToken"] == "p2"


async def test_http_error_raises_for_the_caller_to_retry():
    def handler(request):
        return httpx.Response(429, json={"detail": "slow down"})

    replies = PhotonReplies("pho_prj_x", "k", NURSE, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                            now=lambda: NOW)
    replies.arm()
    with pytest.raises(httpx.HTTPStatusError):
        await replies.poll()
