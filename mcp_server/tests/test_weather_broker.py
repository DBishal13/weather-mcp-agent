"""Unit tests for weather_broker.py - the HTTP/parsing adapter.

All `requests` calls are mocked; no network access happens in this suite.
"""
from unittest.mock import MagicMock, patch

import pytest

import weather_broker as wb


def _resp(json_data):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = json_data
    return resp


CHICAGO = {
    "name": "Chicago",
    "country": "United States",
    "country_code": "US",
    "admin1": "Illinois",
    "latitude": 41.85,
    "longitude": -87.65,
    "timezone": "America/Chicago",
}


class TestDescribeWeatherCode:
    def test_known_code(self):
        assert wb.describe_weather_code(0) == "Clear sky"
        assert wb.describe_weather_code(61) == "Slight rain"

    def test_unknown_code(self):
        assert "Unknown" in wb.describe_weather_code(12345)


class TestGeocode:
    @patch("weather_broker.requests.get")
    def test_success(self, mock_get):
        mock_get.return_value = _resp({"results": [CHICAGO]})
        place = wb.geocode("Chicago, IL")
        assert place["name"] == "Chicago"
        assert place["country_code"] == "US"
        mock_get.assert_called_once()

    @patch("weather_broker.requests.get")
    def test_no_results_raises(self, mock_get):
        mock_get.return_value = _resp({"results": []})
        with pytest.raises(wb.WeatherAPIError):
            wb.geocode("Nowhereville")

    @patch("weather_broker.requests.get")
    def test_network_error_raises(self, mock_get):
        mock_get.side_effect = wb.requests.RequestException("boom")
        with pytest.raises(wb.WeatherAPIError):
            wb.geocode("Chicago")


class TestFetchCurrentConditions:
    @patch("weather_broker.requests.get")
    @patch("weather_broker.geocode")
    def test_shapes_response(self, mock_geocode, mock_get):
        mock_geocode.return_value = CHICAGO
        mock_get.return_value = _resp({
            "current": {
                "time": "2026-08-10T12:00",
                "temperature_2m": 75.0,
                "apparent_temperature": 77.0,
                "relative_humidity_2m": 50,
                "wind_speed_10m": 8.0,
                "wind_direction_10m": 180,
                "precipitation": 0.0,
                "weather_code": 1,
            }
        })
        result = wb.fetch_current_conditions("Chicago, IL")
        assert result["resolved_location"] == "Chicago, Illinois, United States"
        assert result["temperature_f"] == 75.0
        assert result["conditions"] == "Mainly clear"

    @patch("weather_broker.requests.get")
    @patch("weather_broker.geocode")
    def test_network_error_raises(self, mock_geocode, mock_get):
        mock_geocode.return_value = CHICAGO
        mock_get.side_effect = wb.requests.RequestException("timeout")
        with pytest.raises(wb.WeatherAPIError):
            wb.fetch_current_conditions("Chicago, IL")


class TestFetchDailyForecast:
    @patch("weather_broker.requests.get")
    @patch("weather_broker.geocode")
    def test_clamps_days_and_shapes_periods(self, mock_geocode, mock_get):
        mock_geocode.return_value = {**CHICAGO, "name": "Austin", "admin1": "Texas"}
        mock_get.return_value = _resp({
            "daily": {
                "time": ["2026-08-10"],
                "weather_code": [61],
                "temperature_2m_max": [90.0],
                "temperature_2m_min": [72.0],
                "precipitation_probability_max": [60],
                "precipitation_sum": [0.3],
                "wind_speed_10m_max": [12.0],
            }
        })
        result = wb.fetch_daily_forecast("Austin, TX", days=999)

        called_params = mock_get.call_args.kwargs["params"]
        assert called_params["forecast_days"] == 16  # clamped to the 1-16 range

        assert result["days"][0]["conditions"] == "Slight rain"
        assert result["days"][0]["precipitation_probability_pct"] == 60


class TestFindForecastDay:
    DAYS = [{"date": "2026-08-10"}, {"date": "2026-08-11"}]

    def test_defaults_to_first_when_no_date_given(self):
        assert wb.find_forecast_day(self.DAYS, None) == self.DAYS[0]

    def test_matches_requested_date(self):
        assert wb.find_forecast_day(self.DAYS, "2026-08-11") == self.DAYS[1]

    def test_returns_none_when_date_not_in_window(self):
        assert wb.find_forecast_day(self.DAYS, "2099-01-01") is None

    def test_returns_none_for_empty_forecast(self):
        assert wb.find_forecast_day([], None) is None


class TestFetchActiveAlerts:
    @patch("weather_broker.geocode")
    def test_non_us_location_is_unsupported(self, mock_geocode):
        mock_geocode.return_value = {
            "name": "Paris", "admin1": None, "country": "France",
            "country_code": "FR", "latitude": 48.85, "longitude": 2.35,
            "timezone": "Europe/Paris",
        }
        result = wb.fetch_active_alerts("Paris, France")
        assert result["supported"] is False
        assert result["alerts"] == []

    @patch("weather_broker.time.sleep")
    @patch("weather_broker._nws_get")
    @patch("weather_broker.geocode")
    def test_us_location_returns_alerts(self, mock_geocode, mock_nws_get, mock_sleep):
        mock_geocode.return_value = {
            "name": "Oklahoma City", "admin1": "Oklahoma", "country": "United States",
            "country_code": "US", "latitude": 35.47, "longitude": -97.52,
            "timezone": "America/Chicago",
        }
        mock_nws_get.side_effect = [
            {"properties": {"relativeLocation": {"properties": {"state": "OK"}}}},
            {"features": [{"properties": {
                "event": "Tornado Warning", "headline": "Tornado Warning for OKC",
                "severity": "Extreme",
                "effective": "2026-08-10T12:00:00Z", "expires": "2026-08-10T13:00:00Z",
            }}]},
        ]
        result = wb.fetch_active_alerts("Oklahoma City, OK")
        assert result["supported"] is True
        assert len(result["alerts"]) == 1
        assert result["alerts"][0]["event"] == "Tornado Warning"

    @patch("weather_broker._nws_get")
    @patch("weather_broker.geocode")
    def test_unresolvable_state_is_unsupported(self, mock_geocode, mock_nws_get):
        mock_geocode.return_value = {
            "name": "Somewhere", "admin1": None, "country": "United States",
            "country_code": "US", "latitude": 0.0, "longitude": 0.0,
            "timezone": "UTC",
        }
        mock_nws_get.return_value = {"properties": {"relativeLocation": {"properties": {}}}}
        result = wb.fetch_active_alerts("Somewhere, US")
        assert result["supported"] is False
