"""Weather data adapter: all HTTP calls and response parsing live here.

Open-Meteo (https://open-meteo.com) is the primary source for current conditions
and forecasts - global coverage, no signup, no API key. NWS (api.weather.gov) is
used only for the US-only severe-weather-alerts stretch tool; that geocode ->
grid-point -> alerts shape is adapted from the sibling homework at
D:\\DBX\\weather-lakebase-app\\weather_client.py, which does the same lookup to
sync NWS alerts/forecasts into Lakebase for semantic search.

weather_mcp_server.py's @mcp.tool functions stay thin: they call functions here
and shape the result into a dict. No `requests` calls belong in the tool layer.
"""
import os
import time

import requests

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
NWS_BASE_URL = "https://api.weather.gov"


def _user_agent():
    return os.environ.get("NWS_USER_AGENT", "weather-mcp-homework (contact: unknown@example.com)")


class WeatherAPIError(Exception):
    """Raised for any upstream failure: bad location, network error, non-2xx response.

    Caught at the MCP tool layer in weather_mcp_server.py and turned into a clean
    {"error": "..."} dict instead of a stack trace reaching the agent.
    """


WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
    56: "Light freezing drizzle", 57: "Dense freezing drizzle",
    61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
    66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow fall", 73: "Moderate snow fall", 75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
    85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
}


def describe_weather_code(code):
    return WMO_CODES.get(int(code), f"Unknown conditions (code {code})")


def geocode(location):
    """Resolves free-text location ('Chicago, IL' / 'Paris' / '60601') to lat/lon + metadata.

    Returns dict: name, country, country_code, admin1 (state/region), latitude,
    longitude, timezone. Raises WeatherAPIError if the location can't be resolved.
    """
    try:
        resp = requests.get(
            GEOCODE_URL,
            params={"name": location, "count": 1, "language": "en", "format": "json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise WeatherAPIError(f"Geocoding request failed for {location!r}: {exc}") from exc

    results = data.get("results")
    if not results:
        raise WeatherAPIError(f"Could not find a location matching {location!r}")

    top = results[0]
    return {
        "name": top.get("name"),
        "country": top.get("country"),
        "country_code": top.get("country_code"),
        "admin1": top.get("admin1"),
        "latitude": top["latitude"],
        "longitude": top["longitude"],
        "timezone": top.get("timezone", "auto"),
    }


def _label(place):
    parts = [p for p in [place.get("name"), place.get("admin1"), place.get("country")] if p]
    return ", ".join(parts)


def fetch_current_conditions(location):
    """Geocodes `location` then fetches current temperature/humidity/wind/conditions.

    Returns a flat dict ready for the MCP tool to hand back to the agent.
    """
    place = geocode(location)
    params = {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto",
    }
    try:
        resp = requests.get(FORECAST_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise WeatherAPIError(f"Current-conditions request failed for {location!r}: {exc}") from exc

    current = data["current"]
    return {
        "resolved_location": _label(place),
        "observed_at": current["time"],
        "temperature_f": current["temperature_2m"],
        "feels_like_f": current["apparent_temperature"],
        "humidity_pct": current["relative_humidity_2m"],
        "wind_mph": current["wind_speed_10m"],
        "wind_direction_deg": current["wind_direction_10m"],
        "precipitation_in": current["precipitation"],
        "conditions": describe_weather_code(current["weather_code"]),
    }


def fetch_daily_forecast(location, days=3):
    """Geocodes `location` then fetches a `days`-day daily forecast.

    Returns dict with resolved_location + a list of per-day dicts (date,
    highs/lows, precipitation chance, conditions) - the raw material
    predict_umbrella_needed() and get_travel_recommendation() apply judgment to.
    """
    days = max(1, min(int(days), 16))
    place = geocode(location)
    params = {
        "latitude": place["latitude"],
        "longitude": place["longitude"],
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum,wind_speed_10m_max",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto",
        "forecast_days": days,
    }
    try:
        resp = requests.get(FORECAST_URL, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise WeatherAPIError(f"Forecast request failed for {location!r}: {exc}") from exc

    daily = data["daily"]
    periods = [
        {
            "date": day_str,
            "temp_high_f": daily["temperature_2m_max"][i],
            "temp_low_f": daily["temperature_2m_min"][i],
            "precipitation_probability_pct": daily["precipitation_probability_max"][i],
            "precipitation_in": daily["precipitation_sum"][i],
            "wind_mph_max": daily["wind_speed_10m_max"][i],
            "conditions": describe_weather_code(daily["weather_code"][i]),
        }
        for i, day_str in enumerate(daily["time"])
    ]

    return {"resolved_location": _label(place), "days": periods}


def find_forecast_day(forecast_days, target_date):
    """Picks the forecast period matching target_date (YYYY-MM-DD); falls back to the first entry if target_date is None."""
    if not forecast_days:
        return None
    if target_date is None:
        return forecast_days[0]
    for day in forecast_days:
        if day["date"] == target_date:
            return day
    return None


# ---- NWS severe weather alerts (US-only stretch tool) ----
# Adapted from the geocode -> grid-point -> alerts shape in
# D:\DBX\weather-lakebase-app\weather_client.py (resolve_grid_point / fetch_active_alerts).

def _nws_get(url, params=None, timeout=15):
    try:
        resp = requests.get(
            url,
            params=params,
            headers={"User-Agent": _user_agent(), "Accept": "application/geo+json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise WeatherAPIError(f"NWS request failed: {exc}") from exc


def fetch_active_alerts(location):
    """Looks up active NWS severe weather alerts for `location`'s US state.

    Returns a dict with supported=False (and an explanatory note) for
    non-US locations, since NWS has no coverage outside the US.
    """
    place = geocode(location)
    if (place.get("country_code") or "").upper() != "US":
        return {
            "resolved_location": _label(place),
            "supported": False,
            "alerts": [],
            "note": "NWS alerts only cover US locations.",
        }

    point = _nws_get(f"{NWS_BASE_URL}/points/{place['latitude']:.4f},{place['longitude']:.4f}")
    state = point["properties"].get("relativeLocation", {}).get("properties", {}).get("state")
    if not state:
        return {
            "resolved_location": _label(place),
            "supported": False,
            "alerts": [],
            "note": "Could not resolve a US state for this point.",
        }

    time.sleep(0.1)
    data = _nws_get(f"{NWS_BASE_URL}/alerts/active", params={"area": state})
    alerts = [
        {
            "event": f["properties"].get("event"),
            "headline": f["properties"].get("headline"),
            "severity": f["properties"].get("severity"),
            "effective": f["properties"].get("effective"),
            "expires": f["properties"].get("expires"),
        }
        for f in data.get("features", [])
    ]
    return {"resolved_location": _label(place), "supported": True, "alerts": alerts}
