"""离屏加载并驱动插件设置页，验证控件真的能用。

用真实的 Plugin 后端（只把 Class Widgets 本体的 API 打桩），
所以校验到的是实际会跑的槽函数和信号。

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/settings_harness.py
"""

import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# RinUI 必须在 QGuiApplication 之前导入（它要设置 HiDPI 缩放策略）
sys.path.insert(
    0,
    str(
        Path.home()
        / "Class-Widgets-2"
        / ".venv"
        / "lib"
        / "python3.13"
        / "site-packages"
    ),
)
from pydantic import BaseModel
from PySide6.QtCore import QEventLoop, QObject, QTimer, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from RinUI.core import ThemeManager
from shiboken6 import isValid

import RinUI

# ---------------------------------------------------------------- 打桩 SDK
sdk = types.ModuleType("ClassWidgets.SDK")


class _ConfigBaseModel(BaseModel):
    pass


class _CW2Plugin(QObject):
    def __init__(self, api):
        super().__init__()
        self.PATH = ROOT
        self.meta = {"id": "cw2p-com.helloswx.weather"}
        self.pid = "cw2p-com.helloswx.weather"
        self.api = api

    def on_load(self):
        pass

    def on_unload(self):
        pass


sdk.ConfigBaseModel = _ConfigBaseModel
sdk.CW2Plugin = _CW2Plugin
sdk.PluginAPI = object
sys.modules["ClassWidgets.SDK"] = sdk


class _StubAPI:
    """只实现插件用到的那几个 API。"""

    class _Config:
        def register_plugin_model(self, plugin_id, model):
            pass

        def save(self):
            pass

    class _Widgets:
        def register(self, **kwargs):
            pass

    class _Ui:
        def register_settings_page(self, *args, **kwargs):
            pass

        def unregister_settings_page(self, *args, **kwargs):
            pass

    def __init__(self):
        self.config = self._Config()
        self.widgets = self._Widgets()
        self.ui = self._Ui()


import main


class Bridge(QObject):
    def __init__(self, plugin):
        super().__init__()
        self._plugin = plugin

    @Slot(str, result=QObject)
    def get_backend(self, plugin_id: str):
        return self._plugin


# ---------------------------------------------------------------- 启动引擎
app = QGuiApplication(sys.argv)

# qmldir 里写的是 module RinUI，所以 import path 要指向 RinUI 的父目录
RINUI_PATH = Path(RinUI.__file__).resolve().parent.parent
CW_QML = Path.home() / "Class-Widgets-2" / "src" / "qml"

plugin = main.Plugin(_StubAPI())
# 跳过首次自动定位，测试不依赖网络
plugin.config.located_once = True
plugin.on_load()

engine = QQmlApplicationEngine()
engine.addImportPath(str(RINUI_PATH))
engine.addImportPath(str(CW_QML))

problems = []
engine.warnings.connect(lambda errs: problems.extend(str(e.toString()) for e in errs))

theme_manager = ThemeManager()
bridge = Bridge(plugin)
engine.rootContext().setContextProperty("ThemeManager", theme_manager)
engine.rootContext().setContextProperty("PluginBackendBridge", bridge)

SETTINGS_URI = (ROOT / "qml" / "SettingsPage.qml").as_uri()

WRAPPER = f"""
import QtQuick
import QtQuick.Controls
import RinUI

ApplicationWindow {{
    width: 900; height: 700; visible: true
    property alias page: loader.item
    Loader {{
        id: loader
        anchors.fill: parent
        source: "{SETTINGS_URI}"
        onLoaded: item.pluginId = "cw2p-com.helloswx.weather"
    }}
}}
"""

engine.loadData(WRAPPER.encode())
if not engine.rootObjects():
    print("设置页加载失败：")
    for problem in problems:
        print("   ", problem)
    sys.exit(1)

window = engine.rootObjects()[0]
page = window.property("page")

results = []


def check(label, ok, detail=""):
    results.append(ok)
    print(f"{'  ok ' if ok else 'FAIL '}{label}{(' — ' + detail) if detail else ''}")


def by_name(name):
    return window.findChild(QObject, name)


def _prop_names(obj):
    meta = obj.metaObject()
    return {meta.property(i).name() for i in range(meta.propertyCount())}


def tooltips_of(item):
    """挂在 item 下面的 ToolTip（ToolTip 是弹窗，不在视觉树上，只能这么找）。"""
    return [
        obj
        for obj in item.findChildren(QObject)
        if obj.metaObject().className().startswith("ToolTip") and isValid(obj)
    ]


