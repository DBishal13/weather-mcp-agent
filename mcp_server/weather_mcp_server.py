"""Weather-forecast MCP server (FastMCP, streamable-HTTP).

Tool functions here stay thin: parse args, call weather_broker.py, shape
the response, log it. All HTTP calls and response parsing live in
weather_broker.py, and secrets (none required for Open-Meteo; NWS_USER_AGENT
is not sensitive) would go through Databricks secrets via the _secret() helper
below if a future data source needed a real API key.

Run locally:  python weather_mcp_server.py
Deployed as a Databricks App, app.yaml points the app runtime at this file and
DATABRICKS_APP_PORT supplies the port to bind.
"""
import os

from mcp.server.fastmcp import FastMCP

import weather_broker
from weather_broker import WeatherAPIError
from query_log import record


def _secret(scope, key, env_fallback=None):
    """Databricks-secret lookup helper.

    Not currently needed - Open-Meteo requires no key - but kept here so adding
    a stretch data source that *does* need a key (e.g. WeatherAPI.com) is a
    one-line change instead of a new pattern.
    """
    value = os.environ.get(env_fallback) if env_fallback else None
    if value:
        return value
    try:
        from databricks.sdk import WorkspaceClient
        return WorkspaceClient().secrets.get_secret(scope, key).value
    except Exception as exc:
        raise RuntimeError(f"Could not resolve secret {scope}/{key}: {exc}") from exc


mcp = FastMCP(
    "weather-mcp-server",
    host="0.0.0.0",
    port=int(os.environ.get("DATABRICKS_APP_PORT", os.environ.get("PORT", 8000))),
)


@mcp.tool()
def get_current_weather(location: str) -> dict:
    """Fetches current conditions for a location.

    Args:
        location: City/region name, US zip code, or "City, ST"/"City, Country" string.

    Returns:
        dict with resolved_location, observed_at, temperature_f, feels_like_f,
        humidity_pct, wind_mph, wind_direction_deg, precipitation_in, conditions.
        On failure: {"error": "<clean message>"} - never a stack trace.
    """
    try:
        result = weather_broker.fetch_current_conditions(location)
    except WeatherAPIError as exc:
        result = {"error": str(exc)}
    record("get_current_weather", {"location": location}, result)
    return result


@mcp.tool()
def get_forecast(location: str, days: int = 3) -> dict:
    """Fetches a multi-day forecast for a location.

    Args:
        location: City/region name, US zip code, or "City, ST"/"City, Country" string.
        days: Number of days to forecast, 1-16 (default 3). Values outside that
            range are clamped.

    Returns:
        dict with resolved_location and days: a list of per-day dicts (date,
        temp_high_f, temp_low_f, precipitation_probability_pct, precipitation_in,
        wind_mph_max, conditions). On failure: {"error": "<clean message>"}.
    """
    try:
        result = weather_broker.fetch_daily_forecast(location, days=days)
    except WeatherAPIError as exc:
        result = {"error": str(exc)}
    record("get_forecast", {"location": location, "days": days}, result)
    return result


@mcp.tool()
def predict_umbrella_needed(location: str, date: str = None) -> dict:
    """Judges whether to bring an umbrella on a given day, from the raw forecast.

    Rule applied (not a passthrough): recommend an umbrella if the daily
    precipitation-probability forecast for that date exceeds 40%, or if
    forecast precipitation for the day exceeds 0.1 inches. Both are exposed in
    the response so the agent/user can see the numbers behind the call.

    Args:
        location: City/region name, US zip code, or "City, ST"/"City, Country" string.
        date: Target date as "YYYY-MM-DD". Defaults to the earliest available
            forecast day (today) if omitted.

    Returns:
        dict with resolved_location, date, precipitation_probability_pct,
        precipitation_in, umbrella_needed (bool), reason (str).
        On failure or if `date` is outside the available forecast window:
        {"error": "<clean message>"}.
    """
    try:
        forecast = weather_broker.fetch_daily_forecast(location, days=10)
        day = weather_broker.find_forecast_day(forecast["days"], date)
        if day is None:
            result = {"error": f"No forecast available for {location!r} on {date!r} (forecast window is the next 10 days)."}
        else:
            precip_pct = day["precipitation_probability_pct"]
            precip_in = day["precipitation_in"]
            needed = precip_pct > 40 or precip_in > 0.1
            reason = (
                f"{precip_pct}% chance of precipitation ({precip_in}in expected) "
                f"{'exceeds' if needed else 'is below'} the 40%/0.1in umbrella threshold."
            )
            result = {
                "resolved_location": forecast["resolved_location"],
                "date": day["date"],
                "precipitation_probability_pct": precip_pct,
                "precipitation_in": precip_in,
                "umbrella_needed": needed,
                "reason": reason,
            }
    except WeatherAPIError as exc:
        result = {"error": str(exc)}
    record("predict_umbrella_needed", {"location": location, "date": date}, result)
    return result


