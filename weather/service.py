"""抓取编排：把提供商返回的 :class:`Snapshot` 整理成 QML 直接可用的数据。"""

from typing import Any
from uuid import uuid4

from PySide6.QtCore import QThread, Signal

from . import codes
from .cities import CityRepository, coordinates_for_city
from .config import WeatherConfig
from .models import CITY, COORDINATES, Location, Snapshot
from .providers import (
    ProviderError,
    WeatherProvider,
    create,
    get_provider_class,
    provider_classes,
)

#: 分钟级降水最多提前多久提示
MINUTE_HORIZON = 120
#: 逐小时降水最多提前多久提示
HOUR_HORIZON = 12

_SEVERITY_RANK = {"red": 3, "orange": 2, "yellow": 1, "blue": 0}


def location_from_config(config: WeatherConfig) -> Location:
    if not (
        config.city_code or config.latitude is not None or config.longitude is not None
    ):
        cities = getattr(config, "cities", None) or []
        default_id = getattr(config, "default_city_id", "")
        city = next(
            (item for item in cities if str(item.get("id")) == str(default_id)), None
        ) or (cities[0] if cities else None)
        if city:
            return location_from_city(city, config.provider)
    code = config.city_code or ""
    name = ""
    short_name = ""
    mode = config.location_mode or CITY

    # 通过city_code查询城市名称
    if code and mode == CITY:
        repository = repository_for(config.provider)
        if repository is not None:
            row = repository.city_for_code(code)
            name, short_name = (
                str(row.get("name") or ""),
                str(row.get("short_name") or ""),
            )
    # 坐标模式下，优先使用保存的城市名称
    elif (
        mode == COORDINATES
        and config.latitude is not None
        and config.longitude is not None
    ):
        name = config.city_name or f"{config.latitude:.2f}, {config.longitude:.2f}"

    return Location(
        mode=mode,
        code=code,
        name=name,
        short_name=short_name,
        latitude=config.latitude,
        longitude=config.longitude,
    )


def normalize_city(city: dict[str, Any], provider_id: str = "") -> dict[str, Any]:
    """Return a stable, JSON-friendly city record used across providers."""
    record = dict(city or {})
    record.setdefault("id", str(uuid4()))
    record["id"] = str(record["id"])
    record["name"] = str(record.get("name") or "")
    # 短名称（小组件上显示的）缺了就退回长名称
    record["short_name"] = str(record.get("short_name") or "") or record["name"]
    record["mode"] = str(
        record.get("mode") or (COORDINATES if not record.get("code") else CITY)
    )
    if record.get("latitude") is not None:
        record["latitude"] = float(record["latitude"])
    if record.get("longitude") is not None:
        record["longitude"] = float(record["longitude"])
    if record.get("latitude") is None or record.get("longitude") is None:
        coordinates = coordinates_for_city(record["name"])
        if coordinates:
            record["latitude"], record["longitude"] = coordinates
    codes = dict(record.get("provider_codes") or {})
    if provider_id and record.get("code"):
        codes.setdefault(provider_id, str(record["code"]))
    record["provider_codes"] = {
        str(key): str(value) for key, value in codes.items() if value not in (None, "")
    }
    record.pop("code", None)
    return record


def refresh_city_names(
    cities: list[dict[str, Any]], provider_id: str = ""
) -> list[dict[str, Any]]:
    """用城市库里的名字覆盖城市记录的名称（升级老配置时用）。

    老配置里的名字是上一版的格式（「北京 海淀」「北京市」「海淀区」，或者在线搜索来的
    名字），城市库改成长 / 短两套名字之后得按编号重新对一次。对不上的城市原样保留。
    """
    repositories: dict[str, CityRepository] = {}
    for provider_class in provider_classes():
        if provider_class.database:
            repositories[provider_class.id] = CityRepository(provider_class.database)

    refreshed: list[dict[str, Any]] = []
    for city in cities:
        record = dict(city)
        codes = dict(record.get("provider_codes") or {})
        # 先按当前数据源的编号找，找不到再退到其它数据源
        for candidate in ([provider_id] if provider_id in codes else []) + list(codes):
            row = repositories.get(candidate) and repositories[candidate].city_for_code(
                codes[candidate]
            )
            if row:
                record["name"] = str(row["name"])
                record["short_name"] = str(row["short_name"])
                break
        refreshed.append(record)
    return refreshed


