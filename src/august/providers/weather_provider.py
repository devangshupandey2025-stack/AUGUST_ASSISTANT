from __future__ import annotations

from august.providers.base_provider import BaseProvider
from august.providers.provider_result import ProviderResult
from august.providers.utils import extract_location, is_weather_query
from august.providers.weather_service import WeatherService
from august.query_understanding import QueryIntent
from august.utils.logger import get_logger, log_event

logger = get_logger("WeatherProvider")

CONFIDENCE = 0.95


class WeatherProvider(BaseProvider):
    def __init__(self) -> None:
        self._service = WeatherService()

    def can_handle(self, intent: QueryIntent) -> bool:
        metadata = intent.metadata or {}
        topic_category = metadata.get("topic_category", "")
        if topic_category == "weather":
            log_event(logger, "weather_provider_selected", source="weather_provider",
                      success=True, query_type=intent.type,
                      topic_category=topic_category, reason="topic_category")
            return True

        raw = intent.raw_query
        if is_weather_query(raw):
            log_event(logger, "weather_provider_selected", source="weather_provider",
                      success=True, query_type=intent.type,
                      raw_query=raw, reason="weather_markers")
            return True

        return False

    def fetch(self, intent: QueryIntent) -> ProviderResult:
        location = extract_location(intent)
        if not location:
            log_event(logger, "weather_provider_failed", source="weather_provider",
                      success=False, reason="no_location")
            return ProviderResult(success=False, provider="Open-Meteo",
                                  confidence=0.0)

        log_event(logger, "weather_provider_fetch", source="weather_provider",
                  success=True, location=location)

        weather = self._service.get_weather(location)
        if not weather.success:
            log_event(logger, "weather_provider_failed", source="weather_provider",
                      success=False, location=location, reason="service_failed")
            return ProviderResult(success=False, provider="Open-Meteo",
                                  confidence=0.0)

        log_event(logger, "weather_provider_success", source="weather_provider",
                  success=True, location=weather.location,
                  temperature=weather.temperature,
                  condition=weather.condition)

        return ProviderResult(
            success=True,
            provider="Open-Meteo",
            confidence=CONFIDENCE,
            source="Open-Meteo",
            title=f"Weather for {weather.location}",
            summary=weather.summary,
            raw_text=weather.raw_text,
            url="",
            metadata={
                "location": weather.location,
                "temperature": weather.temperature,
                "condition": weather.condition,
                "humidity": weather.humidity,
                "wind_speed": weather.wind_speed,
                "rain_probability": weather.rain_probability,
                "sunrise": weather.sunrise,
                "sunset": weather.sunset,
            },
            structured_data=weather.structured,
        )
