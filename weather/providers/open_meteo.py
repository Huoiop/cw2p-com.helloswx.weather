"""Open-Meteo。无需 API Key，仅按经纬度取数，附带在线城市搜索。"""

from datetime import datetime
from typing import Any

from .. import codes
from ..http import RequestError, build_url, get_json
from ..models import HourlyEntry, Location, MinutelySeries, Snapshot
from .base import ProviderError, WeatherProvider, register

_FORECAST = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={latitude}&longitude={longitude}"
    "&current=temperature_2m,weather_code,is_day"
    "&hourly=temperature_2m,weather_code,precipitation"
    "&minutely_15=precipitation"
    "&daily=temperature_2m_max,temperature_2m_min"
    "&timezone=auto&forecast_days=2"
)
_GEOCODING = "https://geocoding-api.open-meteo.com/v1/search?name={name}&count=30&language=zh&format=json"

_STATUS_FILE = "open_meteo_status.json"


@register
class OpenMeteoProvider(WeatherProvider):
    id = "open_meteo"
    name = "Open-Meteo"
    #: 全球数据源，城市通过 Open-Meteo 的地理编码接口在线搜索
    database = ""
    supports_coordinates = True
    requires_key = False
    supports_alerts = False

    def fetch(self, location: Location) -> Snapshot:
        if location.latitude is None or location.longitude is None:
            raise ProviderError("未设置经纬度")

        url = _FORECAST.format(latitude=location.latitude, longitude=location.longitude)
        try:
            payload = get_json(url, timeout=10.0)
        except RequestError as error:
            raise ProviderError(str(error)) from error

        table = codes.status_table(_STATUS_FILE)
        current = payload.get("current") or {}
        raw_code = current.get("weather_code")
        canonical = table.canonical(raw_code)
        is_day = current.get("is_day")
        night = (is_day == 0) if is_day is not None else self.is_night()

        snapshot = Snapshot(
            canonical=canonical,
            description=table.describe(raw_code),
            temperature=self.to_float(current.get("temperature_2m")),
            city=location.label,
            night=night,
        )

        daily = payload.get("daily") or {}
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        if highs:
            snapshot.temp_high = self.to_float(highs[0])
        if lows:
            snapshot.temp_low = self.to_float(lows[0])

        snapshot.hourly = self._parse_hourly(payload, table, night)
        snapshot.minutely = self._parse_minutely(payload)
        return snapshot

    def _parse_hourly(
        self, payload: dict[str, Any], table: codes.StatusTable, night: bool
    ) -> list[HourlyEntry]:
        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        temperatures = hourly.get("temperature_2m") or []
        weathers = hourly.get("weather_code") or []
        precipitation = hourly.get("precipitation") or []

        start = self._current_index(times)
        entries: list[HourlyEntry] = []
        for index in range(
            start, min(len(times), len(temperatures), len(weathers), start + 24)
        ):
            canonical = table.canonical(weathers[index])
            amount = (
                self.to_float(precipitation[index])
                if index < len(precipitation)
                else None
            )
            entries.append(
                HourlyEntry(
                    canonical=canonical,
                    temperature=self.to_float(temperatures[index]),
                    precipitating=(amount or 0.0) > 0
                    or codes.is_precipitation(canonical),
                    night=night,
                )
            )
        return entries

    def _parse_minutely(self, payload: dict[str, Any]) -> MinutelySeries | None:
        block = payload.get("minutely_15") or {}
        times = block.get("time") or []
        values = block.get("precipitation") or []
        if not times or not values:
            return None

        start = self._current_index(times)
        numbers = [self.to_float(item) or 0.0 for item in values[start : start + 16]]
        if not numbers:
            return None
        return MinutelySeries(values=numbers, step=15)

    @staticmethod
    def _current_index(times: list[str]) -> int:
        """本地时间序列中第一个不早于当前时刻的位置。"""
        now = datetime.now()
        for index, value in enumerate(times):
            try:
                moment = datetime.fromisoformat(str(value))
            except ValueError:
                continue
            if moment >= now:
                return index
        return 0

    def search_cities(self, term: str) -> list[dict[str, str]]:
        term = term.strip()
        if not term:
            return []
        try:
            payload = get_json(build_url(_GEOCODING, {"name": term}), timeout=10.0)
        except RequestError:
            return []

        results: list[dict[str, str]] = []
        for item in payload.get("results") or []:
            latitude = self.to_float(item.get("latitude"))
            longitude = self.to_float(item.get("longitude"))
            if latitude is None or longitude is None:
                continue
            parts = [str(item.get("name", ""))]
            for key in ("admin1", "country"):
                value = item.get(key)
                if value and str(value) not in parts:
                    parts.append(str(value))
            results.append(
                {
                    "name": " ".join(part for part in parts if part),
                    "code": "",
                    "latitude": f"{latitude}",
                    "longitude": f"{longitude}",
                }
            )
        return results