def migrate_cities(config: WeatherConfig) -> list[dict[str, Any]]:
    """Migrate the legacy single-location fields into the city list once.

    只在内容真的变了的时候写回 ``config``。CW2 的配置模型每写一个字段都会触发一次
    全量同步（``model_dump`` + ``configChanged``，主窗口里所有绑定在 ``Configs.data``
    上的地方都要重算），实测一次 ~270ms，而写一个没变化的字段和写新字段一样贵。
    这个函数在 ``settings()`` 这类只读路径上也会被调用，所以必须无写入。
    """
    cities = [normalize_city(item, config.provider) for item in (config.cities or [])]
    if not cities and (
        config.city_code or config.city_name or config.latitude is not None
    ):
        legacy_name, legacy_short = config.city_name, ""
        repository = repository_for(config.provider)
        row = (
            repository.city_for_code(config.city_code)
            if repository and config.city_code
            else {}
        )
        if row:
            legacy_name = str(row["name"])
            legacy_short = str(row["short_name"])
        cities = [
            normalize_city(
                {
                    "name": legacy_name,
                    "short_name": legacy_short,
                    "code": config.city_code,
                    "mode": config.location_mode,
                    "latitude": config.latitude,
                    "longitude": config.longitude,
                },
                config.provider,
            )
        ]
    if cities != (config.cities or []):
        config.cities = cities
    if cities and (
        not config.default_city_id
        or config.default_city_id not in {item["id"] for item in cities}
    ):
        config.default_city_id = cities[0]["id"]
    return cities


def city_for_provider(
    city: dict[str, Any], provider_id: str
) -> dict[str, Any] | None:
    """Resolve a normalized city to the current provider's location fields."""
    record = normalize_city(city)
    provider_class = get_provider_class(provider_id)
    has_coordinates = (
        record.get("latitude") is not None and record.get("longitude") is not None
    )
    if (
        record["mode"] == COORDINATES
        and provider_class
        and provider_class.supports_coordinates
        and has_coordinates
    ):
        return record
    code = (record.get("provider_codes") or {}).get(provider_id, "")
    if not code and provider_id:
        repository = repository_for(provider_id)
        if repository and record.get("name"):
            # 只按汉字名查：这条路径在主线程上跑（切换数据源 / 设为默认城市），
            # 不能触发拼音索引构建。
            matches = repository.search_by_name(record["name"], limit=1)
            if matches:
                code = matches[0]["code"]
                record["provider_codes"][provider_id] = str(code)
    if not code:
        if provider_class and provider_class.supports_coordinates and has_coordinates:
            record["mode"] = COORDINATES
            return record
        return None
    record["mode"] = CITY
    record["code"] = str(code)
    return record


def location_from_city(city: dict[str, Any], provider_id: str) -> Location:
    resolved = city_for_provider(city, provider_id)
    if not resolved:
        name = str((city or {}).get("name") or "")
        return Location(
            name=name, short_name=str((city or {}).get("short_name") or "") or name
        )
    return Location(
        mode=str(resolved.get("mode") or CITY),
        code=str(resolved.get("code") or ""),
        name=str(resolved.get("name") or ""),
        short_name=str(resolved.get("short_name") or ""),
        latitude=resolved.get("latitude"),
        longitude=resolved.get("longitude"),
    )


def sync_legacy_location(config: WeatherConfig, city: dict[str, Any] | None) -> None:
    """Keep legacy fields usable for older plugin/widget code and upgrades.

    同样只写真正变化的字段——见 :func:`migrate_cities` 里关于 CW2 配置同步代价的说明。
    """
    if not city:
        legacy = (CITY, "", "", None, None)
    else:
        resolved = city_for_provider(city, config.provider) or normalize_city(city)
        legacy = (
            str(resolved.get("mode") or CITY),
            str(resolved.get("code") or ""),
            str(resolved.get("name") or ""),
            resolved.get("latitude"),
            resolved.get("longitude"),
        )
    current = (
        config.location_mode,
        config.city_code,
        config.city_name,
        config.latitude,
        config.longitude,
    )
    if legacy == current:
        return
    for field, value in zip(
        ("location_mode", "city_code", "city_name", "latitude", "longitude"), legacy
    ):
        if getattr(config, field) != value:
            setattr(config, field, value)


