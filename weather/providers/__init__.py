"""天气提供商注册表。

导入各模块即可通过 ``@register`` 完成注册；顺序决定设置页中的展示顺序。
"""

from . import amap, open_meteo, qq, qweather, xiaomi  # noqa: F401  保证注册副作用
from .base import (
    ProviderError,
    WeatherProvider,
    create,
    get_provider_class,
    provider_classes,
    register,
)

__all__ = [
    "ProviderError",
    "WeatherProvider",
    "create",
    "get_provider_class",
    "provider_classes",
    "register",
]
