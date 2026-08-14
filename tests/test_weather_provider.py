from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, Mock, patch

import requests

from august.providers.provider_router import AVAILABLE_PROVIDERS, ProviderRouter
from august.providers.utils import extract_location, is_weather_query
from august.providers.weather_provider import WeatherProvider
from august.providers.weather_service import WeatherData, WeatherService
from august.query_understanding import QueryIntent


def _make_intent(
    query_type: str = "definition",
    entities: list[str] | None = None,
    topic: str = "",
    raw_query: str = "",
    metadata: dict | None = None,
) -> QueryIntent:
    return QueryIntent(
        type=query_type,
        entities=entities or [],
        topic=topic or raw_query,
        raw_query=raw_query or topic or "test query",
        confidence=0.5,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# is_weather_query
# ---------------------------------------------------------------------------
class IsWeatherQueryTests(unittest.TestCase):
    def test_weather_markers(self) -> None:
        self.assertTrue(is_weather_query("what's the weather today"))
        self.assertTrue(is_weather_query("weather in kolkata"))
        self.assertTrue(is_weather_query("will it rain tomorrow"))
        self.assertTrue(is_weather_query("temperature in delhi"))
        self.assertTrue(is_weather_query("humidity in mumbai"))
        self.assertTrue(is_weather_query("wind speed in hyderabad"))
        self.assertTrue(is_weather_query("sunrise in kolkata"))
        self.assertTrue(is_weather_query("weather forecast for pune"))
        self.assertTrue(is_weather_query("current weather"))
        self.assertTrue(is_weather_query("is it raining"))

    def test_non_weather(self) -> None:
        self.assertFalse(is_weather_query("what is docker"))
        self.assertFalse(is_weather_query("who is alan turing"))
        self.assertFalse(is_weather_query("latest ai news"))
        self.assertFalse(is_weather_query("compare chatgpt and claude"))
        self.assertFalse(is_weather_query("open spotify"))
        self.assertFalse(is_weather_query("who is the chief minister of west bengal"))
        self.assertFalse(is_weather_query(""))
        self.assertFalse(is_weather_query("hello"))


# ---------------------------------------------------------------------------
# extract_location
# ---------------------------------------------------------------------------
class ExtractLocationTests(unittest.TestCase):
    def test_from_metadata_locations(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            metadata={"locations": ["Kolkata"], "topic_category": "weather"},
            raw_query="weather in kolkata",
        )
        self.assertEqual(extract_location(intent), "Kolkata")

    def test_from_entities(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            entities=["Delhi"],
            metadata={"topic_category": "weather"},
            raw_query="temperature in delhi",
        )
        self.assertEqual(extract_location(intent), "Delhi")

    def test_from_regex_in(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="weather in Mumbai",
            metadata={"topic_category": "weather"},
        )
        self.assertEqual(extract_location(intent), "Mumbai")

    def test_from_regex_for(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="forecast for Chennai",
        )
        self.assertEqual(extract_location(intent), "Chennai")

    def test_no_location_returns_none(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="what's the weather",
            metadata={"topic_category": "weather"},
        )
        self.assertIsNone(extract_location(intent))

    def test_empty_intent(self) -> None:
        intent = _make_intent(query_type="conversation", raw_query="hello")
        self.assertIsNone(extract_location(intent))


# ---------------------------------------------------------------------------
# WeatherService — geocoding
# ---------------------------------------------------------------------------
class WeatherServiceGeocodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = WeatherService()

    @patch("august.providers.weather_service.requests.get")
    def test_geocode_success(self, mock_get: MagicMock) -> None:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"name": "Kolkata", "latitude": 22.5726, "longitude": 88.3639,
                 "country": "India", "population": 4630000},
            ]
        }
        mock_get.return_value = mock_response

        result = self.service._geocode("Kolkata")
        self.assertIsNotNone(result)
        lat, lon, name = result
        self.assertAlmostEqual(lat, 22.5726)
        self.assertAlmostEqual(lon, 88.3639)
        self.assertEqual(name, "Kolkata")

    @patch("august.providers.weather_service.requests.get")
    def test_geocode_http_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.exceptions.ConnectionError("API down")
        result = self.service._geocode("Kolkata")
        self.assertIsNone(result)

    @patch("august.providers.weather_service.requests.get")
    def test_geocode_empty_results(self, mock_get: MagicMock) -> None:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"results": []}
        mock_get.return_value = mock_response
        result = self.service._geocode("UnknownCityXYZ")
        self.assertIsNone(result)

    @patch("august.providers.weather_service.requests.get")
    def test_geocode_selects_highest_population(self, mock_get: MagicMock) -> None:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"name": "Springfield", "latitude": 39.0, "longitude": -89.0,
                 "population": 15000},
                {"name": "Springfield", "latitude": 37.0, "longitude": -93.0,
                 "population": 170000},
            ]
        }
        mock_get.return_value = mock_response
        result = self.service._geocode("Springfield")
        self.assertIsNotNone(result)
        lat, lon, name = result
        self.assertAlmostEqual(lat, 37.0)
        self.assertAlmostEqual(lon, -93.0)


