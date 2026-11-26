"""高德天气。需要 API Key，按城市 adcode 取数。"""

from typing import Any

from .. import codes
from ..http import RequestError, get_json
from ..models import Location, Snapshot
from .base import ProviderError, WeatherProvider, register

_ENDPOINT = "https://restapi.amap.com/v3/weather/weatherInfo?key={key}&city={city}&extensions={extensions}"
_STATUS_FILE = "amap_weather_status.json"


@register
class AmapProvider(WeatherProvider):
    id = "amap_weather"
    name = "高德天气"
    database = "amap_weather.db"
    supports_coordinates = False
    requires_key = True
    supports_alerts = False

    def fetch(self, location: Location) -> Snapshot:
        if not self.api_key:
            raise ProviderError("缺少 API Key")
        if not location.code:
            raise ProviderError("未设置城市")

        live = self._request(location.code, "base")
        lives = live.get("lives") or []
        if not lives:
            raise ProviderError("未返回实况数据")
        current = lives[0]

        table = codes.status_table(_STATUS_FILE)
        description = str(current.get("weather") or "")
        canonical = table.canonical_from_text(description)

        snapshot = Snapshot(
            canonical=canonical,
            description=description,
            temperature=self.to_float(current.get("temperature")),
            city=location.label or str(current.get("city") or ""),
            night=self.is_night(),
        )
        self._apply_forecast(snapshot, location.code, table)
        return snapshot

    def _request(self, city: str, extensions: str) -> dict[str, Any]:
        url = _ENDPOINT.format(key=self.api_key, city=city, extensions=extensions)
        try:
            payload = get_json(url, timeout=10.0)
        except RequestError as error:
            raise ProviderError(str(error)) from error

        if str(payload.get("status")) != "1":
            raise ProviderError(str(payload.get("info") or "高德天气请求失败"))
        return payload

    def _apply_forecast(
        self, snapshot: Snapshot, city: str, table: codes.StatusTable
    ) -> None:
        """高德只有逐日预报，这里仅取当天最高 / 最低温。

        没有逐小时数据，因此该源不提供降水预报屏幕。
        """
        try:
            payload = self._request(city, "all")
        except ProviderError:
            return

        forecasts = payload.get("forecasts") or []
        if not forecasts:
            return
        casts: list[dict[str, Any]] = forecasts[0].get("casts") or []
        if not casts:
            return

        today = casts[0]
        snapshot.temp_high = self.to_float(today.get("daytemp"))
        snapshot.temp_low = self.to_float(today.get("nighttemp"))
