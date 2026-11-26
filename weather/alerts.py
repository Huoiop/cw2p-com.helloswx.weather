"""天气预警归一化。

把各家 API 的预警数据整理成同一形状，并尽量从预警正文中解析出设计稿
（node 427:240）里的两组指标，例如「24 小时内 / 7 级」「3 小时内 / ≥50 毫米」。
"""

import re
from typing import Any

_SEVERITY_ALIASES: dict[str, str] = {
    "1": "blue",
    "2": "yellow",
    "3": "orange",
    "4": "red",
    "minor": "blue",
    "moderate": "yellow",
    "severe": "orange",
    "extreme": "red",
    "blue": "blue",
    "yellow": "yellow",
    "orange": "orange",
    "red": "red",
    "蓝": "blue",
    "黄": "yellow",
    "橙": "orange",
    "红": "red",
    "蓝色": "blue",
    "黄色": "yellow",
    "橙色": "orange",
    "红色": "red",
    "白": "blue",
    "白色": "blue",
}

_SEVERITY_LABEL = {"blue": "蓝色", "yellow": "黄色", "orange": "橙色", "red": "红色"}

_COLOUR_PATTERN = re.compile(r"(蓝|黄|橙|红)色预警")
_HOURS_PATTERN = re.compile(r"(\d+)\s*小时(?:内|之内)")
#: 预警标题里出现在「类别」之前的常见前缀
_TITLE_PREFIXES = ("发布", "升级为", "变更为", "继续", "更新", "气象台", "气象局")

# (正则, 单位)；正则第 1 组是数值，第 2 组是「以上 / 以下」
_VALUE_PATTERNS = [
    (re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:℃|摄氏度)(以上|以下)?"), "℃"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*毫米(以上|以下)?"), "毫米"),
    (re.compile(r"(\d+)\s*级(以上|以下)?"), "级"),
    (re.compile(r"(\d+(?:\.\d+)?)\s*厘米(以上|以下)?"), "厘米"),
]


def normalise_severity(value: Any) -> str:
    """把等级/颜色字段折算成 blue / yellow / orange / red。"""
    if value is None:
        return "yellow"
    text = str(value).strip().lower()
    if text in _SEVERITY_ALIASES:
        return _SEVERITY_ALIASES[text]
    for alias, severity in _SEVERITY_ALIASES.items():
        if alias and alias in text:
            return severity
    return "yellow"


def compose_title(raw_title: str, severity: str, fallback_type: str = "") -> str:
    """整理成设计稿中的「暴雨橙色预警」形式。

    输入通常形如「海口市气象台发布暴雨橙色预警[Ⅲ级/较重]」，
    需要取出紧挨颜色前面的预警类别。
    """
    if raw_title:
        match = _COLOUR_PATTERN.search(raw_title)
        if match:
            head = raw_title[: match.start()]
            for prefix in _TITLE_PREFIXES:
                position = head.rfind(prefix)
                if position != -1:
                    head = head[position + len(prefix) :]
            kind = re.sub(r"[^一-龥]", "", head)[-6:]
            return f"{kind}{match.group(1)}色预警"

    kind = fallback_type.strip()
    if not kind and raw_title:
        # 去掉「XX气象台发布」和等级后缀，尽量只留预警类别
        cleaned = re.sub(r"^.*?发布", "", raw_title)
        cleaned = re.sub(r"[\[【(（].*?[\]】)）]", "", cleaned)
        kind = cleaned.replace("预警信号", "").replace("预警", "").strip()

    label = _SEVERITY_LABEL.get(severity, "黄色")
    if kind:
        return f"{kind}{label}预警"
    return f"{label}预警"


def extract_metrics(text: str) -> list[dict[str, str]]:
    """解析预警正文中的「N 小时内 + 数值 单位」，解析不出则返回空列表。"""
    if not text:
        return []

    hours = _HOURS_PATTERN.search(text)
    if not hours:
        return []

    for pattern, unit in _VALUE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        number = match.group(1)
        number = number.removesuffix(".0")
        qualifier = match.group(2)
        prefix = "≥" if qualifier == "以上" else "≤" if qualifier == "以下" else ""
        return [
            {"value": hours.group(1), "unit": "小时内"},
            {"value": f"{prefix}{number}", "unit": unit},
        ]
    return []


def build_alert(
    raw_title: str,
    severity_value: Any,
    text: str = "",
    fallback_type: str = "",
) -> dict[str, Any] | None:
    """构造统一的预警字典；标题与正文都为空时返回 ``None``。"""
    if not raw_title and not text:
        return None

    severity = normalise_severity(
        severity_value if severity_value is not None else raw_title
    )
    return {
        "title": compose_title(raw_title, severity, fallback_type),
        "severity": severity,
        "text": text or "",
        "metrics": extract_metrics(text),
    }
