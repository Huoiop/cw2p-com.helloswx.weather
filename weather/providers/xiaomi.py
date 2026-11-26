"""小米天气。无需 API Key，支持城市代码与经纬度，提供预警和分钟级降水。"""

from typing import Any

from .. import codes
from ..alerts import build_alert
from ..http import RequestError, get_json
from ..models import COORDINATES, HourlyEntry, Location, MinutelySeries, Snapshot
from .base import ProviderError, WeatherProvider, register

_ENDPOINT = (
    "https://weatherapi.market.xiaomi.com/wtr-v3/weather/all"
    "?latitude={latitude}&longitude={longitude}&locationKey={location_key}"
    "&appKey=weather20151024&sign=zUFJoAR2ZVrDy1vF3D07&isGlobal=false&locale={locale}&days=7"
)

_STATUS_FILE = "xiaomi_weather_status.json"


@register
class XiaomiProvider(WeatherProvider):
    id = "xiaomi_weather"
    name = "小米天气"
    database = "xiaomi_weather.db"
    supports_coordinates = True
    requires_key = False
    supports_alerts = True

    def fetch(self, location: Location) -> Snapshot:
        latitude = location.latitude if location.latitude is not None else "0"
        longitude = location.longitude if location.longitude is not None else "0"
        location_key = ""
        if location.mode != COORDINATES and location.code:
            location_key = location.code
            if not location_key.startswith("weathercn:"):
                location_key = f"weathercn:{location_key}"

        url = _ENDPOINT.format(
            latitude=latitude,
            longitude=longitude,
            location_key=location_key,
            locale=self.locale,
        )
        try:
            payload = get_json(url, timeout=10.0)
        except RequestError as error:
            raise ProviderError(str(error)) from error

        table = codes.status_table(_STATUS_FILE)
        current = payload.get("current") or {}
        raw_code = current.get("weather")
        canonical = table.canonical(raw_code)
        night = self.is_night()

        snapshot = Snapshot(
            canonical=canonical,
            description=table.describe(raw_code),
            temperature=self.to_float((current.get("temperature") or {}).get("value")),
            city=location.label,
            night=night,
        )

        self._parse_daily(payload, snapshot)
        snapshot.hourly = self._parse_hourly(payload, table, night)
        snapshot.minutely = self._parse_minutely(payload)
        snapshot.alerts = self._parse_alerts(payload)
        return snapshot

    @staticmethod
    def _parse_daily(payload: dict[str, Any], snapshot: Snapshot) -> None:
        daily = (payload.get("forecastDaily") or {}).get("temperature") or {}
        values = daily.get("value") or []
        if not values:
            return
        today = values[0] or {}
        # 小米把当日最高温放在 from、最低温放在 to
        first = WeatherProvider.to_float(today.get("from"))
        second = WeatherProvider.to_float(today.get("to"))
        if first is None or second is None:
            snapshot.temp_high = first if first is not None else second
            snapshot.temp_low = second if first is not None else None
            return
        snapshot.temp_high = max(first, second)
        snapshot.temp_low = min(first, second)

    @staticmethod
    def _parse_hourly(
        payload: dict[str, Any], table: codes.StatusTable, night: bool
    ) -> list[HourlyEntry]:
        hourly = payload.get("forecastHourly") or {}
        temperatures = ((hourly.get("temperature") or {}).get("value")) or []
        weathers = ((hourly.get("weather") or {}).get("value")) or []

        entries: list[HourlyEntry] = []
        for index in range(min(len(temperatures), len(weathers), 24)):
            canonical = table.canonical(weathers[index])
            entries.append(
                HourlyEntry(
                    canonical=canonical,
                    temperature=WeatherProvider.to_float(temperatures[index]),
                    precipitating=codes.is_precipitation(canonical),
                    night=night,
                )
            )
        return entries

    @staticmethod
    def _parse_minutely(payload: dict[str, Any]) -> MinutelySeries | None:
        minutely = payload.get("minutely") or {}
        values = minutely.get("precipitation")
        if not isinstance(values, list) or not values:
            return None
        numbers = [WeatherProvider.to_float(item) or 0.0 for item in values]
        return MinutelySeries(values=numbers, step=1)

    @staticmethod
    def _parse_alerts(payload: dict[str, Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in payload.get("alerts") or []:
            if not isinstance(item, dict):
                continue
            alert = build_alert(
                raw_title=str(item.get("title", "")),
                severity_value=item.get("level"),
                text=str(item.get("detail", "")),
                fallback_type=str(item.get("type", "")),
            )
            if alert:
                results.append(alert)
        return results
