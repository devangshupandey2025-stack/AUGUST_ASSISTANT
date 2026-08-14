from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

import requests

from august.utils.logger import get_logger, log_event

logger = get_logger("WeatherService")

OPEN_METEO_GEOCODING = "https://geocoding-api.open-meteo.com/v1/search"
OPEN_METEO_WEATHER = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 6
CACHE_TTL = 600

WEATHER_CODES: dict[int, str] = {
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

_DEFAULT_CITY = "your area"


@dataclass
class WeatherData:
    success: bool
    location: str = ""
    temperature: float | None = None
    condition: str = ""
    humidity: float | None = None
    wind_speed: float | None = None
    rain_probability: float | None = None
    sunrise: str = ""
    sunset: str = ""
    summary: str = ""
    raw_text: str = ""
    structured: dict[str, object] = field(default_factory=dict)


class WeatherService:
    def __init__(self, cache_ttl: int = CACHE_TTL) -> None:
        self._cache: dict[str, tuple[float, WeatherData]] = {}
        self._cache_ttl = cache_ttl

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_weather(self, location: str) -> WeatherData:
        cache_key = self._make_cache_key(location)
        cached = self._get_cached(cache_key)
        if cached is not None:
            log_event(logger, "weather_cache_hit", source="weather_service",
                      success=True, location=location)
            return cached

        log_event(logger, "weather_geocoding_started", source="weather_service",
                  success=True, location=location)

        geo = self._geocode(location)
        if geo is None:
            log_event(logger, "weather_geocoding_failed", source="weather_service",
                      success=False, location=location, reason="no_results")
            return WeatherData(success=False)

        lat, lon, resolved_name = geo
        log_event(logger, "weather_geocoding_success", source="weather_service",
                  success=True, location=location, resolved_name=resolved_name,
                  lat=round(lat, 4), lon=round(lon, 4))

        log_event(logger, "weather_api_started", source="weather_service",
                  success=True, location=resolved_name)

        raw = self._fetch_weather(lat, lon)
        if raw is None:
            log_event(logger, "weather_api_failed", source="weather_service",
                      success=False, location=resolved_name, reason="api_error")
            return WeatherData(success=False)

        log_event(logger, "weather_api_success", source="weather_service",
                  success=True, location=resolved_name)

        result = self._build_result(raw, resolved_name)
        log_event(logger, "weather_cache_store", source="weather_service",
                  success=True, location=resolved_name)
        self._set_cached(cache_key, result)
        return result

    # ------------------------------------------------------------------
    # Geocoding
    # ------------------------------------------------------------------
    def _geocode(self, location: str) -> tuple[float, float, str] | None:
        try:
            response = requests.get(
                OPEN_METEO_GEOCODING,
                params={"name": location, "count": 5, "language": "en", "format": "json"},
                timeout=REQUEST_TIMEOUT,
            )
            response.raise_for_status()
        except requests.RequestException:
            return None

        try:
            data = response.json()
        except ValueError:
            return None

        results = data.get("results") or []
        if not results:
            return None

        best = self._select_best_result(results)
        if best is None:
            return None

        lat = best.get("latitude", 0.0)
        lon = best.get("longitude", 0.0)
        name = best.get("name", location)
        return lat, lon, name

    def _select_best_result(self, results: list[dict]) -> dict | None:
        if not results:
            return None
        return max(results, key=lambda r: r.get("population", 0) or 0)

    # ------------------------------------------------------------------
    # Weather API
    # ------------------------------------------------------------------
    def _fetch_weather(self, lat: float, lon: float) -> dict | None:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "precipitation_probability_max,sunrise,sunset",
            "timezone": "auto",
            "forecast_days": 1,
        }
        try:
            response = requests.get(
                OPEN_METEO_WEATHER, params=params, timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            return None

    # ------------------------------------------------------------------
    # Result building
    # ------------------------------------------------------------------
    def _build_result(self, raw: dict, location: str) -> WeatherData:
        current = raw.get("current", {})
        daily = raw.get("daily", {})

        temp = current.get("temperature_2m")
        humidity = current.get("relative_humidity_2m")
        wind = current.get("wind_speed_10m")
        weather_code = current.get("weather_code", 0)

        rain_probs = daily.get("precipitation_probability_max") or []
        rain = rain_probs[0] if rain_probs else None

        sunrises = daily.get("sunrise") or []
        sunset_times = daily.get("sunset") or []
        sunrise_str = sunrises[0] if sunrises else ""
        sunset_str = sunset_times[0] if sunset_times else ""

        condition = WEATHER_CODES.get(int(weather_code), "Unknown")

        temp_str = f"{temp}\u00b0C" if temp is not None else "N/A"
        humidity_str = f"{humidity}%" if humidity is not None else "N/A"
        wind_str = f"{wind} km/h" if wind is not None else "N/A"
        rain_str = f"{rain}%" if rain is not None else "N/A"

        structured: dict[str, object] = {}
        if temp is not None:
            structured["temperature"] = temp
        if humidity is not None:
            structured["humidity"] = humidity
        if wind is not None:
            structured["wind_speed"] = wind
        structured["condition"] = condition
        structured["location"] = location
        if rain is not None:
            structured["rain_probability"] = rain
        if sunrise_str:
            structured["sunrise"] = sunrise_str
        if sunset_str:
            structured["sunset"] = sunset_str

        raw_lines = [
            f"Location: {location}",
        ]
        if temp is not None:
            raw_lines.append(f"Temperature: {temp_str}")
        raw_lines.append(f"Condition: {condition}")
        if humidity is not None:
            raw_lines.append(f"Humidity: {humidity_str}")
        if wind is not None:
            raw_lines.append(f"Wind: {wind_str}")
        if rain is not None:
            raw_lines.append(f"Chance of rain: {rain_str}")
        if sunrise_str:
            raw_lines.append(f"Sunrise: {sunrise_str}")
        if sunset_str:
            raw_lines.append(f"Sunset: {sunset_str}")
        raw_text = "\n".join(raw_lines)

        summary_parts = [f"Current weather for {location}: {temp_str}, {condition}."]
        if humidity is not None:
            summary_parts.append(f"Humidity {humidity_str}.")
        if wind is not None:
            summary_parts.append(f"Wind {wind_str}.")
        if rain is not None:
            summary_parts.append(f"Chance of rain {rain_str}.")
        if sunrise_str and sunset_str:
            summary_parts.append(f"Sunrise at {sunrise_str}, sunset at {sunset_str}.")
        summary = " ".join(summary_parts)

        return WeatherData(
            success=True,
            location=location,
            temperature=temp,
            condition=condition,
            humidity=humidity,
            wind_speed=wind,
            rain_probability=rain,
            sunrise=sunrise_str,
            sunset=sunset_str,
            summary=summary,
            raw_text=raw_text,
            structured=structured,
        )

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------
    def _make_cache_key(self, location: str) -> str:
        today = date.today().isoformat()
        return f"weather|{location.strip().lower()}|{today}|current"

    def _get_cached(self, key: str) -> WeatherData | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        stored_time, data = entry
        if time.monotonic() - stored_time > self._cache_ttl:
            del self._cache[key]
            return None
        return data

    def _set_cached(self, key: str, data: WeatherData) -> None:
        self._cache[key] = (time.monotonic(), data)
