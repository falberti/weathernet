import datetime
import time
from unittest.mock import MagicMock, patch

import requests
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from probes.models import Probe
from telemetry.models import SensorReading

from . import geocoding as geocoding_module
from .bot import MAX_QUERY_LENGTH, MAX_SUBSCRIPTIONS_PER_CHAT, handle_update
from .geo import haversine_km
from .geocoding import GeocodingError, geocode_place
from .matching import nearest_active_probe
from .models import WeatherSubscription


class HaversineTests(TestCase):
    def test_same_point_is_zero(self):
        self.assertAlmostEqual(haversine_km(45.0, 9.0, 45.0, 9.0), 0.0)

    def test_known_distance_milan_rome(self):
        # Milan to Rome is roughly 480km great-circle -- a loose bound
        # is enough to catch a real formula bug without being brittle.
        distance = haversine_km(45.4642, 9.1900, 41.9028, 12.4964)
        self.assertTrue(450 < distance < 500, distance)


class GeocodingTests(TestCase):
    def setUp(self):
        # The throttle's "last request" state is module-level (shared
        # across every call in this process, on purpose -- see
        # geocoding.py) -- reset it far enough in the past that these
        # tests never actually sleep because an earlier test in the
        # same run happened to call geocode_place() a moment ago.
        geocoding_module._last_request_at = 0.0

    @patch("subscriptions.geocoding.requests.get")
    def test_returns_top_match(self, mock_get):
        mock_get.return_value = MagicMock(
            json=lambda: [{"display_name": "Milano, Lombardia, Italia", "lat": "45.4642", "lon": "9.1900"}],
        )
        result = geocode_place("Milano")
        self.assertEqual(result["display_name"], "Milano, Lombardia, Italia")
        self.assertAlmostEqual(result["latitude"], 45.4642)
        self.assertAlmostEqual(result["longitude"], 9.1900)

    @patch("subscriptions.geocoding.requests.get")
    def test_no_results_returns_none(self, mock_get):
        mock_get.return_value = MagicMock(json=lambda: [])
        self.assertIsNone(geocode_place("asdkjfhaskdjfh not a real place"))

    @patch("subscriptions.geocoding.requests.get")
    def test_network_error_raises_geocoding_error_not_a_generic_one(self, mock_get):
        mock_get.side_effect = requests.ConnectionError("boom")
        with self.assertRaises(GeocodingError):
            geocode_place("Milano")


class GeocodingThrottleTests(TestCase):
    """The rate limiter that keeps a Telegram message flood (accidental
    or a deliberate DoS attempt against the bot) from hammering
    Nominatim fast enough to get this server's IP blocked there --
    see the comment above _throttle() in geocoding.py.
    """

    @patch("subscriptions.geocoding.time.sleep")
    @patch("subscriptions.geocoding.requests.get")
    def test_back_to_back_calls_sleep_before_the_second_request(self, mock_get, mock_sleep):
        mock_get.return_value = MagicMock(json=lambda: [{"display_name": "X", "lat": "1.0", "lon": "1.0"}])
        geocoding_module._last_request_at = time.monotonic()  # "just" made a request

        geocode_place("Somewhere")

        mock_sleep.assert_called_once()
        slept_for = mock_sleep.call_args[0][0]
        self.assertGreater(slept_for, 0)
        self.assertLessEqual(slept_for, geocoding_module._MIN_REQUEST_INTERVAL_SECONDS)

    @patch("subscriptions.geocoding.time.sleep")
    @patch("subscriptions.geocoding.requests.get")
    def test_does_not_sleep_once_enough_time_has_passed(self, mock_get, mock_sleep):
        mock_get.return_value = MagicMock(json=lambda: [{"display_name": "X", "lat": "1.0", "lon": "1.0"}])
        geocoding_module._last_request_at = time.monotonic() - 5  # long enough ago

        geocode_place("Somewhere")

        mock_sleep.assert_not_called()


