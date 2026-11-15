"""Тесты без сети: requests, sleep и ключ подменяются.

Запуск из корня проекта:
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import requests

import weather_app


def make_weather(temp: float = 12.4, description: str = "пасмурно") -> dict:
    return {
        "name": "Москва",
        "main": {"temp": temp, "feels_like": 10.8, "humidity": 77},
        "weather": [{"main": "Clouds", "description": description}],
        "wind": {"speed": 3.4},
    }


class FakeResponse:
    """Минимальная замена requests.Response."""

    def __init__(self, status_code: int = 200, payload=None, reason: str = "OK"):
        self.status_code = status_code
        self.reason = reason
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class WeatherTestCase(unittest.TestCase):
    """Общая обвязка: подставной ключ и настоящий requests.get."""

    def setUp(self):
        patcher = mock.patch.object(weather_app, "API_KEY", "test-key")
        patcher.start()
        self.addCleanup(patcher.stop)


class GetCoordinatesTest(WeatherTestCase):
    def test_returns_lat_lon_and_passes_params(self):
        payload = [{"name": "Москва", "lat": 55.752, "lon": 37.6156}]
        with mock.patch.object(
            weather_app.requests, "get", return_value=FakeResponse(payload=payload)
        ) as get:
            result = weather_app.get_coordinates("Москва")

        self.assertEqual(result, (55.752, 37.6156))
        url = get.call_args.args[0]
        params = get.call_args.kwargs["params"]
        self.assertEqual(url, weather_app.GEOCODING_URL)
        self.assertEqual(params, {"q": "Москва", "limit": 1, "appid": "test-key", "lang": "ru"})

    def test_empty_result_is_readable(self):
        with mock.patch.object(
            weather_app.requests, "get", return_value=FakeResponse(payload=[])
        ):
            with self.assertRaises(weather_app.WeatherError) as ctx:
                weather_app.get_coordinates("Атлантида")
        self.assertIn("не найден", str(ctx.exception))

    def test_empty_city_is_rejected_without_request(self):
        with mock.patch.object(weather_app.requests, "get") as get:
            with self.assertRaises(weather_app.WeatherError):
                weather_app.get_coordinates("   ")
        get.assert_not_called()

    def test_invalid_key_message(self):
        response = FakeResponse(401, payload={"cod": 401, "message": "Invalid API key"})
        with mock.patch.object(weather_app.requests, "get", return_value=response):
            with self.assertRaises(weather_app.WeatherError) as ctx:
                weather_app.get_coordinates("Москва")
        message = str(ctx.exception)
        self.assertIn("401", message)
        self.assertIn("API_KEY", message)

    def test_missing_key_message(self):
        with mock.patch.object(weather_app, "API_KEY", ""), mock.patch.object(
            weather_app, "requests", autospec=True
        ) as requests_mock:
            with self.assertRaises(weather_app.WeatherError) as ctx:
                weather_app.get_coordinates("Москва")
        self.assertIn(".env", str(ctx.exception))
        requests_mock.get.assert_not_called()


class GetWeatherTest(WeatherTestCase):
    def test_returns_payload_and_passes_params(self):
        payload = make_weather()
        with mock.patch.object(
            weather_app.requests, "get", return_value=FakeResponse(payload=payload)
        ) as get:
            result = weather_app.get_weather_by_coordinates(55.752, 37.6156)

        self.assertEqual(result, payload)
        params = get.call_args.kwargs["params"]
        self.assertEqual(params["units"], "metric")
        self.assertEqual(params["lang"], "ru")
        self.assertEqual(params["lat"], 55.752)
        self.assertEqual(params["lon"], 37.6156)

    def test_retries_on_429_then_succeeds(self):
        responses = [FakeResponse(429), FakeResponse(payload=make_weather())]
        with mock.patch.object(
            weather_app.requests, "get", side_effect=responses
        ) as get, mock.patch.object(weather_app.time, "sleep") as sleep:
            result = weather_app.get_weather_by_coordinates(55.0, 37.0)

        self.assertIn("main", result)
        self.assertEqual(get.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_network_error_retried_three_times_then_gives_up(self):
        with mock.patch.object(
            weather_app.requests, "get", side_effect=requests.ConnectionError("no network")
        ) as get, mock.patch.object(weather_app.time, "sleep") as sleep:
            with self.assertRaises(weather_app.WeatherUnavailableError) as ctx:
                weather_app.get_weather_by_coordinates(55.0, 37.0)

        self.assertEqual(get.call_count, 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [1, 2, 4])
        self.assertIn("Не удалось связаться", str(ctx.exception))

    def test_429_exhausted_is_unavailable(self):
        with mock.patch.object(
            weather_app.requests, "get", return_value=FakeResponse(429)
        ), mock.patch.object(weather_app.time, "sleep"):
            with self.assertRaises(weather_app.WeatherUnavailableError):
                weather_app.get_weather_by_coordinates(55.0, 37.0)

    def test_404_is_not_retried(self):
        with mock.patch.object(
            weather_app.requests, "get", return_value=FakeResponse(404)
        ) as get, mock.patch.object(weather_app.time, "sleep") as sleep:
            with self.assertRaises(weather_app.WeatherError):
                weather_app.get_weather_by_coordinates(55.0, 37.0)
        self.assertEqual(get.call_count, 1)
        sleep.assert_not_called()

    def test_coordinates_validation(self):
        for lat, lon in ((91, 37), (-91, 37), (55, 181), (55, -181)):
            with self.assertRaises(weather_app.WeatherError):
                weather_app.get_weather_by_coordinates(lat, lon)
        with self.assertRaises(weather_app.WeatherError):
            weather_app.get_weather_by_coordinates("широта", 37)

    def test_non_json_response(self):
        with mock.patch.object(
            weather_app.requests, "get", return_value=FakeResponse(payload=None)
        ):
            with self.assertRaises(weather_app.WeatherError) as ctx:
                weather_app.get_weather_by_coordinates(55.0, 37.0)
        self.assertIn("не JSON", str(ctx.exception))

    def test_missing_main_block(self):
        with mock.patch.object(
            weather_app.requests, "get", return_value=FakeResponse(payload={"cod": 200})
        ):
            with self.assertRaises(weather_app.WeatherError):
                weather_app.get_weather_by_coordinates(55.0, 37.0)


class FormattingTest(unittest.TestCase):
    def test_main_line_matches_task_example(self):
        line = weather_app.format_weather(make_weather(), "Москва")
        self.assertEqual(line, "Погода в Москва: 12.4°C, пасмурно")

    def test_without_description(self):
        weather = make_weather()
        weather["weather"] = []
        self.assertEqual(weather_app.format_weather(weather, "Минск"), "Погода в Минск: 12.4°C")

    def test_details_line(self):
        details = weather_app.format_details(make_weather())
        self.assertIn("ощущается как 10.8°C", details)
        self.assertIn("влажность 77%", details)
        self.assertIn("ветер 3.4 м/с", details)

    def test_format_without_temp_raises(self):
        with self.assertRaises(weather_app.WeatherError):
            weather_app.format_weather({}, "Москва")


class ParseCoordinatesTest(unittest.TestCase):
    def test_separators(self):
        for text in ("55.7520, 37.6156", "55.7520 37.6156", "55.7520;37.6156"):
            self.assertEqual(weather_app._parse_coordinates(text), (55.752, 37.6156))

    def test_bad_input(self):
        for text in ("", "55.75", "a, b", "200, 37"):
            with self.assertRaises(weather_app.WeatherError):
                weather_app._parse_coordinates(text)


class CacheTest(unittest.TestCase):
    def test_roundtrip_keeps_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weather_cache.json"
            weather = make_weather()
            weather_app.save_cache(weather, "Москва", 55.752, 37.6156, path)

            entry = weather_app.load_cache(path)
            self.assertEqual(entry["city"], "Москва")
            self.assertEqual(entry["lat"], 55.752)
            self.assertEqual(entry["lon"], 37.6156)
            self.assertEqual(entry["weather"], weather)
            self.assertIsNotNone(weather_app.cache_age_hours(entry))
            self.assertLess(weather_app.cache_age_hours(entry), 1)

            with open(path, encoding="utf-8") as file:
                raw = json.load(file)
            self.assertIn("fetched_at", raw)

    def test_old_cache_age(self):
        entry = {
            "city": "Москва",
            "fetched_at": (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(),
        }
        self.assertGreaterEqual(weather_app.cache_age_hours(entry), 3)

    def test_missing_and_corrupt_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "weather_cache.json"
            self.assertIsNone(weather_app.load_cache(path))

            path.write_text("{ это не json", encoding="utf-8")
            self.assertIsNone(weather_app.load_cache(path))
            self.assertIsNone(weather_app.cache_age_hours(None))

    def test_naive_timestamp_is_accepted(self):
        entry = {"fetched_at": "2026-11-15T10:00:00"}
        self.assertIsNotNone(weather_app.cache_age_hours(entry))


if __name__ == "__main__":
    unittest.main()