def _format_temperature(value: float | None) -> str:
    if value is None:
        return ""
    return f"{round(value):d}"


def _description_of(snapshot: Snapshot) -> str:
    """优先用数据源的中文描述，否则回落到归一化编号的中文名。"""
    description = (snapshot.description or "").strip()
    if description and any("一" <= character <= "鿿" for character in description):
        return description
    return codes.canonical_description(snapshot.canonical) or description


def _precipitation_screen(snapshot: Snapshot) -> dict[str, Any] | None:
    """计算「降水预报」屏幕。"""
    raining_now = codes.is_precipitation(snapshot.canonical)

    change_in: int | None = None
    unit = ""
    upcoming = snapshot.canonical
    change_in_minutes: int | None = None  # 保存实际分钟数用于判断

    series = snapshot.minutely
    if series and series.values:
        raining_now = series.values[0] > 0
        for index, value in enumerate(series.values):
            if (value > 0) != raining_now:
                minutes = index * series.step
                if 0 < minutes <= MINUTE_HORIZON:
                    change_in_minutes = minutes
                    # 1小时内：精确到分钟
                    if minutes <= 60:
                        change_in, unit = minutes, "分钟后"
                    # 1-2小时：模糊描述
                    elif minutes <= 90:
                        change_in, unit = 1.5, "小时后"
                    else:
                        change_in, unit = 2, "小时后"
                break

    # 只在分钟级数据无结果时才使用小时级数据，且限制在2小时内
    if change_in is None and snapshot.hourly:
        for index, entry in enumerate(snapshot.hourly):
            state = entry.precipitating
            if state is None:
                state = codes.is_precipitation(entry.canonical)
            if state != raining_now:
                hours = max(index, 1)
                if hours <= 2:  # 只显示2小时内的降水预报
                    change_in_minutes = hours * 60
                    # 1小时用模糊描述
                    if hours == 1:
                        change_in, unit = 1, "小时后"
                    # 2小时用模糊描述
                    else:
                        change_in, unit = 2, "小时后"
                    upcoming = entry.canonical
                break

    if change_in is None:
        return None

    starting = not raining_now
    snowing = upcoming in (13, 14, 15, 16, 17, 26, 27, 28, 34)

    # 生成标题和描述
    if starting:
        # 即将开始降水
        title = "降水预报"
    else:
        # 降水即将结束
        if change_in_minutes and change_in_minutes <= 60:
            # 距离结束1小时内，显示"X分钟内雨渐停"
            title = "降水预报"
            change_in, unit = (
                change_in_minutes,
                "分钟内雨渐停" if not snowing else "分钟内雪渐停",
            )
        else:
            # 距离结束1小时外，不显示
            return None

    slug = "snow" if snowing else "rain"

    colour, alpha = codes.GLOW_RAIN
    screen = {
        "active": True,
        "title": title,
        "value": str(change_in),
        "unit": unit,
        "badgeIconPath": codes.icon_path(slug),
        "badgeIconScale": codes.icon_scale(slug),
        "glowColor": colour,
        "glowAlpha": alpha,
    }
    screen.update(codes.icon_visual(slug))
    return screen


def _alert_screen(snapshot: Snapshot) -> dict[str, Any] | None:
    if not snapshot.alerts:
        return None

    alert = max(
        snapshot.alerts,
        key=lambda item: _SEVERITY_RANK.get(item.get("severity", "yellow"), 1),
    )

    severity = alert.get("severity", "yellow")
    colour, alpha = codes.SEVERITY_GLOW.get(severity, codes.SEVERITY_GLOW["yellow"])
    slug = codes.SEVERITY_ICON.get(severity, "code-yellow")
    screen = {
        "active": True,
        "title": alert.get("title", ""),
        "severity": severity,
        "text": alert.get("text", ""),
        "metrics": alert.get("metrics") or [],
        "badgeIconPath": codes.icon_path(slug),
        "badgeIconScale": codes.icon_scale(slug),
        "glowColor": colour,
        "glowAlpha": alpha,
    }
    screen.update(codes.icon_visual(slug))
    return screen


