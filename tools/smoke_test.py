"""开发期冒烟测试：不依赖 Class Widgets 主体，直接跑通后端。

.venv/bin/python tools/smoke_test.py
"""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 主体会在运行时注入 ClassWidgets.SDK，这里用 pydantic 顶替以便离线测试
if "ClassWidgets.SDK" not in sys.modules:
    from pydantic import BaseModel

    stub = types.ModuleType("ClassWidgets.SDK")
    stub.ConfigBaseModel = BaseModel
    sys.modules["ClassWidgets.SDK"] = stub

from weather import codes
from weather.alerts import build_alert
from weather.cities import CityRepository
from weather.config import WeatherConfig
from weather.models import COORDINATES, Location
from weather.providers import ProviderError, create, provider_classes
from weather.service import build_payload

PASS, FAIL = "  ok ", "FAIL "
failures = 0


def check(label, condition, detail=""):
    global failures
    if condition:
        print(f"{PASS}{label}{(' — ' + detail) if detail else ''}")
    else:
        failures += 1
        print(f"{FAIL}{label}{(' — ' + detail) if detail else ''}")


def section(name):
    print(f"\n=== {name} ===")


section("天气代码与图标")
xiaomi = codes.status_table("xiaomi_weather_status.json")
check("小米 0 -> 晴", xiaomi.describe(0) == "晴", xiaomi.describe(0))
check("小米 0 归一化", xiaomi.canonical(0) == 0)
qweather = codes.status_table("qweather_status.json")
check(
    "和风 305 归一化为小雨",
    qweather.canonical(305) == 7,
    f"canonical={qweather.canonical(305)}",
)
meteo = codes.status_table("open_meteo_status.json")
check(
    "Open-Meteo 95 归一化为雷阵雨",
    meteo.canonical(95) == 4,
    f"canonical={meteo.canonical(95)}",
)
amap = codes.status_table("amap_weather_status.json")
check(
    "高德「中雨」反查",
    amap.canonical_from_text("中雨") == 8,
    f"canonical={amap.canonical_from_text('中雨')}",
)
check(
    "高德「雷阵雨」优先于「阵雨」",
    amap.canonical_from_text("雷阵雨") == 4,
    f"canonical={amap.canonical_from_text('雷阵雨')}",
)

check("晴天白天图标", codes.icon_slug(0, False) == "clear-day")
check("晴天夜间图标", codes.icon_slug(0, True) == "clear-night")
missing = [
    canonical
    for canonical in list(range(36)) + [53, 99]
    for night in (False, True)
    if not codes.icon_file(codes.icon_slug(canonical, night)).exists()
]
check("所有归一化编号都有图标文件", not missing, f"缺失 {missing}")
check(
    "图标路径是 file:// URI（qt-lottie 只认 isLocalFile）",
    codes.icon_path("clear-day").startswith("file:///"),
    codes.icon_path("clear-day"),
)
for severity, slug in codes.SEVERITY_ICON.items():
    check(f"预警图标 {severity}", codes.icon_file(slug).name == f"{slug}.json")
check("降水判定", codes.is_precipitation(8) and not codes.is_precipitation(0))


section("预警解析")
cases = [
    (
        "市气象台发布高温红色预警[Ⅰ级/特别重大]",
        "red",
        "预计未来1小时内，最高气温将升至40℃以上。",
        "高温红色预警",
        ["1", "≥40"],
    ),
    (
        "海南省海口市气象台发布暴雨橙色预警[Ⅲ级/较重]",
        "orange",
        "预计未来3小时内降雨量将达50毫米以上。",
        "暴雨橙色预警",
        ["3", "≥50"],
    ),
    (
        "台风蓝色预警",
        "blue",
        "预计未来24小时内，平均风力可达7级以上。",
        "台风蓝色预警",
        ["24", "≥7"],
    ),
    (
        "暴雪黄色预警信号",
        "yellow",
        "预计未来12小时内降雪量将达6毫米。",
        "暴雪黄色预警",
        ["12", "6"],
    ),
]
for raw_title, severity_value, text, expected_title, expected_values in cases:
    alert = build_alert(raw_title, severity_value, text)
    values = [item["value"] for item in alert["metrics"]]
    check(f"标题 {expected_title}", alert["title"] == expected_title, alert["title"])
    check(f"指标 {expected_values}", values == expected_values, str(alert["metrics"]))

no_metrics = build_alert("高温预警", "红色", "天气炎热，请注意防暑。")
check("无法解析指标时留空", no_metrics["metrics"] == [], str(no_metrics["metrics"]))


section("城市库")
for database, sample in (("xiaomi_weather.db", "海口"), ("amap_weather.db", "海口")):
    repository = CityRepository(database)
    provinces = repository.provinces()
    check(f"{database} 省份数量", len(provinces) == 34, str(len(provinces)))
    first = repository.cities(0)
    check(
        f"{database} 首个省份有城市",
        len(first) > 0,
        f"{len(first)} 个，例如 {first[:1]}",
    )
    found = repository.search(sample)
    check(f"{database} 搜索「{sample}」", len(found) > 0, str(found[:2]))
    if found:
        name = repository.name_for_code(found[0]["code"])
        check(f"{database} 代码反查", bool(name), name)


section("提供商注册")
ids = [cls.id for cls in provider_classes()]
check("五个数据源", len(ids) == 5, str(ids))
for provider_class in provider_classes():
    if provider_class.database:
        check(
            f"{provider_class.id} 城市库存在",
            (ROOT / "data" / provider_class.database).exists(),
        )


section("在线抓取（需要网络）")


def try_fetch(provider_id, location, label):
    provider = create(provider_id)
    if provider is None:
        check(label, False, "未注册")
        return None
    try:
        snapshot = provider.fetch(location)
    except ProviderError as error:
        print(f"{FAIL}{label} — {error}")
        return None
    payload = build_payload(snapshot, location, provider)
    check(
        label,
        payload["available"] and payload["temperature"] != "",
        f"{payload.get('description', '')} {payload.get('temperature')}° "
        f"icon={payload['iconPath'].rsplit('/', 1)[-1]} "
        f"hourly={len(payload['hourly'])} "
        f"alert={payload['alert'].get('active')} precip={payload['precipitation'].get('active')}",
    )
    return payload


try_fetch(
    "open_meteo",
    Location(mode=COORDINATES, latitude=20.03, longitude=110.32, name="海口"),
    "Open-Meteo 海口",
)
try_fetch(
    "xiaomi_weather",
    Location(code="101310101", name="海口", latitude=20.03, longitude=110.32),
    "小米天气 海口",
)

meteo_provider = create("open_meteo")
results = meteo_provider.search_cities("柏林")
check("Open-Meteo 城市搜索「柏林」", len(results) > 0, str(results[:2]))


section("不可用状态")
fallback = build_payload(None, Location(), None, "未选择城市")
check("不可用有黄色图标", fallback["iconPath"].endswith("code-yellow.json"))
check("不可用带错误文案", fallback["error"] == "未选择城市")
check(
    "不可用时无预警/降水",
    not fallback["alert"]["active"] and not fallback["precipitation"]["active"],
)


section("配置默认值")
config = WeatherConfig()
check("默认数据源", config.provider == "xiaomi_weather")
check("默认刷新间隔", config.refresh_minutes == 5)
check("api_keys 独立", WeatherConfig().api_keys is not config.api_keys)

print(f"\n{'全部通过' if not failures else str(failures) + ' 项失败'}")
sys.exit(1 if failures else 0)
