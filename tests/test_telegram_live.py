"""Separately invoked live Telegram integration test.

This file has never been run. It is skipped unless an authorized operator
supplies every required input, it is marked `live` so the documented core
command excludes it, and it sends at most one message with public synthetic
content.

A skip here is an unsatisfied gate, not a pass. Do not remove the guards to
"see if it works": doing so is live external activity.

Invocation, only under explicit operator authorization:

    KESTREL_RUN_LIVE_TELEGRAM=1 \\
    KESTREL_TELEGRAM_ACTIVATED=1 \\
    KESTREL_TELEGRAM_CREDENTIAL=/external/messaging/telegram-secrets/bot.token \\
    KESTREL_TELEGRAM_CHAT_ID=<numeric chat id> \\
    .venv/bin/python -m pytest tests/test_telegram_live.py -m live -q \\
        --allow-hosts=api.telegram.org

No token or chat identifier may enter a JUnit record, a fixture, or a commit.
"""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

REQUIRED = ("KESTREL_RUN_LIVE_TELEGRAM", "KESTREL_TELEGRAM_ACTIVATED",
            "KESTREL_TELEGRAM_CREDENTIAL", "KESTREL_TELEGRAM_CHAT_ID")

SYNTHETIC_TEXT = ("Kestrel synthetic activation test. This message contains no research "
                  "content. Provider acceptance is not delivery, reading, review, or "
                  "approval.")


def _missing() -> list[str]:
    return [name for name in REQUIRED if not os.environ.get(name)]


@pytest.fixture(scope="module")
def authorized():
    missing = _missing()
    if missing:
        pytest.skip(f"live Telegram gate not authorized; missing {', '.join(missing)}")
    if os.environ["KESTREL_RUN_LIVE_TELEGRAM"] != "1":
        pytest.skip("live Telegram gate not explicitly enabled")
    credential = Path(os.environ["KESTREL_TELEGRAM_CREDENTIAL"])
    chat_id = int(os.environ["KESTREL_TELEGRAM_CHAT_ID"])
    return credential, chat_id


def test_a_single_authorized_synthetic_message(authorized):
    """One `getMe`, one `getWebhookInfo`, one `sendMessage`. Nothing else."""
    from kestrel.telegram import (
        API_DOCUMENTATION,
        StandardHttpTransport,
        TelegramClient,
        read_credential,
    )

    credential, chat_id = authorized
    client = TelegramClient(read_credential(credential), http=StandardHttpTransport(),
                            channel="live-canary")
    identity = client.get_me()
    assert identity.is_bot
    webhook = client.get_webhook_info()
    assert not webhook.url, ("another integration owns this bot's webhook; stop and resolve "
                             "the conflict with its owner")
    outcome = client.send_message(chat_id=chat_id, text=SYNTHETIC_TEXT)
    assert outcome["ok"] is True, outcome
    assert outcome["destination_confirmed"] is True

    # Record only what may be recorded. No token, no chat id, no message text.
    for record in client.log:
        rendered = repr(record.__dict__)
        assert os.environ["KESTREL_TELEGRAM_CHAT_ID"] not in rendered
        assert credential.read_text().strip() not in rendered
    assert [record.method for record in client.log] == \
        ["getMe", "getWebhookInfo", "sendMessage"]

    # This is the boundary of what the test establishes.
    assert API_DOCUMENTATION["live_integration_verified"] is False, (
        "API acceptance of one message is not a general delivery guarantee; whether it "
        "appeared on a device is a separate manual observation an operator records by hand")


def test_the_gate_is_closed_by_default():
    """Runs in ordinary invocations only to assert the guards exist."""
    if not _missing():
        pytest.skip("the live gate is currently authorized")
    assert os.environ.get("KESTREL_RUN_LIVE_TELEGRAM") in (None, "", "0")