class NearestActiveProbeTests(TestCase):
    def test_returns_closest_active_probe(self):
        near = Probe.objects.create(
            name="near", hardware_type=Probe.HardwareType.RASPBERRY_PI_3,
            location_latitude="45.46", location_longitude="9.19", is_active=True,
        )
        Probe.objects.create(
            name="far", hardware_type=Probe.HardwareType.RASPBERRY_PI_3,
            location_latitude="41.90", location_longitude="12.50", is_active=True,
        )
        match = nearest_active_probe(45.47, 9.20)
        self.assertEqual(match[0], near)

    def test_excludes_inactive_probes(self):
        Probe.objects.create(
            name="inactive", hardware_type=Probe.HardwareType.RASPBERRY_PI_3,
            location_latitude="45.46", location_longitude="9.19", is_active=False,
        )
        self.assertIsNone(nearest_active_probe(45.46, 9.19))

    def test_excludes_probes_without_coordinates(self):
        Probe.objects.create(name="no-coords", hardware_type=Probe.HardwareType.RASPBERRY_PI_3, is_active=True)
        self.assertIsNone(nearest_active_probe(45.46, 9.19))

    def test_no_probes_returns_none(self):
        self.assertIsNone(nearest_active_probe(45.46, 9.19))


def _text_update(chat_id, text, username=""):
    return {"update_id": 1, "message": {"chat": {"id": chat_id, "username": username}, "text": text}}


