"""插件内部使用的天气数据结构。"""

from dataclasses import dataclass, field
from typing import Any

CITY = "city"
COORDINATES = "coordinates"


@dataclass
class Location:
    """用户选定的位置。"""

    mode: str = CITY
    code: str = ""
    name: str = ""  # 长名称，北京海淀
    short_name: str = ""  # 短名称，海淀区——小组件上显示的就是它
    latitude: float | None = None
    longitude: float | None = None

    @property
    def configured(self) -> bool:
        if self.mode == COORDINATES:
            return self.latitude is not None and self.longitude is not None
        return bool(self.code) or (
            self.latitude is not None and self.longitude is not None
        )

    @property
    def label(self) -> str:
        """小组件上显示的城市名：有短名称就用短名称。"""
        if self.short_name or self.name:
            return self.short_name or self.name
        if self.mode == COORDINATES and self.configured:
            return f"{self.latitude:.2f}, {self.longitude:.2f}"
        return ""


@dataclass
class HourlyEntry:
    """逐小时预报的一项。"""

    canonical: int
    temperature: float | None = None
    label: str = ""
    precipitating: bool | None = None
    night: bool = False


@dataclass
class MinutelySeries:
    """分钟级降水序列。``step`` 为每项代表的分钟数。"""

    values: list[float]
    step: int = 1


@dataclass
class Snapshot:
    """一次抓取得到的全部天气信息。"""

    canonical: int = 99
    description: str = ""
    temperature: float | None = None
    temp_high: float | None = None
    temp_low: float | None = None
    city: str = ""
    night: bool = False
    hourly: list[HourlyEntry] = field(default_factory=list)
    minutely: MinutelySeries | None = None
    alerts: list[dict[str, Any]] = field(default_factory=list)
