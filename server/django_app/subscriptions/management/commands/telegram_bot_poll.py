import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from subscriptions.bot import handle_update
from subscriptions.telegram_api import get_updates

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Runs forever, long-polling Telegram for updates and dispatching
    each one to subscriptions.bot.handle_update. Meant to be the main
    process of a long-running container (see docker-compose.yml's
    `telegram-bot` service), not a one-shot/cron command -- unlike
    send_daily_digest, there's no fixed point where this is "done".
    """

    help = "Long-poll Telegram for bot updates and dispatch them (runs forever)."

    def handle(self, *args, **options):
        offset = None
        self.stdout.write(self.style.SUCCESS("Telegram bot poll loop starting"))

        while True:
            try:
                updates = get_updates(offset)
            except Exception as exc:  # noqa: BLE001 -- a transient network/API blip must never kill this loop
                logger.warning("get_updates failed, retrying in 5s: %s", exc)
                time.sleep(5)
                continue

            for update in updates:
                offset = update["update_id"] + 1
                try:
                    handle_update(update)
                except Exception:  # noqa: BLE001 -- one bad update must not stop the loop or skip the rest of the batch
                    logger.exception("Error handling update %s", update.get("update_id"))

            # A long-lived process can outlive individual DB
            # connections (idle timeout, network blip); this lets
            # Django reopen a fresh one next iteration instead of
            # reusing one that's gone stale.
            close_old_connections()