# ---------------------------------------------------------------------------
# WeatherService — weather API
# ---------------------------------------------------------------------------
class WeatherServiceFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = WeatherService()

    @patch("august.providers.weather_service.requests.get")
    def test_fetch_weather_success(self, mock_get: MagicMock) -> None:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "current": {
                "temperature_2m": 31.0,
                "relative_humidity_2m": 78.0,
                "weather_code": 2,
                "wind_speed_10m": 12.0,
            },
            "daily": {
                "precipitation_probability_max": [10.0],
                "sunrise": ["2026-07-02T05:42"],
                "sunset": ["2026-07-02T18:18"],
            },
        }
        mock_get.return_value = mock_response

        result = self.service._fetch_weather(22.5726, 88.3639)
        self.assertIsNotNone(result)
        self.assertEqual(result["current"]["temperature_2m"], 31.0)

    @patch("august.providers.weather_service.requests.get")
    def test_fetch_weather_http_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.exceptions.Timeout("timeout")
        result = self.service._fetch_weather(22.5726, 88.3639)
        self.assertIsNone(result)

    @patch("august.providers.weather_service.requests.get")
    def test_fetch_weather_parse_error(self, mock_get: MagicMock) -> None:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("bad json")
        mock_get.return_value = mock_response
        result = self.service._fetch_weather(22.5726, 88.3639)
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# WeatherService — get_weather (full flow)
# ---------------------------------------------------------------------------
class WeatherServiceGetWeatherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = WeatherService()

    @patch("august.providers.weather_service.requests.get")
    def test_get_weather_success(self, mock_get: MagicMock) -> None:
        geo_response = Mock()
        geo_response.status_code = 200
        geo_response.json.return_value = {
            "results": [
                {"name": "Kolkata", "latitude": 22.5726, "longitude": 88.3639,
                 "country": "India", "population": 4630000},
            ]
        }

        weather_response = Mock()
        weather_response.status_code = 200
        weather_response.json.return_value = {
            "current": {
                "temperature_2m": 31.0,
                "relative_humidity_2m": 78.0,
                "weather_code": 2,
                "wind_speed_10m": 12.0,
            },
            "daily": {
                "precipitation_probability_max": [10.0],
                "sunrise": ["2026-07-02T05:42"],
                "sunset": ["2026-07-02T18:18"],
            },
        }

        mock_get.side_effect = [geo_response, weather_response]

        result = self.service.get_weather("Kolkata")
        self.assertTrue(result.success)
        self.assertEqual(result.location, "Kolkata")
        self.assertAlmostEqual(result.temperature, 31.0)
        self.assertEqual(result.condition, "Partly cloudy")
        self.assertAlmostEqual(result.humidity, 78.0)
        self.assertAlmostEqual(result.wind_speed, 12.0)
        self.assertAlmostEqual(result.rain_probability, 10.0)
        self.assertIn("Sunrise", result.summary)
        self.assertIn("Kolkata", result.raw_text)
        self.assertIn("Partly cloudy", result.raw_text)
        self.assertEqual(result.structured.get("temperature"), 31.0)
        self.assertEqual(result.structured.get("condition"), "Partly cloudy")

    @patch("august.providers.weather_service.requests.get")
    def test_get_weather_geocode_failure(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = requests.exceptions.ConnectionError("API down")
        result = self.service.get_weather("UnknownCity")
        self.assertFalse(result.success)

    @patch("august.providers.weather_service.requests.get")
    def test_get_weather_weather_api_failure(self, mock_get: MagicMock) -> None:
        geo_response = Mock()
        geo_response.status_code = 200
        geo_response.json.return_value = {
            "results": [{"name": "Kolkata", "latitude": 22.5726, "longitude": 88.3639}]
        }
        weather_response = Mock()
        weather_response.status_code = 500
        weather_response.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")

        mock_get.side_effect = [geo_response, weather_response]

        result = self.service.get_weather("Kolkata")
        self.assertFalse(result.success)


# ---------------------------------------------------------------------------
# WeatherService — caching
# ---------------------------------------------------------------------------
class WeatherServiceCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = WeatherService(cache_ttl=600)

    def test_cache_key_format(self) -> None:
        key = self.service._make_cache_key("Kolkata")
        today = date.today().isoformat()
        self.assertEqual(key, f"weather|kolkata|{today}|current")

    def test_cache_set_and_get(self) -> None:
        data = WeatherData(success=True, location="Kolkata", temperature=31.0)
        key = "weather|kolkata|2026-07-02|current"
        self.service._set_cached(key, data)
        cached = self.service._get_cached(key)
        self.assertIsNotNone(cached)
        self.assertTrue(cached.success)
        self.assertEqual(cached.location, "Kolkata")

    def test_cache_expiry(self) -> None:
        service = WeatherService(cache_ttl=-1)
        data = WeatherData(success=True, location="Kolkata")
        key = "weather|kolkata|2026-07-02|current"
        service._set_cached(key, data)
        cached = service._get_cached(key)
        self.assertIsNone(cached)

    def test_cache_miss(self) -> None:
        result = self.service._get_cached("nonexistent_key")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# WeatherProvider — can_handle
# ---------------------------------------------------------------------------
class WeatherProviderCanHandleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = WeatherProvider()

    def test_weather_topic_category(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            metadata={"topic_category": "weather"},
            raw_query="what's the weather today",
        )
        self.assertTrue(self.provider.can_handle(intent))

    def test_weather_today(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="what's the weather today",
            metadata={"topic_category": "weather"},
        )
        self.assertTrue(self.provider.can_handle(intent))

    def test_weather_in_city(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="weather in Kolkata",
            metadata={"locations": ["Kolkata"], "topic_category": "weather"},
        )
        self.assertTrue(self.provider.can_handle(intent))

    def test_will_it_rain(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="will it rain tomorrow",
        )
        self.assertTrue(self.provider.can_handle(intent))

    def test_temperature_in_delhi(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="temperature in delhi",
            metadata={"locations": ["Delhi"]},
        )
        self.assertTrue(self.provider.can_handle(intent))

    def test_humidity_in_mumbai(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="humidity in mumbai",
            metadata={"locations": ["Mumbai"]},
        )
        self.assertTrue(self.provider.can_handle(intent))

    def test_wind_speed(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="wind speed in hyderabad",
        )
        self.assertTrue(self.provider.can_handle(intent))

    def test_sunrise(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="sunrise in kolkata",
        )
        self.assertTrue(self.provider.can_handle(intent))

    def test_weather_forecast(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="weather forecast for pune",
        )
        self.assertTrue(self.provider.can_handle(intent))

    def test_current_weather(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="current weather",
        )
        self.assertTrue(self.provider.can_handle(intent))

    def test_rejects_definition(self) -> None:
        intent = _make_intent(
            query_type="definition",
            raw_query="what is docker",
            metadata={"topic_category": "technology"},
        )
        self.assertFalse(self.provider.can_handle(intent))

    def test_rejects_person_query(self) -> None:
        intent = _make_intent(
            query_type="definition",
            raw_query="who is alan turing",
            metadata={"topic_category": "general"},
        )
        self.assertFalse(self.provider.can_handle(intent))

    def test_rejects_news(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="latest ai news",
            metadata={"topic_category": "news"},
        )
        self.assertFalse(self.provider.can_handle(intent))

    def test_rejects_comparison(self) -> None:
        intent = _make_intent(
            query_type="comparison",
            raw_query="compare chatgpt and claude",
        )
        self.assertFalse(self.provider.can_handle(intent))

    def test_rejects_conversation(self) -> None:
        intent = _make_intent(
            query_type="conversation",
            raw_query="hello",
        )
        self.assertFalse(self.provider.can_handle(intent))

    def test_rejects_office_holder(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="who is the chief minister of west bengal",
            metadata={"offices": ["Chief Minister"], "topic_category": "government"},
        )
        self.assertFalse(self.provider.can_handle(intent))


# ---------------------------------------------------------------------------
# WeatherProvider — fetch
# ---------------------------------------------------------------------------
class WeatherProviderFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = WeatherProvider()

    @patch.object(WeatherService, "get_weather")
    def test_fetch_success(self, mock_get_weather: MagicMock) -> None:
        mock_get_weather.return_value = WeatherData(
            success=True,
            location="Kolkata",
            temperature=31.0,
            condition="Partly cloudy",
            humidity=78.0,
            wind_speed=12.0,
            rain_probability=10.0,
            sunrise="2026-07-02T05:42",
            sunset="2026-07-02T18:18",
            summary="Current weather for Kolkata: 31°C, Partly cloudy. Humidity 78%. Wind 12 km/h. Chance of rain 10%.",
            raw_text="Location: Kolkata\nTemperature: 31°C\nCondition: Partly cloudy\nHumidity: 78%\nWind: 12 km/h\nChance of rain: 10%\nSunrise: 2026-07-02T05:42\nSunset: 2026-07-02T18:18",
            structured={"temperature": 31.0, "condition": "Partly cloudy",
                        "humidity": 78.0, "wind_speed": 12.0, "rain_probability": 10.0,
                        "location": "Kolkata", "sunrise": "2026-07-02T05:42",
                        "sunset": "2026-07-02T18:18"},
        )

        intent = _make_intent(
            query_type="dynamic_fact",
            entities=["Kolkata"],
            raw_query="weather in kolkata",
            metadata={"locations": ["Kolkata"], "topic_category": "weather"},
        )

        result = self.provider.fetch(intent)
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "Open-Meteo")
        self.assertEqual(result.confidence, 0.95)
        self.assertEqual(result.title, "Weather for Kolkata")
        self.assertIn("Kolkata", result.summary)
        self.assertIn("Partly cloudy", result.raw_text)
        self.assertEqual(result.structured_data.get("temperature"), 31.0)
        self.assertEqual(result.metadata.get("location"), "Kolkata")

    @patch.object(WeatherService, "get_weather")
    def test_fetch_service_failure(self, mock_get_weather: MagicMock) -> None:
        mock_get_weather.return_value = WeatherData(success=False)
        intent = _make_intent(
            query_type="dynamic_fact",
            entities=["Kolkata"],
            raw_query="weather in kolkata",
            metadata={"locations": ["Kolkata"], "topic_category": "weather"},
        )
        result = self.provider.fetch(intent)
        self.assertFalse(result.success)
        self.assertEqual(result.provider, "Open-Meteo")

    def test_fetch_no_location(self) -> None:
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="what's the weather",
            metadata={"topic_category": "weather"},
        )
        result = self.provider.fetch(intent)
        self.assertFalse(result.success)
        self.assertEqual(result.provider, "Open-Meteo")