def city_clips():
    """城市卡片（CityClip 委托）。

    Repeater 委托不是 QObject 子对象，findChildren 到不了；
    弹窗也不在视觉树上，所以只能认准网格再从它的 childItems 里取。
    """
    clips = []
    for obj in page.findChildren(QObject):
        if obj.metaObject().className() != "QQuickGrid":
            continue
        clips.extend(
            child
            for child in obj.childItems()
            if "CityClip" in child.metaObject().className() and isValid(child)
        )
    return clips


def wait_until(predicate, timeout_ms=3000):
    """城市库是异步的：转事件循环等结果回来，顺便证明它没有卡住 GUI 线程。"""
    loop = QEventLoop()
    deadline = {"hit": False}
    poll = QTimer()
    poll.setInterval(10)
    poll.timeout.connect(lambda: deadline.update(hit=True) if predicate() else None)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(loop.quit)
    poll.start()
    watchdog.start(timeout_ms)
    loop.exec()
    poll.stop()
    return deadline["hit"] or predicate()


def run_checks():
    print("\n=== 设置页控件 ===")
    check("页面已加载", page is not None)

    provider_box = by_name("providerBox")
    check("数据源下拉存在", provider_box is not None)
    if provider_box:
        model = provider_box.property("model")
        count = model.property("count") if model else 0
        check("数据源列表非空", count == 5, f"count={count}")
        provider_ids = [item["id"] for item in plugin.providers()]
        expected_index = provider_ids.index(plugin.config.provider)
        check(
            "当前项与配置一致",
            provider_box.property("currentIndex") == expected_index,
            f"currentIndex={provider_box.property('currentIndex')}"
            f"（{plugin.config.provider} 应为 {expected_index}）",
        )
        check(
            "下拉显示的是名称而非空白",
            bool(provider_box.property("displayText")),
            f"displayText={provider_box.property('displayText')!r}",
        )

    for name, label in (
        ("apiKeyCard", "API Key 卡片"),
        ("cityCard", "城市卡片"),
        ("refreshBox", "刷新间隔"),
        ("locateButton", "自动定位按钮"),
        ("cityDialog", "城市对话框"),
    ):
        check(f"{label}存在", by_name(name) is not None)

    print("\n=== Tooltip 用的是 RinUI 的 ===")
    # 光看有没有报错抓不到这个问题：ToolTip.text 附加属性加载时不报任何警告，
    # 但拿到的是 Qt 自带的样式。只有 RinUI 的 ToolTip 才有下面这几个属性。
    tips = tooltips_of(by_name("locateButton")) + tooltips_of(by_name("chooseButton"))
    check("两个按钮各有 ToolTip", len(tips) == 2, f"{len(tips)} 个")
    check(
        "都是 RinUI 的 ToolTip（带 backgroundColor）",
        bool(tips) and all("backgroundColor" in _prop_names(t) for t in tips),
    )
    check(
        "RinUI 的 ToolTip 不会自动消失（timeout=-1）",
        bool(tips) and all(t.property("timeout") == -1 for t in tips),
    )
    tip_texts = sorted(t.property("text") for t in tips)
    check(
        "提示文案正确",
        tip_texts == sorted(["添加当前位置", "添加城市"]),
        str(tip_texts),
    )

    print("\n=== 后端槽函数 ===")
    check("providers() 返回 5 个", len(plugin.providers()) == 5)
    info = plugin.settings()
    check(
        "settings() 含关键字段",
        all(
            k in info
            for k in ("provider", "cityName", "requiresKey", "hasCityList", "locating")
        ),
    )

    provinces = []
    plugin.provincesLoaded.connect(
        lambda pid, items: provinces.clear() or provinces.extend(items)
    )
    started = time.perf_counter()
    plugin.provinces()
    check(
        "provinces() 不阻塞 GUI 线程",
        time.perf_counter() - started < 0.01,
        f"{(time.perf_counter() - started) * 1000:.2f} ms",
    )
    check(
        "provinces() 结果异步回来",
        wait_until(lambda: len(provinces) == 34),
        f"{len(provinces)} 个",
    )

    cities = []
    plugin.cityListLoaded.connect(
        lambda pid, index, items: (
            cities.clear() or cities.extend(items) if index == 0 else None
        )
    )
    plugin.citiesIn(0)
    check(
        "citiesIn(0) 结果异步回来", wait_until(lambda: len(cities) > 0), str(cities[:1])
    )

    found = {}
    plugin.citiesFound.connect(
        lambda term, index, items: found.update({"term": term, "res": items})
    )
    plugin.searchCities("海口", -1)
    check(
        "searchCities 通过信号回传",
        wait_until(lambda: bool(found.get("res"))),
        str(found.get("res", [])[:1]),
    )
    plugin.searchCities("haikou", -1)
    check(
        "searchCities 支持拼音",
        wait_until(lambda: len(found.get("res") or []) > 0),
        str([item["name"] for item in (found.get("res") or [])[:3]]),
    )

    print("\n=== 切换数据源后城市重新映射 ===")
    plugin.setCity("101310101", "海口", 20.03, 110.32)
    check("选中城市后 located=True", plugin.settings()["located"])
    started = time.perf_counter()
    plugin.setProvider("amap_weather")
    elapsed = (time.perf_counter() - started) * 1000
    check(
        "setProvider 不卡 GUI 线程（< 16.7ms 一帧）",
        elapsed < 16.7,
        f"{elapsed:.1f} ms",
    )
    after = plugin.settings()
    check(
        "切换后城市保留并换成新数据源的编号",
        after["located"] and after["cities"][0]["providerCode"] == "460100",
        str(after["cities"][0].get("providerCode")),
    )
    provinces.clear()
    cities.clear()
    plugin.provinces()
    plugin.citiesIn(0)
    wait_until(lambda: len(provinces) == 34 and len(cities) > 0)
    check(
        "切换后城市库跟着换",
        len(provinces) == 34 and cities and cities[0]["name"] == "北京",
        cities[0]["name"] if cities else "",
    )
    check("切换到需要 Key 的源会标记 requiresKey", after["requiresKey"])

    print("\n=== 城市卡片的 Tooltip ===")
    # 卡片要等城市列表回来才实例化，所以这里才查得到每张卡片上的两个提示
    clips = city_clips()
    card_tips = [tip for clip in clips for tip in tooltips_of(clip)]
    check(
        "每张卡片都有 Tooltip",
        clips and len(card_tips) == 2 * len(clips),
        f"{len(clips)} 张卡片 / {len(card_tips)} 个 Tooltip",
    )
    check(
        "卡片上的 Tooltip 也是 RinUI 的",
        bool(card_tips) and all("backgroundColor" in _prop_names(t) for t in card_tips),
    )
    card_texts = [t.property("text") for t in card_tips]
    check(
        "卡片提示是坐标/数据源两行",
        any("\n" in (t or "") for t in card_texts),
        str(next((t for t in card_texts if "\n" in (t or "")), "")),
    )
    check("卡片操作按钮的提示存在", "城市操作" in card_texts)

    print("\n=== 城市对话框 ===")
    dialog = by_name("cityDialog")
    if dialog:
        dialog.setProperty("info", plugin.settings())
        QTimer.singleShot(0, dialog.open)

    QTimer.singleShot(300, dialog_open_check)


