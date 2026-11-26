"""按 IP 粗定位，供首次安装时自动选城市。

流程：
    1. ip-api.com 拿到经纬度；
    2. 小米的城市地理接口把经纬度换成城市名；
    3. 由调用方在对应数据源的城市库里查出该数据源自己的城市编号。

三步里任何一步失败都只是拿不到位置，不影响手动选择。
"""

from typing import Any

from .http import RequestError, fetch_json, get_json

_IP_ENDPOINT = "http://ip-api.com/json/?fields=status,lat,lon,city"
_CITY_ENDPOINT = (
    "https://weatherapi.market.xiaomi.com/wtr-v3/location/city/geo"
    "?latitude={latitude}&longitude={longitude}"
    "&appKey=weather20151024&sign=zUFJoAR2ZVrDy1vF3D07&locale=zh_cn"
)


def coordinates_by_ip(timeout: float = 8.0) -> tuple[float, float] | None:
    """按出口 IP 取经纬度。"""
    try:
        payload = get_json(_IP_ENDPOINT, timeout=timeout)
    except RequestError:
        return None

    if str(payload.get("status")) != "success":
        return None
    try:
        return float(payload["lat"]), float(payload["lon"])
    except (KeyError, TypeError, ValueError):
        return None


def city_name_at(latitude: float, longitude: float, timeout: float = 8.0) -> str:
    """把经纬度换成中文城市名；失败返回空串。"""
    url = _CITY_ENDPOINT.format(latitude=latitude, longitude=longitude)
    try:
        payload: Any = fetch_json(url, timeout=timeout)
    except RequestError:
        return ""

    entries: list[dict[str, Any]] = payload if isinstance(payload, list) else [payload]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if name:
            return name
    return ""


def locate(timeout: float = 8.0) -> dict[str, Any] | None:
    """返回 ``{"latitude", "longitude", "name"}``；定位不到时返回 ``None``。"""
    position = coordinates_by_ip(timeout=timeout)
    if position is None:
        return None

    latitude, longitude = position
    return {
        "latitude": latitude,
        "longitude": longitude,
        "name": city_name_at(latitude, longitude, timeout=timeout),
    }
