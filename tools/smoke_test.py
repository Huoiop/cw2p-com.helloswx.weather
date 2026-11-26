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
from weather.models import COORDINATES, DailyEntry, Location, Snapshot
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


section("到点提醒文案")
for canonical, label, expected in (
    (0, "晴", "阳光明媚，宜出门走走。"),
    (1, "多云", "云影徘徊，出门无妨。"),
    (2, "阴", "云层低垂，心情勿沉。"),
    (3, "阵雨", "阵雨忽至，伞在身边。"),
    (4, "雷阵雨", "雷声隐隐，避雨檐下。"),
    (5, "冰雹", "冰雹骤落，暂避室内。"),
    (6, "雨夹雪", "雨雪相杂，备好伞衣。"),
    (7, "小雨", "细雨如丝，出门带伞。"),
    (8, "中雨", "雨势渐密，行路慢些。"),
    (9, "大雨", "大雨滂沱，暂缓远行。"),
    (14, "小雪", "雪花纷飞，添衣保暖。"),
    (17, "暴雪", "风雪交加，不宜出门。"),
    (18, "雾", "雾气氤氲，缓步慢行。"),
    (19, "冻雨", "冻雨路滑，缓步慢行。"),
    (20, "沙尘暴", "沙尘漫卷，护好口鼻。"),
    (32, "大风", "风势正紧，帽要戴稳。"),
    (53, "霾", "霾色沉沉，口罩护身。"),
):
    sentence = codes.outdoor_sentence(canonical)
    check(f"室外文案 {label}", sentence == expected, sentence)
check("未知编号不提醒", codes.outdoor_sentence(codes.UNKNOWN) == "")

notice_snapshot = Snapshot(
    canonical=3,
    description="阵雨",
    temperature=18,
    temp_high=25,
    temp_low=18,
    city="海口",
)
notice_snapshot.tomorrow = DailyEntry(
    canonical=7, description="小雨", temp_high=20, temp_low=16
)
notice_payload = build_payload(notice_snapshot, Location(name="海口"), None)
check(
    "室外提醒文案进 payload",
    notice_payload["outdoorText"] == "阵雨忽至，伞在身边。",
    notice_payload["outdoorText"],
)
tomorrow = notice_payload["tomorrow"]
check("明日提醒可用", tomorrow["available"] is True)
check(
    "明日提醒是单行文案",
    tomorrow["text"] == "明日细雨将至，出门莫忘带伞。",
    tomorrow["text"],
)
check("明日提醒不再输出温度", "temperatureHigh" not in tomorrow)
check(
    "明日提醒带图标",
    tomorrow["iconPath"].endswith("drizzle.json"),
    tomorrow["iconPath"].rsplit("/", 1)[-1],
)
no_tomorrow = build_payload(Snapshot(), Location(), None)
check("没有明日数据时不提醒", no_tomorrow["tomorrow"]["available"] is False)


def tomorrow_text(canonical: int, tomorrow_high: float, today_high: float = 25) -> str:
    """构造一次明日提醒，返回单行文案。"""
    snapshot = Snapshot(canonical=0, temp_high=today_high)
    snapshot.tomorrow = DailyEntry(canonical=canonical, temp_high=tomorrow_high)
    payload = build_payload(snapshot, Location(name="测试"), None)
    return str(payload["tomorrow"].get("text", ""))


for canonical, tomorrow_high, expected, label in (
    (7, 18, "明日风雨添凉，伞与外套，皆不可少。", "大幅降温 + 雨"),
    (14, 18, "明日风雪骤寒，厚衣务必加身。", "大幅降温 + 雪"),
    (7, 32, "明日雨暖交加，伞可携，衣勿过厚。", "大幅升温 + 雨"),
    (0, 18, "明日气温骤降，不加衣服冻死你喵。", "大幅降温"),
    (0, 32, "明日暖意回升，衣可稍减。", "大幅升温"),
    (7, 22, "明日细雨将至，出门莫忘带伞。", "有雨"),
    (14, 22, "明日有雪，添衣保暖。", "有雪"),
    (6, 22, "明日雨雪交加，厚衣与伞皆需。", "雨夹雪"),
    (0, 21, "明日微凉，衣可加一件。", "普通降温"),
    (0, 29, "明日稍暖，衣可略减。", "普通升温"),
    (0, 25, "明日晴光可期，宜出门走走。", "晴"),
    (1, 25, "明日云影轻移，天气尚可。", "多云"),
    (2, 25, "明日天色微沉，心情自可明朗。", "阴"),
    (18, 25, "明日晨雾未散，行路宜缓。", "雾"),
    (53, 25, "明日霾色未清，口罩护身。", "霾"),
    (20, 25, "明日风沙将起，护好口鼻。", "沙尘"),
    (32, 25, "明日风大，行路当心。", "大风"),
):
    text = tomorrow_text(canonical, tomorrow_high)
    check(f"明日文案 {label}", text == expected, text)
check("明日未知天气不提醒", tomorrow_text(codes.UNKNOWN, 25) == "")


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
