"""和风天气。需要 API Key，支持 LocationID 与经纬度，提供预警和分钟级降水。"""

from typing import Any

from .. import codes
from ..alerts import build_alert
from ..http import RequestError, get_json
from ..models import COORDINATES, HourlyEntry, Location, MinutelySeries, Snapshot
from .base import ProviderError, WeatherProvider, register

_HOST = "https://devapi.qweather.com"
_STATUS_FILE = "qweather_status.json"


@register
class QWeatherProvider(WeatherProvider):
    id = "qweather"
    name = "和风天气"
    database = "xiaomi_weather.db"
    supports_coordinates = True
    requires_key = True
    supports_alerts = True

    def fetch(self, location: Location) -> Snapshot:
        if not self.api_key:
            raise ProviderError("缺少 API Key")

        target = self._location_param(location)
        if not target:
            raise ProviderError("未设置位置")

        now = self._request("/v7/weather/now", target)
        table = codes.status_table(_STATUS_FILE)
        current = now.get("now") or {}
        raw_code = current.get("icon")
        canonical = table.canonical(raw_code)

        snapshot = Snapshot(
            canonical=canonical,
            description=str(current.get("text") or table.describe(raw_code)),
            temperature=self.to_float(current.get("temp")),
            city=location.label,
            night=self.is_night(),
        )

        self._apply_daily(snapshot, target)
        snapshot.hourly = self._fetch_hourly(target, table, snapshot.night)
        snapshot.minutely = self._fetch_minutely(location)
        snapshot.alerts = self._fetch_alerts(target)
        return snapshot

    @staticmethod
    def _location_param(location: Location) -> str:
        if location.mode == COORDINATES or not location.code:
            if location.longitude is None or location.latitude is None:
                return ""
            # 和风的经纬度参数是「经度,纬度」，最多两位小数
            return f"{location.longitude:.2f},{location.latitude:.2f}"
        return location.code

    def _request(self, path: str, target: str, extra: str = "") -> dict[str, Any]:
        url = f"{_HOST}{path}?location={target}&key={self.api_key}{extra}"
        try:
            payload = get_json(url, timeout=10.0)
        except RequestError as error:
            raise ProviderError(str(error)) from error

        status = str(payload.get("code", ""))
        if status != "200":
            raise ProviderError(f"和风天气返回 {status or '未知错误'}")
        return payload

    def _apply_daily(self, snapshot: Snapshot, target: str) -> None:
        try:
            payload = self._request("/v7/weather/3d", target)
        except ProviderError:
            return
        daily = payload.get("daily") or []
        if not daily:
            return
        today = daily[0]
        snapshot.temp_high = self.to_float(today.get("tempMax"))
        snapshot.temp_low = self.to_float(today.get("tempMin"))

    def _fetch_hourly(
        self, target: str, table: codes.StatusTable, night: bool
    ) -> list[HourlyEntry]:
        try:
            payload = self._request("/v7/weather/24h", target)
        except ProviderError:
            return []

        entries: list[HourlyEntry] = []
        for item in (payload.get("hourly") or [])[:24]:
            canonical = table.canonical(item.get("icon"))
            precipitation = self.to_float(item.get("precip"))
            entries.append(
                HourlyEntry(
                    canonical=canonical,
                    temperature=self.to_float(item.get("temp")),
                    precipitating=(precipitation or 0.0) > 0
                    or codes.is_precipitation(canonical),
                    night=night,
                )
            )
        return entries

    def _fetch_minutely(self, location: Location) -> MinutelySeries | None:
        if location.longitude is None or location.latitude is None:
            return None
        target = f"{location.longitude:.2f},{location.latitude:.2f}"
        try:
            payload = self._request("/v7/minutely/5m", target)
        except ProviderError:
            return None

        values = [
            self.to_float(item.get("precip")) or 0.0
            for item in payload.get("minutely") or []
        ]
        if not values:
            return None
        return MinutelySeries(values=values, step=5)

    def _fetch_alerts(self, target: str) -> list[dict[str, Any]]:
        try:
            payload = self._request("/v7/warning/now", target)
        except ProviderError:
            return []

        results: list[dict[str, Any]] = []
        for item in payload.get("warning") or []:
            alert = build_alert(
                raw_title=str(item.get("title", "")),
                severity_value=item.get("severityColor") or item.get("severity"),
                text=str(item.get("text", "")),
                fallback_type=str(item.get("typeName", "")),
            )
            if alert:
                results.append(alert)
        return results