def dialog_cities():
    """对话框里的城市列表：``availableCities`` 是后端异步喂进来的。"""
    dialog = by_name("cityDialog")
    entries = dialog.property("availableCities")
    if entries is None:
        return 0
    return len(entries.toVariant() if hasattr(entries, "toVariant") else entries)


def dialog_open_check():
    dialog = by_name("cityDialog")
    check(
        "对话框异步列出了热门城市",
        wait_until(lambda: dialog_cities() > 0, 5000),
        f"{dialog_cities()} 个",
    )
    check(
        "坐标型数据源才显示经纬度入口", dialog.property("coordinatesSupported") is False
    )

    # 切回需要 Key 的数据源（支持坐标），确认经纬度入口会亮出来
    plugin.setProvider("open_meteo")
    dialog.setProperty("info", plugin.settings())
    check(
        "坐标型数据源显示经纬度入口",
        wait_until(lambda: dialog.property("coordinatesSupported") is True),
    )

    QTimer.singleShot(200, finish)


def finish():
    # 走一遍卸载路径：抓取线程必须自己收尾，否则退出时会 abort
    started = time.perf_counter()
    plugin.on_unload()
    print(
        f"\n=== 卸载 ===\n       （on_unload 用时 {(time.perf_counter() - started) * 1000:.1f} ms）"
    )

    real_problems = [p for p in problems if "MenuItem.qml" not in p]
    print(f"\n=== QML 告警（{len(real_problems)} 条）===")
    for problem in real_problems[:10]:
        print("   ", problem)
    # CW2 的 PluginPage 自己会把 ScrollBar 挂到非 Flickable 上，这不是插件的锅
    check(
        "没有来自本插件 QML 的报错",
        not any(
            "helloswx" in p for p in real_problems if "ScrollBar attached" not in p
        ),
    )

    failed = results.count(False)
    print(f"\n{'全部通过' if not failed else str(failed) + ' 项失败'}")
    app.exit(1 if failed else 0)


QTimer.singleShot(300, run_checks)
sys.exit(app.exec())