@mcp.tool()
def get_travel_recommendation(location: str, date: str = None) -> dict:
    """Gives a packing/travel recommendation for a given day, derived from the forecast.

    Rules applied (not a passthrough):
      - umbrella: precipitation probability > 40% or precipitation > 0.1in
      - jacket: forecast low temperature < 55F
      - sun protection: forecast high temperature > 90F
      - high wind caution: max wind speed > 25mph
    Each rule is evaluated independently and explained in `reasons`, so the
    agent can quote the specific numbers rather than asserting a vibe.

    Args:
        location: City/region name, US zip code, or "City, ST"/"City, Country" string.
        date: Target date as "YYYY-MM-DD". Defaults to the earliest available
            forecast day (today) if omitted.

    Returns:
        dict with resolved_location, date, conditions, bring_umbrella,
        bring_jacket, use_sun_protection, high_wind_caution (bools), and
        reasons (list of strings explaining each judgment).
        On failure or an out-of-window date: {"error": "<clean message>"}.
    """
    try:
        forecast = weather_broker.fetch_daily_forecast(location, days=10)
        day = weather_broker.find_forecast_day(forecast["days"], date)
        if day is None:
            result = {"error": f"No forecast available for {location!r} on {date!r} (forecast window is the next 10 days)."}
        else:
            bring_umbrella = day["precipitation_probability_pct"] > 40 or day["precipitation_in"] > 0.1
            bring_jacket = day["temp_low_f"] < 55
            use_sun_protection = day["temp_high_f"] > 90
            high_wind_caution = day["wind_mph_max"] > 25

            reasons = []
            reasons.append(
                f"Umbrella: {'yes' if bring_umbrella else 'no'} - "
                f"{day['precipitation_probability_pct']}% precip chance, {day['precipitation_in']}in expected."
            )
            reasons.append(
                f"Jacket: {'yes' if bring_jacket else 'no'} - low of {day['temp_low_f']}F "
                f"({'below' if bring_jacket else 'at/above'} the 55F threshold)."
            )
            reasons.append(
                f"Sun protection: {'yes' if use_sun_protection else 'no'} - high of {day['temp_high_f']}F "
                f"({'above' if use_sun_protection else 'at/below'} the 90F threshold)."
            )
            reasons.append(
                f"High wind caution: {'yes' if high_wind_caution else 'no'} - "
                f"gusts to {day['wind_mph_max']}mph ({'above' if high_wind_caution else 'at/below'} the 25mph threshold)."
            )

            result = {
                "resolved_location": forecast["resolved_location"],
                "date": day["date"],
                "conditions": day["conditions"],
                "bring_umbrella": bring_umbrella,
                "bring_jacket": bring_jacket,
                "use_sun_protection": use_sun_protection,
                "high_wind_caution": high_wind_caution,
                "reasons": reasons,
            }
    except WeatherAPIError as exc:
        result = {"error": str(exc)}
    record("get_travel_recommendation", {"location": location, "date": date}, result)
    return result


@mcp.tool()
def get_weather_alerts(location: str) -> dict:
    """(Stretch) Fetches active NWS severe weather alerts for a US location.

    Args:
        location: City/region name, US zip code, or "City, ST" string. Only
            US locations are supported (NWS has no international coverage).

    Returns:
        dict with resolved_location, supported (bool), alerts (list of dicts:
        event, headline, severity, effective, expires), and a note when
        supported is False (e.g. non-US location). On failure:
        {"error": "<clean message>"}.
    """
    try:
        result = weather_broker.fetch_active_alerts(location)
    except WeatherAPIError as exc:
        result = {"error": str(exc)}
    record("get_weather_alerts", {"location": location}, result)
    return result


@mcp.tool()
def compare_weather(locations: list[str]) -> dict:
    """(Stretch) Fetches current conditions for multiple locations side by side.

    Args:
        locations: List of 2-6 city/region strings to compare.

    Returns:
        dict with a "results" list, one entry per input location (each either
        the current-conditions dict or {"error": ...} if that one location
        failed to resolve - one bad location doesn't fail the whole call).
    """
    if not locations or len(locations) < 2:
        result = {"error": "Provide at least 2 locations to compare."}
        record("compare_weather", {"locations": locations}, result)
        return result

    results = []
    for loc in locations[:6]:
        try:
            results.append(weather_broker.fetch_current_conditions(loc))
        except WeatherAPIError as exc:
            results.append({"location": loc, "error": str(exc)})
    result = {"results": results}
    record("compare_weather", {"locations": locations}, result)
    return result


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