@override_settings(SUBSCRIPTION_MAX_DISTANCE_KM=15)
class BotDispatchTests(TestCase):
    @patch("subscriptions.bot.send_message")
    def test_start_sends_welcome(self, mock_send):
        handle_update(_text_update(1, "/start"))
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args[0][0], 1)

    @patch("subscriptions.bot.send_message")
    def test_ignores_non_text_updates(self, mock_send):
        handle_update({"update_id": 1, "edited_message": {}})
        mock_send.assert_not_called()

    @patch("subscriptions.bot.send_message")
    def test_list_when_empty(self, mock_send):
        handle_update(_text_update(1, "/list"))
        self.assertIn("Non hai ancora", mock_send.call_args[0][1])

    @patch("subscriptions.bot.geocode_place")
    @patch("subscriptions.bot.send_message")
    def test_oversized_query_is_rejected_without_calling_geocoder(self, mock_send, mock_geocode):
        handle_update(_text_update(1, "A" * (MAX_QUERY_LENGTH + 1)))
        mock_geocode.assert_not_called()
        self.assertEqual(WeatherSubscription.objects.count(), 0)
        self.assertIn("troppo lungo", mock_send.call_args[0][1])

    @patch("subscriptions.bot.geocode_place")
    @patch("subscriptions.bot.send_message")
    def test_subscription_cap_is_enforced_without_calling_geocoder(self, mock_send, mock_geocode):
        for i in range(MAX_SUBSCRIPTIONS_PER_CHAT):
            WeatherSubscription.objects.create(
                chat_id=1, query_text=f"place-{i}", place_label=f"Place {i}",
                latitude=str(i), longitude=str(i),
            )

        handle_update(_text_update(1, "One more place"))

        mock_geocode.assert_not_called()
        self.assertEqual(WeatherSubscription.objects.filter(chat_id=1).count(), MAX_SUBSCRIPTIONS_PER_CHAT)
        self.assertIn("massimo consentito", mock_send.call_args[0][1])

    @patch("subscriptions.bot.geocode_place")
    @patch("subscriptions.bot.send_message")
    def test_place_query_creates_subscription_with_nearby_probe(self, mock_send, mock_geocode):
        Probe.objects.create(
            name="p", hardware_type=Probe.HardwareType.RASPBERRY_PI_3,
            location_latitude="45.46", location_longitude="9.19", is_active=True,
        )
        mock_geocode.return_value = {"display_name": "Milano, Italia", "latitude": 45.4650, "longitude": 9.1910}

        handle_update(_text_update(42, "Milano", username="mario"))

        sub = WeatherSubscription.objects.get(chat_id=42)
        self.assertEqual(sub.place_label, "Milano, Italia")
        self.assertEqual(sub.chat_username, "mario")
        self.assertTrue(sub.probe_ever_found)
        self.assertIn("riceverai il riepilogo", mock_send.call_args[0][1])

    @patch("subscriptions.bot.geocode_place")
    @patch("subscriptions.bot.send_message")
    def test_place_query_without_nearby_probe_still_saves_it(self, mock_send, mock_geocode):
        mock_geocode.return_value = {"display_name": "Roma, Italia", "latitude": 41.9, "longitude": 12.5}

        handle_update(_text_update(42, "Roma"))

        sub = WeatherSubscription.objects.get(chat_id=42)
        self.assertFalse(sub.probe_ever_found)
        self.assertIn("nessuna sonda abbastanza", mock_send.call_args[0][1])

    @patch("subscriptions.bot.geocode_place")
    @patch("subscriptions.bot.send_message")
    def test_place_query_with_no_geocode_match_creates_nothing(self, mock_send, mock_geocode):
        mock_geocode.return_value = None
        handle_update(_text_update(42, "asdkjfhaskdjfh"))
        self.assertEqual(WeatherSubscription.objects.count(), 0)
        self.assertIn("Non ho trovato", mock_send.call_args[0][1])

    @patch("subscriptions.bot.geocode_place")
    @patch("subscriptions.bot.send_message")
    def test_geocoding_error_is_reported_without_crashing(self, mock_send, mock_geocode):
        mock_geocode.side_effect = GeocodingError("timeout")
        handle_update(_text_update(42, "Milano"))
        self.assertEqual(WeatherSubscription.objects.count(), 0)
        self.assertIn("non risponde", mock_send.call_args[0][1])

    @patch("subscriptions.bot.geocode_place")
    @patch("subscriptions.bot.send_message")
    def test_duplicate_nearby_query_is_rejected_not_duplicated(self, mock_send, mock_geocode):
        WeatherSubscription.objects.create(
            chat_id=42, query_text="Milano", place_label="Milano",
            latitude="45.4650", longitude="9.1910",
        )
        mock_geocode.return_value = {"display_name": "Milano centro", "latitude": 45.4651, "longitude": 9.1911}

        handle_update(_text_update(42, "Milano centro"))

        self.assertEqual(WeatherSubscription.objects.filter(chat_id=42).count(), 1)
        self.assertIn("Sei già iscritto", mock_send.call_args[0][1])

    @patch("subscriptions.bot.send_message")
    def test_remove_valid_index(self, mock_send):
        WeatherSubscription.objects.create(chat_id=42, query_text="a", place_label="A", latitude="1", longitude="1")
        WeatherSubscription.objects.create(chat_id=42, query_text="b", place_label="B", latitude="2", longitude="2")

        handle_update(_text_update(42, "/remove 1"))

        remaining = list(WeatherSubscription.objects.filter(chat_id=42).values_list("place_label", flat=True))
        self.assertEqual(remaining, ["B"])

    @patch("subscriptions.bot.send_message")
    def test_remove_invalid_index_changes_nothing(self, mock_send):
        WeatherSubscription.objects.create(chat_id=42, query_text="a", place_label="A", latitude="1", longitude="1")
        handle_update(_text_update(42, "/remove 9"))
        self.assertEqual(WeatherSubscription.objects.filter(chat_id=42).count(), 1)
        self.assertIn("Numero non valido", mock_send.call_args[0][1])

    @patch("subscriptions.bot.send_message")
    def test_stop_removes_all_subscriptions_for_that_chat_only(self, mock_send):
        WeatherSubscription.objects.create(chat_id=42, query_text="a", place_label="A", latitude="1", longitude="1")
        WeatherSubscription.objects.create(chat_id=42, query_text="b", place_label="B", latitude="2", longitude="2")
        WeatherSubscription.objects.create(chat_id=7, query_text="c", place_label="C", latitude="3", longitude="3")

        handle_update(_text_update(42, "/stop"))

        self.assertEqual(WeatherSubscription.objects.filter(chat_id=42).count(), 0)
        self.assertEqual(WeatherSubscription.objects.filter(chat_id=7).count(), 1)