# ---------------------------------------------------------------------------
# ProviderRouter — weather routing
# ---------------------------------------------------------------------------
class WeatherProviderRouterTests(unittest.TestCase):
    def test_weather_queries_are_in_available_providers(self) -> None:
        provider_types = {type(p).__name__ for p in AVAILABLE_PROVIDERS}
        self.assertIn("WeatherProvider", provider_types)

    def test_weather_query_routes_to_weather_provider(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="dynamic_fact",
            entities=["Kolkata"],
            raw_query="weather in kolkata",
            metadata={"locations": ["Kolkata"], "topic_category": "weather"},
        )

        with patch.object(WeatherService, "get_weather") as mock_get:
            mock_get.return_value = WeatherData(
                success=True, location="Kolkata", temperature=31.0,
                condition="Partly cloudy", humidity=78.0, wind_speed=12.0,
            )
            result = router.route(intent)

        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "Open-Meteo")

    def test_weather_without_location_falls_through(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="dynamic_fact",
            raw_query="what's the weather",
            metadata={"topic_category": "weather"},
        )
        result = router.route(intent)
        self.assertIsNone(result)

    def test_wikipedia_still_handles_definitions(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="definition",
            entities=["Python"],
            raw_query="what is python",
            metadata={"topic_category": "technology"},
        )

        with patch("august.providers.wikipedia_provider.requests.get") as mock_get:
            search_resp = Mock()
            search_resp.status_code = 200
            search_resp.json.return_value = {
                "query": {"search": [{"title": "Python (programming language)", "pageid": 1}]}
            }
            summary_resp = Mock()
            summary_resp.status_code = 200
            summary_resp.json.return_value = {
                "title": "Python (programming language)",
                "extract": "Python is a high-level programming language." * 20,
                "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Python"}},
                "type": "standard",
            }
            mock_get.side_effect = [search_resp, summary_resp]

            result = router.route(intent)

        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "Wikipedia")


