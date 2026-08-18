"""Telegram command handling -- pure dispatch logic, kept separate
from management/commands/telegram_bot_poll.py so it's testable without
actually running the long-poll loop (see tests.py).

There is no web form for subscribing (see PROJECT_SPEC.md Section 3):
the entire flow -- giving a place name, seeing whether a probe is
close enough, listing/removing subscriptions -- happens as a
conversation with the bot. Nominatim (geocoding.py) resolves whatever
free text a user sends into coordinates; it doesn't need to be exact,
only close enough to compare against probe locations.
"""

import logging

from django.conf import settings

from .geo import haversine_km
from .geocoding import GeocodingError, geocode_place
from .matching import nearest_active_probe
from .models import WeatherSubscription
from .telegram_api import send_message

logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "Ciao! Sono il bot di WeatherNet.\n\n"
    "Scrivimi il nome di una località (es. \"Milano\" o \"Via Roma, Torino\") "
    "e ti iscrivo al riepilogo meteo giornaliero per quella zona, se c'è "
    "una sonda abbastanza vicina.\n\n"
    "Comandi:\n"
    "/list -- le tue località iscritte\n"
    "/remove <numero> -- rimuovi una località (numero da /list)\n"
    "/stop -- rimuovi tutte le tue iscrizioni\n"
    "/help -- questo messaggio"
)

# A repeat query that resolves within this distance of an existing
# subscription for the same chat is treated as a duplicate, not a new
# subscription -- avoids silent pile-up from someone re-sending
# roughly the same place name (e.g. "Milano" then "Milano centro").
DEDUP_DISTANCE_KM = 1.0

# Real place names are short ("Via Roma 12, Milano, MI, Italia" is
# ~35 chars) -- this is generous headroom, not a real constraint on
# legitimate use. Rejecting an oversized message before it reaches
# geocode_place() means a flood of garbage text costs nothing beyond
# a DB read and a reply, not a wasted (rate-limited) Nominatim call.
MAX_QUERY_LENGTH = 200

# One chat spamming distinct place names can't be stopped by the
# geocoding throttle alone (it slows the flood down, it doesn't cap
# how many subscriptions eventually land) -- this bounds how much one
# chat can grow the table and how many digest messages it generates
# per day, regardless of how patient the sender is.
MAX_SUBSCRIPTIONS_PER_CHAT = 10


def handle_update(update: dict) -> None:
    message = update.get("message")
    if not message or "text" not in message:
        return  # not a plain text message (edited message, photo, sticker, ...) -- ignore
    chat_id = message["chat"]["id"]
    chat_username = message["chat"].get("username", "")
    text = message["text"].strip()
    if not text:
        return

    if text in ("/start", "/help"):
        send_message(chat_id, WELCOME_TEXT)
    elif text == "/list":
        _handle_list(chat_id)
    elif text.startswith("/remove"):
        _handle_remove(chat_id, text)
    elif text == "/stop":
        _handle_stop(chat_id)
    else:
        _handle_place_query(chat_id, chat_username, text)


def _handle_list(chat_id: int) -> None:
    subscriptions = list(WeatherSubscription.objects.filter(chat_id=chat_id).order_by("created_at"))
    if not subscriptions:
        send_message(chat_id, "Non hai ancora nessuna località iscritta. Scrivimi un nome di luogo per iniziare.")
        return

    lines = []
    for index, sub in enumerate(subscriptions, start=1):
        match = nearest_active_probe(float(sub.latitude), float(sub.longitude))
        if match and match[1] <= settings.SUBSCRIPTION_MAX_DISTANCE_KM:
            status = f"sonda a {match[1]:.1f} km"
        else:
            status = "nessuna sonda abbastanza vicina per ora"
        lines.append(f"{index}. {sub.place_label} -- {status}")
    send_message(chat_id, "Le tue località:\n" + "\n".join(lines))


def _handle_remove(chat_id: int, text: str) -> None:
    parts = text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip().isdigit():
        send_message(chat_id, "Uso: /remove <numero> -- il numero è quello mostrato da /list.")
        return

    index = int(parts[1].strip())
    subscriptions = list(WeatherSubscription.objects.filter(chat_id=chat_id).order_by("created_at"))
    if index < 1 or index > len(subscriptions):
        send_message(chat_id, "Numero non valido. Usa /list per vedere i numeri correnti.")
        return

    removed = subscriptions[index - 1]
    removed.delete()
    send_message(chat_id, f"Rimossa: {removed.place_label}")


def _handle_stop(chat_id: int) -> None:
    deleted, _ = WeatherSubscription.objects.filter(chat_id=chat_id).delete()
    if deleted:
        send_message(chat_id, "Tutte le tue iscrizioni sono state rimosse. Puoi ricominciare quando vuoi.")
    else:
        send_message(chat_id, "Non avevi nessuna iscrizione attiva.")


def _handle_place_query(chat_id: int, chat_username: str, query: str) -> None:
    if len(query) > MAX_QUERY_LENGTH:
        send_message(chat_id, "Testo troppo lungo -- prova con un nome di località più breve.")
        return

    existing = list(WeatherSubscription.objects.filter(chat_id=chat_id))
    if len(existing) >= MAX_SUBSCRIPTIONS_PER_CHAT:
        send_message(
            chat_id,
            f"Hai già {MAX_SUBSCRIPTIONS_PER_CHAT} località iscritte, il massimo consentito. "
            "Usa /remove per liberarne una prima di aggiungerne altre.",
        )
        return

    try:
        result = geocode_place(query)
    except GeocodingError:
        send_message(chat_id, "Il servizio di geocoding non risponde al momento -- riprova tra qualche minuto.")
        return

    if result is None:
        send_message(
            chat_id,
            f"Non ho trovato nessun luogo corrispondente a \"{query}\". "
            "Prova a essere più specifico (es. aggiungi la provincia).",
        )
        return

    for sub in existing:
        distance = haversine_km(result["latitude"], result["longitude"], float(sub.latitude), float(sub.longitude))
        if distance <= DEDUP_DISTANCE_KM:
            send_message(chat_id, f"Sei già iscritto a una località molto vicina: {sub.place_label}")
            return

    subscription = WeatherSubscription.objects.create(
        chat_id=chat_id,
        chat_username=chat_username,
        query_text=query,
        place_label=result["display_name"],
        latitude=result["latitude"],
        longitude=result["longitude"],
    )

    match = nearest_active_probe(result["latitude"], result["longitude"])
    if match and match[1] <= settings.SUBSCRIPTION_MAX_DISTANCE_KM:
        subscription.probe_ever_found = True
        subscription.save(update_fields=["probe_ever_found"])
        send_message(
            chat_id,
            f"Iscritto a \"{result['display_name']}\" -- c'è una sonda a {match[1]:.1f} km, "
            "riceverai il riepilogo ogni mattina.",
        )
    else:
        send_message(
            chat_id,
            f"Iscritto a \"{result['display_name']}\" -- al momento non c'è nessuna sonda abbastanza "
            f"vicina (entro {settings.SUBSCRIPTION_MAX_DISTANCE_KM:.0f} km). Tengo la richiesta in "
            "memoria: ti avviso appena una sonda sarà attiva nella tua zona.",
        )
