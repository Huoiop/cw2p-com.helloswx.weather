"""天气代码归一化与 Meteocons 图标映射。

各家 API 的天气代码互不相同，这里统一折算成 ``data/xiaomi_weather_status.json``
使用的编号（0 晴 / 1 多云 / 2 阴 / …），再由归一化编号映射到 Meteocons 图标。
"""

import json
from functools import lru_cache
from pathlib import Path

from . import DATA_DIR, LOTTIE_DIR

UNKNOWN = 99

# 归一化编号 -> (白天图标, 夜间图标)
_METEOCONS: dict[int, tuple[str, str]] = {
    0: ("clear-day", "clear-night"),
    1: ("partly-cloudy-day", "partly-cloudy-night"),
    2: ("overcast-day", "overcast-night"),
    3: ("partly-cloudy-day-rain", "partly-cloudy-night-rain"),
    4: ("thunderstorms-day-rain", "thunderstorms-night-rain"),
    5: ("hail", "hail"),
    6: ("sleet", "sleet"),
    7: ("drizzle", "drizzle"),
    8: ("rain", "rain"),
    9: ("rain", "rain"),
    10: ("extreme-rain", "extreme-rain"),
    11: ("extreme-rain", "extreme-rain"),
    12: ("extreme-rain", "extreme-rain"),
    13: ("partly-cloudy-day-snow", "partly-cloudy-night-snow"),
    14: ("snow", "snow"),
    15: ("snow", "snow"),
    16: ("snow", "snow"),
    17: ("extreme-snow", "extreme-snow"),
    18: ("fog-day", "fog-night"),
    19: ("sleet", "sleet"),
    20: ("dust-day", "dust-night"),
    21: ("rain", "rain"),
    22: ("rain", "rain"),
    23: ("extreme-rain", "extreme-rain"),
    24: ("extreme-rain", "extreme-rain"),
    25: ("extreme-rain", "extreme-rain"),
    26: ("snow", "snow"),
    27: ("snow", "snow"),
    28: ("extreme-snow", "extreme-snow"),
    29: ("dust", "dust"),
    30: ("dust-day", "dust-night"),
    31: ("dust", "dust"),
    32: ("wind", "wind"),
    33: ("tornado", "tornado"),
    34: ("snow", "snow"),
    35: ("mist", "mist"),
    53: ("haze", "haze"),
    UNKNOWN: ("not-available", "not-available"),
}

# 含降水的归一化编号
_PRECIPITATION = frozenset(
    {
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        19,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        34,
    }
)

# 光晕颜色取自设计稿（node 25:579 / 427:240）
GLOW_DAY = ("#F8AF18", 0.15)
GLOW_NIGHT = ("#72B9D5", 0.30)
GLOW_RAIN = ("#0A5AD4", 0.15)
GLOW_UNAVAILABLE = ("#FDE047", 0.15)

SEVERITY_GLOW: dict[str, tuple[str, float]] = {
    "blue": ("#0A5AD4", 0.15),
    "yellow": ("#FDE047", 0.15),
    "orange": ("#FB923C", 0.15),
    "red": ("#EF4444", 0.15),
}

SEVERITY_ICON: dict[str, str] = {
    "blue": "code-blue",
    "yellow": "code-yellow",
    "orange": "code-orange",
    "red": "code-red",
}


class StatusTable:
    """单个提供商的天气代码对照表。"""

    def __init__(self, filename: str):
        self._to_canonical: dict[str, int] = {}
        self._describe: dict[str, str] = {}
        self._text_to_canonical: dict[str, int] = {}

        path = DATA_DIR / filename
        try:
            entries: list[dict] = json.loads(path.read_text(encoding="utf-8"))[
                "weatherinfo"
            ]
        except (OSError, ValueError, KeyError):
            entries = []

        for entry in entries:
            raw = str(entry.get("code"))
            text = str(entry.get("wea", ""))
            canonical = int(entry.get("original_code", entry.get("code", UNKNOWN)))
            self._to_canonical[raw] = canonical
            self._describe[raw] = text
            # 同一描述可能对应多个原始代码，保留第一个即可
            self._text_to_canonical.setdefault(text, canonical)

    def canonical(self, code: object) -> int:
        return self._to_canonical.get(str(code), UNKNOWN)

    def describe(self, code: object) -> str:
        return self._describe.get(str(code), "")

    def canonical_from_text(self, text: str) -> int:
        """按天气描述反查，用于只返回中文描述的高德 / 腾讯。"""
        if not text:
            return UNKNOWN
        if text in self._text_to_canonical:
            return self._text_to_canonical[text]
        # 退化为包含匹配，优先匹配更长的描述（"雷阵雨" 先于 "阵雨"）
        best: str | None = None
        for candidate in self._text_to_canonical:
            if not candidate or candidate not in text:
                continue
            if best is None or len(candidate) > len(best):
                best = candidate
        return self._text_to_canonical[best] if best else UNKNOWN


@lru_cache(maxsize=8)
def status_table(filename: str) -> StatusTable:
    return StatusTable(filename)


def icon_slug(canonical: int, night: bool = False) -> str:
    day_slug, night_slug = _METEOCONS.get(canonical, _METEOCONS[UNKNOWN])
    return night_slug if night else day_slug


def canonical_description(canonical: int) -> str:
    """归一化编号对应的中文天气描述。

    Open-Meteo 等数据源只给英文描述，统一回落到这里保证界面语言一致。
    """
    return status_table("xiaomi_weather_status.json").describe(canonical)


def icon_file(slug: str) -> Path:
    """图标文件路径；缺失时回退到 ``not-available``。"""
    path = LOTTIE_DIR / f"{slug}.json"
    if not path.exists():
        path = LOTTIE_DIR / "not-available.json"
    return path


def icon_path(slug: str) -> str:
    """给 QML 用的 ``file://`` URI。

    qt-lottie 只在 ``QUrl.isLocalFile()`` 为真时加载文件，
    而 QML 把裸路径字符串转成 QUrl 时不会带 file 协议，因此这里必须返回 URI。
    """
    return icon_file(slug).resolve().as_uri()


@lru_cache(maxsize=1)
def _icon_metrics() -> dict[str, float]:
    """各图标内容在画布中的占比，由 ``tools/build_icon_metrics.py`` 生成。"""
    try:
        return json.loads((LOTTIE_DIR / "metrics.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def icon_scale(slug: str) -> float:
    """渲染框相对设计尺寸的放大倍数。

    Meteocons 在 128×128 画布里留白很多且各图标不一致，按设计稿的尺寸
    直接渲染会偏小。把渲染框放大 1/占比，图标内容才等于设计稿尺寸。
    """
    ratio = _icon_metrics().get(slug, 0.0)
    if ratio <= 0:
        return 1.0
    return round(1.0 / ratio, 4)


def icon_visual(slug: str) -> dict[str, object]:
    """QML 需要的一组图标信息：路径 + 放大倍数。"""
    return {"iconPath": icon_path(slug), "iconScale": icon_scale(slug)}


def is_precipitation(canonical: int) -> bool:
    return canonical in _PRECIPITATION


def glow_for(canonical: int, night: bool = False) -> tuple[str, float]:
    """主体屏幕的顶部光晕颜色。"""
    if canonical == UNKNOWN:
        return GLOW_UNAVAILABLE
    if is_precipitation(canonical):
        return GLOW_RAIN
    return GLOW_NIGHT if night else GLOW_DAY