# ---------------------------------------------------------------------------
# Integration: web_research with weather provider
# ---------------------------------------------------------------------------
class WeatherProviderIntegrationTests(unittest.TestCase):
    def test_weather_query_handled_by_weather_provider(self) -> None:
        from august.web_research import WebResearchEngine

        engine = WebResearchEngine()
        intent = _make_intent(
            query_type="dynamic_fact",
            entities=["Kolkata"],
            raw_query="weather in kolkata",
            metadata={"locations": ["Kolkata"], "topic_category": "weather"},
        )

        provider_router = ProviderRouter()

        with patch.object(WeatherService, "get_weather") as mock_get:
            mock_get.return_value = WeatherData(
                success=True, location="Kolkata", temperature=31.0,
                condition="Partly cloudy", humidity=78.0, wind_speed=12.0,
            )
            result = provider_router.route(intent)

        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "Open-Meteo")

    def test_non_weather_query_still_uses_wikipedia(self) -> None:
        router = ProviderRouter()
        intent = _make_intent(
            query_type="definition",
            raw_query="what is polymorphism",
            entities=["Polymorphism"],
        )

        with patch("august.providers.wikipedia_provider.requests.get") as mock_get:
            search_resp = Mock()
            search_resp.status_code = 200
            search_resp.json.return_value = {
                "query": {"search": [{"title": "Polymorphism", "pageid": 12345}]}
            }
            summary_resp = Mock()
            summary_resp.status_code = 200
            summary_resp.json.return_value = {
                "title": "Polymorphism",
                "extract": "Polymorphism is the provision of a single interface." * 20,
                "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Polymorphism"}},
                "type": "standard",
            }
            mock_get.side_effect = [search_resp, summary_resp]

            result = router.route(intent)

        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        self.assertEqual(result.provider, "Wikipedia")


if __name__ == "__main__":
    unittest.main()
