"""天气提供商基类与注册表。"""

from abc import ABC, abstractmethod
from datetime import datetime

from ..models import Location, Snapshot


class ProviderError(Exception):
    """提供商无法给出可用数据。"""


class WeatherProvider(ABC):
    """一个天气数据源。"""

    id: str = ""
    name: str = ""
    #: 城市列表所用的数据库文件；为空表示该源不使用内置城市库
    database: str = ""
    #: 是否接受直接输入经纬度
    supports_coordinates: bool = False
    #: 是否必须填写 API Key
    requires_key: bool = False
    #: 是否支持天气预警
    supports_alerts: bool = False

    def __init__(self, api_key: str = "", locale: str = "zh_cn"):
        self.api_key = api_key or ""
        self.locale = locale

    @abstractmethod
    def fetch(self, location: Location) -> Snapshot:
        """抓取并解析天气数据，失败时抛出 :class:`ProviderError`。"""

    def search_cities(self, term: str) -> list[dict[str, str]]:
        """在线城市搜索；默认不支持，由内置城市库负责。"""
        return []

    @staticmethod
    def is_night(reference: datetime | None = None) -> bool:
        hour = (reference or datetime.now()).hour
        return hour < 6 or hour >= 18

    @staticmethod
    def to_float(value: object) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None


_REGISTRY: dict[str, type[WeatherProvider]] = {}


def register(provider_class: type[WeatherProvider]) -> type[WeatherProvider]:
    _REGISTRY[provider_class.id] = provider_class
    return provider_class


def provider_classes() -> list[type[WeatherProvider]]:
    return list(_REGISTRY.values())


def get_provider_class(provider_id: str) -> type[WeatherProvider] | None:
    return _REGISTRY.get(provider_id)


def create(
    provider_id: str, api_key: str = "", locale: str = "zh_cn"
) -> WeatherProvider | None:
    provider_class = _REGISTRY.get(provider_id)
    if provider_class is None:
        return None
    return provider_class(api_key=api_key, locale=locale)
