"""Weather Service — structured weather data via Open-Meteo (free, no API key).

Detects weather-intent queries and returns structured JSON instead of
scraping fragile HTML pages. Falls back gracefully if the API is unreachable.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from august.utils.logger import get_logger, log_event

logger = get_logger("WeatherService")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OPEN_METEO_GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_WEATHER = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 6

WEATHER_MARKERS: frozenset[str] = frozenset({
    "weather", "temperature", "temp", "rain", "humidity",
    "wind", "forecast", "sunny", "cloudy", "storm",
    "precipitation", "heat", "cold", "fog", "snow",
})

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class WeatherResult:
    success: bool
    location: str = ""
    temperature: str = ""
    condition: str = ""
    humidity: str = ""
    wind: str = ""
    rain_chance: str = ""
    summary: str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def is_weather_query(normalized_query: str) -> bool:
    """Return True if the query is asking about weather."""
    return any(marker in normalized_query for marker in WEATHER_MARKERS)


def get_weather(location: str) -> WeatherResult:
    """Fetch structured weather for *location* using Open-Meteo.

    Returns a ``WeatherResult`` with all fields populated on success.
    """
    log_event(logger, "weather_fetch_started", source="weather_service", success=True, location=location)

    # Step 1 — Geocode the location name.
    try:
        geo_response = requests.get(
            OPEN_METEO_GEOCODING,
            params={"name": location, "count": 1, "language": "en"},
            timeout=REQUEST_TIMEOUT,
        )
        geo_response.raise_for_status()
        geo_data = geo_response.json()
    except Exception as exc:
        log_event(logger, "weather_geocode_failed", source="weather_service", success=False, location=location, error=str(exc))
        return WeatherResult(success=False)

    results = geo_data.get("results") or []
    if not results:
        log_event(logger, "weather_location_not_found", source="weather_service", success=False, location=location)
        return WeatherResult(success=False)

    geo = results[0]
    lat = geo.get("latitude", 0.0)
    lon = geo.get("longitude", 0.0)
    resolved_name = geo.get("name", location)

    # Step 2 — Fetch current weather + daily precipitation probability.
    try:
        weather_response = requests.get(
            OPEN_METEO_WEATHER,
            params={
                "latitude": lat,
                "longitude": lon,
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "daily": "precipitation_probability_max",
                "timezone": "auto",
            },
            timeout=REQUEST_TIMEOUT,
        )
        weather_response.raise_for_status()
        weather_data = weather_response.json()
    except Exception as exc:
        log_event(logger, "weather_fetch_failed", source="weather_service", success=False, location=location, error=str(exc))
        return WeatherResult(success=False)

    current = weather_data.get("current", {})
    daily = weather_data.get("daily", {})

    temp = current.get("temperature_2m")
    humidity = current.get("relative_humidity_2m")
    wind_speed = current.get("wind_speed_10m")
    weather_code = current.get("weather_code", 0)
    rain_probs = daily.get("precipitation_probability_max") or []
    rain = rain_probs[0] if rain_probs else None

    condition = _weather_code_to_text(int(weather_code))
    temp_str = f"{temp}\u00b0C" if temp is not None else "N/A"
    humidity_str = f"{humidity}%" if humidity is not None else "N/A"
    wind_str = f"{wind_speed} km/h" if wind_speed is not None else "N/A"
    rain_str = f"{rain}%" if rain is not None else "N/A"

    summary = f"{resolved_name}: {temp_str}, {condition}. Humidity {humidity_str}, wind {wind_str}."
    if rain is not None:
        summary += f" Rain chance {rain_str}."

    result = WeatherResult(
        success=True,
        location=resolved_name,
        temperature=temp_str,
        condition=condition,
        humidity=humidity_str,
        wind=wind_str,
        rain_chance=rain_str,
        summary=summary,
    )

    log_event(
        logger,
        "weather_fetch_success",
        source="weather_service",
        success=True,
        location=resolved_name,
        temperature=temp_str,
        condition=condition,
    )
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_WEATHER_CODE_MAP: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


def _weather_code_to_text(code: int) -> str:
    return _WEATHER_CODE_MAP.get(code, "Unknown")