def _create_probe_with_yesterday_readings():
    probe = Probe.objects.create(
        name="p", hardware_type=Probe.HardwareType.RASPBERRY_PI_3,
        location_latitude="45.46", location_longitude="9.19", is_active=True,
    )
    yesterday = timezone.localtime(timezone.now()).date() - datetime.timedelta(days=1)
    base = timezone.make_aware(datetime.datetime.combine(yesterday, datetime.time(12, 0)))
    SensorReading.objects.create(probe=probe, sensor_type="temperature_c", value=20.0, time=base)
    SensorReading.objects.create(probe=probe, sensor_type="temperature_c", value=24.0, time=base + datetime.timedelta(minutes=1))
    SensorReading.objects.create(probe=probe, sensor_type="humidity_pct", value=50.0, time=base)
    SensorReading.objects.create(probe=probe, sensor_type="pressure_hpa", value=1012.0, time=base)
    SensorReading.objects.create(probe=probe, sensor_type="gas_resistance_ohm", value=80000.0, time=base)
    return probe


@override_settings(SUBSCRIPTION_MAX_DISTANCE_KM=15)
class SendDailyDigestTests(TestCase):
    @patch("subscriptions.management.commands.send_daily_digest.send_message")
    def test_sends_yesterdays_aggregates_to_a_nearby_subscription(self, mock_send):
        _create_probe_with_yesterday_readings()
        WeatherSubscription.objects.create(
            chat_id=1, query_text="q", place_label="Milano", latitude="45.4650", longitude="9.1910"
        )

        call_command("send_daily_digest")

        mock_send.assert_called_once()
        chat_id, text = mock_send.call_args[0]
        self.assertEqual(chat_id, 1)
        self.assertIn("min 20.0°C", text)
        self.assertIn("max 24.0°C", text)
        self.assertIn("media 22.0°C", text)
        self.assertIn("Buone notizie", text)  # first time this subscription got a probe in range

        self.assertTrue(WeatherSubscription.objects.get(chat_id=1).probe_ever_found)

    @patch("subscriptions.management.commands.send_daily_digest.send_message")
    def test_no_first_time_notice_once_already_notified(self, mock_send):
        _create_probe_with_yesterday_readings()
        WeatherSubscription.objects.create(
            chat_id=1, query_text="q", place_label="Milano", latitude="45.4650", longitude="9.1910",
            probe_ever_found=True,
        )

        call_command("send_daily_digest")

        self.assertNotIn("Buone notizie", mock_send.call_args[0][1])

    @patch("subscriptions.management.commands.send_daily_digest.send_message")
    def test_subscription_with_no_nearby_probe_is_skipped_silently(self, mock_send):
        _create_probe_with_yesterday_readings()
        WeatherSubscription.objects.create(
            chat_id=2, query_text="q", place_label="Roma", latitude="41.9", longitude="12.5"
        )

        call_command("send_daily_digest")

        mock_send.assert_not_called()

    @patch("subscriptions.management.commands.send_daily_digest.send_message")
    def test_missing_readings_render_as_not_available(self, mock_send):
        Probe.objects.create(
            name="p", hardware_type=Probe.HardwareType.RASPBERRY_PI_3,
            location_latitude="45.46", location_longitude="9.19", is_active=True,
        )
        WeatherSubscription.objects.create(
            chat_id=1, query_text="q", place_label="Milano", latitude="45.4650", longitude="9.1910"
        )

        call_command("send_daily_digest")

        self.assertIn("n/d", mock_send.call_args[0][1])

    @patch("subscriptions.management.commands.send_daily_digest.send_message")
    def test_readings_from_two_days_ago_are_not_counted(self, mock_send):
        probe = Probe.objects.create(
            name="p", hardware_type=Probe.HardwareType.RASPBERRY_PI_3,
            location_latitude="45.46", location_longitude="9.19", is_active=True,
        )
        two_days_ago = timezone.localtime(timezone.now()).date() - datetime.timedelta(days=2)
        stale_time = timezone.make_aware(datetime.datetime.combine(two_days_ago, datetime.time(12, 0)))
        SensorReading.objects.create(probe=probe, sensor_type="temperature_c", value=99.0, time=stale_time)
        WeatherSubscription.objects.create(
            chat_id=1, query_text="q", place_label="Milano", latitude="45.4650", longitude="9.1910"
        )

        call_command("send_daily_digest")

        text = mock_send.call_args[0][1]
        self.assertNotIn("99.0", text)
        self.assertIn("min n/d", text)
