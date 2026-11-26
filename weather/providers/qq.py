"""腾讯位置服务天气。需要 API Key，按城市 adcode 取数。"""

from typing import Any

from .. import codes
from ..http import RequestError, get_json
from ..models import Location, Snapshot
from .base import ProviderError, WeatherProvider, register

_ENDPOINT = (
    "https://apis.map.qq.com/ws/weather/v1/?key={key}&adcode={adcode}&type={type}"
)
_STATUS_FILE = "qq_weather_status.json"


@register
class QQProvider(WeatherProvider):
    id = "qq_weather"
    name = "腾讯天气"
    database = "amap_weather.db"
    supports_coordinates = False
    requires_key = True
    supports_alerts = False

    def fetch(self, location: Location) -> Snapshot:
        if not self.api_key:
            raise ProviderError("缺少 API Key")
        if not location.code:
            raise ProviderError("未设置城市")

        payload = self._request(location.code, "now")
        realtime: list[dict[str, Any]] = (payload.get("result") or {}).get(
            "realtime"
        ) or []
        if not realtime:
            raise ProviderError("未返回实况数据")

        infos = realtime[0].get("infos") or {}
        table = codes.status_table(_STATUS_FILE)
        description = str(infos.get("weather") or "")
        canonical = table.canonical_from_text(description)

        snapshot = Snapshot(
            canonical=canonical,
            description=description,
            temperature=self.to_float(infos.get("degree")),
            city=location.label,
            night=self.is_night(),
        )
        self._apply_forecast(snapshot, location.code)
        return snapshot

    def _request(self, adcode: str, kind: str) -> dict[str, Any]:
        url = _ENDPOINT.format(key=self.api_key, adcode=adcode, type=kind)
        try:
            payload = get_json(url, timeout=10.0)
        except RequestError as error:
            raise ProviderError(str(error)) from error

        if int(payload.get("status", -1)) != 0:
            raise ProviderError(str(payload.get("message") or "腾讯天气请求失败"))
        return payload

    def _apply_forecast(self, snapshot: Snapshot, adcode: str) -> None:
        """腾讯只有逐日预报，这里仅取当天最高 / 最低温。

        没有逐小时数据，因此该源不提供降水预报屏幕。
        """
        try:
            payload = self._request(adcode, "future")
        except ProviderError:
            return

        forecast: list[dict[str, Any]] = (payload.get("result") or {}).get(
            "forecast"
        ) or []
        if not forecast:
            return
        infos = forecast[0].get("infos") or []
        if not infos:
            return

        today = infos[0]
        snapshot.temp_high = self.to_float(today.get("max_degree"))
        snapshot.temp_low = self.to_float(today.get("min_degree"))