def build_payload(
    snapshot: Snapshot | None,
    location: Location,
    provider: WeatherProvider | None,
    error: str = "",
) -> dict[str, Any]:
    """构造 QML 使用的数据字典。"""
    if snapshot is None:
        colour, alpha = codes.GLOW_UNAVAILABLE
        payload = {
            "available": False,
            "error": error or "天气数据不可用",
            "city": location.label,
            "description": "",
            "temperature": "",
            "glowColor": colour,
            "glowAlpha": alpha,
            "precipitation": {"active": False},
            "alert": {"active": False},
            "hourly": [],
        }
        payload.update(codes.icon_visual("code-yellow"))
        return payload

    colour, alpha = codes.glow_for(snapshot.canonical, snapshot.night)
    hourly: list[dict[str, object]] = []
    for entry in snapshot.hourly[:3]:
        item: dict[str, object] = {
            "temperature": _format_temperature(entry.temperature)
        }
        item.update(codes.icon_visual(codes.icon_slug(entry.canonical, entry.night)))
        hourly.append(item)

    payload = {
        "available": True,
        "error": "",
        "city": snapshot.city or location.label,
        "description": _description_of(snapshot),
        "temperature": _format_temperature(snapshot.temperature),
        "temperatureHigh": _format_temperature(snapshot.temp_high),
        "temperatureLow": _format_temperature(snapshot.temp_low),
        "night": snapshot.night,
        "glowColor": colour,
        "glowAlpha": alpha,
        "hourly": hourly,
        "precipitation": _precipitation_screen(snapshot) or {"active": False},
        "alert": _alert_screen(snapshot) or {"active": False},
    }
    payload.update(
        codes.icon_visual(codes.icon_slug(snapshot.canonical, snapshot.night))
    )
    return payload


class FetchWorker(QThread):
    """在后台线程抓取天气，完成后发出 :attr:`finishedWith`。"""

    finishedWith = Signal(dict)

    def __init__(self, provider: WeatherProvider, location: Location, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._location = location

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            snapshot = self._provider.fetch(self._location)
            payload = build_payload(snapshot, self._location, self._provider)
        except ProviderError as error:
            payload = build_payload(None, self._location, self._provider, str(error))
        except Exception as error:  # noqa: BLE001 - 提供商解析异常不应让线程崩溃
            payload = build_payload(
                None, self._location, self._provider, f"解析失败：{error}"
            )
        if not self.isInterruptionRequested():
            self.finishedWith.emit(payload)


def describe_providers() -> list[dict[str, Any]]:
    """给设置页用的提供商清单。"""
    return [
        {
            "id": provider_class.id,
            "name": provider_class.name,
            "requiresKey": provider_class.requires_key,
            "supportsCoordinates": provider_class.supports_coordinates,
            "supportsAlerts": provider_class.supports_alerts,
            "hasCityList": bool(provider_class.database),
            "onlineSearch": not provider_class.database,
        }
        for provider_class in provider_classes()
    ]


def repository_for(provider_id: str) -> CityRepository | None:
    provider_class = get_provider_class(provider_id)
    if provider_class is None or not provider_class.database:
        return None
    return CityRepository(provider_class.database)


def make_provider(config: WeatherConfig) -> WeatherProvider | None:
    return create(
        config.provider, api_key=(config.api_keys or {}).get(config.provider, "")
    )


def resolve_auto_location(
    provider_id: str, fix: dict[str, Any]
) -> dict[str, Any] | None:
    """把 IP 粗定位结果落到当前数据源能用的位置上。

    有内置城市库的数据源要按城市名查出自己的编号；
    只认坐标的数据源直接用经纬度。都不行就返回 ``None``。
    """
    latitude = fix.get("latitude")
    longitude = fix.get("longitude")
    if latitude is None or longitude is None:
        return None

    name = str(fix.get("name") or "").strip()
    repository = repository_for(provider_id)
    if repository is not None and name:
        matches = repository.search(name, limit=1)
        if matches:
            return {
                "mode": CITY,
                "code": matches[0]["code"],
                "name": matches[0]["name"],
                "short_name": matches[0]["short_name"],
                "latitude": latitude,
                "longitude": longitude,
            }

    provider_class = get_provider_class(provider_id)
    if provider_class is not None and provider_class.supports_coordinates:
        return {
            "mode": COORDINATES,
            "code": "",
            "name": name or f"{latitude:.2f}, {longitude:.2f}",
            "latitude": latitude,
            "longitude": longitude,
        }
    return None
