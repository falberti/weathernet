import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class TelegramAPIError(Exception):
    pass


def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"


def get_updates(offset: int | None, timeout: int = 25) -> list[dict]:
    """Long-polls Telegram for new updates. `timeout` (seconds) is how
    long Telegram holds the connection open waiting for something to
    happen before responding with an empty list -- long-polling like
    this, rather than a short poll on a tight loop, is what Telegram's
    own docs recommend: near-instant delivery without needing a public
    webhook (and therefore without needing the VM's self-signed
    certificate to be something Telegram's servers will accept, which
    it isn't -- see PROJECT_SPEC.md Section 12).
    """
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    response = requests.get(_api_url("getUpdates"), params=params, timeout=timeout + 10)
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise TelegramAPIError(f"getUpdates failed: {body}")
    return body["result"]


def send_message(chat_id: int, text: str) -> None:
    response = requests.post(
        _api_url("sendMessage"),
        json={"chat_id": chat_id, "text": text},
        timeout=15,
    )
    if response.status_code != 200:
        # Never raise on a single failed send -- one unreachable chat
        # (e.g. the user blocked the bot) must not stop the digest run
        # or the poll loop from processing everything else.
        logger.warning("Telegram sendMessage to %s failed (%s): %s", chat_id, response.status_code, response.text)
