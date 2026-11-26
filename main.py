"""
天气
A Class Widgets plugin.

两条铁律（都在真实 CW2 里量过）：

1. **GUI 线程只做「发请求 + 收信号」**。会阻塞的活儿（城市库查询、在线搜索、
   逆地理编码、IP 定位）都交给 :class:`BackgroundTasks`；天气抓取用
   :class:`service.FetchWorker`（每个城市一个 QThread），一次抓取就是一个完整的
   请求-响应，不需要再拆。
2. **配置写入要合并、要跳过没变化的**。CW2 的 ``ConfigBaseModel.__setattr__``
   每写一个字段都会做一次全量同步（``model_dump()`` + ``configChanged.emit()``），
   主窗口里所有绑定在 ``Configs.data`` 上的地方都要重算，实测一次 250~300ms——
   比一次天气 HTTP 请求还贵。所有写配置的地方都用 :meth:`Plugin._config_batch`。
"""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ClassWidgets.SDK import CW2Plugin, PluginAPI
from loguru import logger
from PySide6.QtCore import (
    Property,
    QObject,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtQml import QJSValue

from weather import geolocate, service
from weather.cities import PROVINCE_CITY_LIMIT, SEARCH_LIMIT, built_in_cities
from weather.config import WeatherConfig
from weather.models import CITY, COORDINATES
from weather.providers import get_provider_class

WIDGET_ID = "helloswx.weather"
WIDGET_DEFAULTS = {
    # 空字符串表示始终跟随插件的默认城市
    "city_id": "",
    # current | high_low —— 主体屏幕显示实时温度还是当日最高 / 最低温
    "content_mode": "current",
    # 额外加入「未来 3 小时」屏幕
    "show_hourly": False,
    # 主体与次级屏幕之间轮播
    "carousel": True,
    # 主体屏幕停留时长（优先级最高，停留更久）
    "main_seconds": 12,
    # 降水预报 / 天气预警屏幕停留时长
    "detail_seconds": 6,
}

#: 设置页城市查询结果的缓存上限（条目）
_CITY_CACHE_LIMIT = 8
_SEARCH_CACHE_LIMIT = 32


def _qml_to_python(value: Any) -> Any:
    """Convert QML object arguments (QJSValue) into Python containers.

    PySide6 may deliver an inline QML object passed to a ``QVariant`` slot as
    ``QJSValue`` instead of ``dict``.  Keep this conversion at the API
    boundary so the rest of the weather/config code can stay type-agnostic.
    """
    if not isinstance(value, QJSValue):
        if isinstance(value, dict):
            return {str(key): _qml_to_python(item) for key, item in value.items()}
        if isinstance(value, list):
            return [_qml_to_python(item) for item in value]
        return value

    converted = value.toVariant()
    if (
        converted is not None
        and converted is not value
        and not isinstance(converted, QJSValue)
    ):
        return _qml_to_python(converted)
    if value.isArray():
        length = int(value.property("length").toVariant() or 0)
        return [_qml_to_python(value.property(str(index))) for index in range(length)]
    if value.isObject():
        # QJSValue does not expose enumerable keys consistently across Qt
        # versions; these are the keys accepted by addCityRecord().
        keys = (
            "id",
            "name",
            "short_name",
            "code",
            "mode",
            "latitude",
            "longitude",
            "provider_codes",
        )
        return {
            key: _qml_to_python(value.property(key))
            for key in keys
            if value.hasProperty(key)
        }
    return None


def _coordinates_of(city: dict[str, Any]) -> tuple[float | None, float | None]:
    """取出成对的经纬度；缺一个就当作没有坐标。"""
    try:
        latitude = float(city["latitude"])
        longitude = float(city["longitude"])
    except (KeyError, TypeError, ValueError):
        return None, None
    return latitude, longitude


class _Job(QThread):
    """跑一次同步调用，结果通过 :attr:`finishedWith` 回到 GUI 线程。"""

    finishedWith = Signal(object)

    def __init__(
        self,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._func = func
        self._args = args
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        if self._cancelled:
            return
        try:
            result = self._func(*self._args)
        except Exception as error:  # noqa: BLE001 - 后台任务失败只影响这一次结果，不能打断 GUI
            logger.warning(
                f"后台任务 {getattr(self._func, '__name__', self._func)} 失败: {error}"
            )
            result = None
        if not self._cancelled:
            self.finishedWith.emit(result)


class BackgroundTasks(QObject):
    """按 key 单飞的后台任务：同一个 key 只保留最新的那个。

    迟到的旧结果会被直接丢掉，所以「连续敲字搜索」「反复切换省份」不会把过时
    的结果塞回 QML；``cancel`` 只是丢弃结果——已经发出的 HTTP 请求没法中断，
    但它不会阻塞 GUI 线程。

    每个任务一个 :class:`_Job`（跟 CW2 其它插件一样用 QThread），挂在
    ``BackgroundTasks`` 下当子对象，结束即 ``deleteLater``，不阻塞也不用管生命周期。
    """

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._jobs: dict[str, _Job] = {}
        self._generations: dict[str, int] = {}

    def submit(
        self,
        key: str,
        func: Callable[..., Any],
        on_done: Callable[..., None],
        *args: Any,
    ) -> None:
        self.cancel(key)
        generation = self._generations.get(key, 0) + 1
        self._generations[key] = generation
        job = _Job(func, args, self)
        job.finishedWith.connect(
            lambda result, a=args, k=key, g=generation: self._deliver(
                k, g, result, on_done, a
            )
        )
        # finished 在 run() 返回之后才发，所以这里丢掉引用 / 销毁线程都是安全的
        job.finished.connect(lambda w=job, k=key, g=generation: self._forget(k, g, w))
        job.finished.connect(job.deleteLater)
        self._jobs[key] = job
        job.start()

    def _deliver(
        self,
        key: str,
        generation: int,
        result: Any,
        on_done: Callable[..., None],
        args: tuple[Any, ...],
    ) -> None:
        if self._generations.get(key) != generation:
            return  # 已经被更新的请求顶掉了
        on_done(result, *args)

    def _forget(self, key: str, generation: int, job: _Job) -> None:
        if self._jobs.get(key) is job:
            self._jobs.pop(key, None)

    def cancel(self, key: str) -> None:
        job = self._jobs.pop(key, None)
        if job is None:
            return
        job.cancel()
        self._generations[key] = self._generations.get(key, 0) + 1

    def cancel_all(self) -> None:
        for key in tuple(self._jobs):
            self.cancel(key)

    def shutdown(self, timeout_ms: int = 300) -> None:
        """插件卸载时调用：作废所有结果，最多等 ``timeout_ms`` 收尾。

        没在截止时间内结束的（在线搜索这种要发网络请求的）强制终止：还活着的
        QThread 跟着插件一起销毁会让 Qt 直接 abort。线程这时多半阻塞在 socket
        读上（GIL 已放开），terminate() 收得回来。
        """
        jobs = tuple(self._jobs.values())
        self.cancel_all()
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        for job in jobs:
            job.wait(max(0, int((deadline - time.monotonic()) * 1000)))
        for job in jobs:
            if job.isRunning():
                logger.warning("后台任务未在截止时间内结束，强制终止")
                job.terminate()
                job.wait(200)


class Plugin(CW2Plugin):
    dataChanged = Signal()
    configChanged = Signal()
    locatingChanged = Signal()
    # 城市库是异步的：结果分别通过下面三个信号回传，providerId 用来丢弃
    # 切换数据源之后迟到的响应。
    provincesLoaded = Signal(str, "QVariant")
    cityListLoaded = Signal(str, int, "QVariant")
    citiesFound = Signal(str, int, "QVariant")

    def __init__(self, api: PluginAPI):
        super().__init__(api)
        self.config = WeatherConfig()
        service.migrate_cities(self.config)
        self._payload: dict[str, Any] = {"available": False, "error": "正在获取天气…"}
        self._city_payloads: dict[str, dict[str, Any]] = {}
        self._fetch_workers: dict[str, service.FetchWorker] = {}
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._tasks = BackgroundTasks(self)
        self._locating = False
        self._settings_page = Path("qml") / "SettingsPage.qml"
        # 城市库查询结果缓存：第二次打开对话框时直接命中，不再等后台线程。
        # 都有上限——缓存只是省一次往返，不值得一直占着内存。
        self._province_cache: dict[str, list[str]] = {}
        self._city_cache: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self._search_cache: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        # 攒着一次把配置改动同步给 CW2，见 _config_batch
        self._sync_pending = False
        self._sync_timer = QTimer(self)
        self._sync_timer.setSingleShot(True)
        self._sync_timer.setInterval(400)
        self._sync_timer.timeout.connect(self._flush_config_sync)

    # ------------------------------------------------------------------ 生命周期

    @contextmanager
    def _config_batch(self) -> Iterator[None]:
        """把一次操作里的多次配置写入合并成一次同步。

        CW2 的 ``ConfigBaseModel.__setattr__`` 每写一个字段都会 ``model_dump()`` +
        ``configChanged.emit()``，主窗口里所有绑定在 ``Configs.data`` 上的地方都要
        重算——实测一次 250~300ms，而且写一个没变化的字段和写新字段一样贵。
        「切换默认城市」要写 default_city_id + city_code + city_name…，于是就变成
        一秒多的卡顿；``settings()`` 这种只读路径更糟，它顺带 ``migrate_cities()``
        就会写三次。

        所以：借用 CW2 自己的 ``_on_change`` 钩子，批处理期间先挡住回调，退出时统一
        刷一次，并且推迟 400ms 让连续几次操作合成一次。插件界面靠自己的
        ``configChanged`` 信号刷新，不依赖这次同步，所以推迟不影响观感。
        CW2 换了实现（没有 ``_on_change``）就退化成逐次写入，不会出错。
        """
        original = getattr(self.config, "_on_change", None)
        if not callable(original):
            yield
            return
        touched = {"count": 0}

        def defer() -> None:
            touched["count"] += 1

        try:
            self.config._on_change = defer
        except Exception:  # noqa: BLE001 - CW2 的模型不保证有这个属性，挂不上就放弃合并
            yield
            return
        try:
            yield
        finally:
            try:
                self.config._on_change = original
            except Exception:  # noqa: BLE001, S110 - 还原失败没有别的补救办法
                pass
            if touched["count"]:
                self._sync_pending = True
                self._sync_timer.start()

    @Slot()
    def _flush_config_sync(self) -> None:
        """把攒下来的配置改动同步给 CW2（一次 ~270ms，所以要合并）。"""
        if not self._sync_pending:
            return
        self._sync_pending = False
        sync = getattr(self.config, "_on_change", None)
        if not callable(sync):
            return
        try:
            sync()
        except Exception as error:  # noqa: BLE001 - CW2 自己的回调，出错也不能带崩插件
            logger.warning(f"配置同步失败: {error}")

    def on_load(self):
        super().on_load()

        self.api.config.register_plugin_model(self.pid, self.config)
        with self._config_batch():
            service.migrate_cities(self.config)
            # 城市库改成了长 / 短两套名字，老配置里的旧格式按编号重新对一次
            named = service.refresh_city_names(self.config.cities, self.config.provider)
            if named != (self.config.cities or []):
                self.config.cities = named
            service.sync_legacy_location(
                self.config,
                next(
                    (
                        city
                        for city in self.config.cities
                        if city["id"] == self.defaultCityId()
                    ),
                    None,
                ),
            )
        self.api.config.save()

        self.api.widgets.register(
            widget_id=WIDGET_ID,
            name="天气",
            qml_path=Path("qml") / "WeatherWidget.qml",
            backend_obj=self,
            settings_qml=Path("qml") / "WidgetSettings.qml",
            default_settings=dict(WIDGET_DEFAULTS),
        )
        self.api.ui.register_settings_page(
            self._settings_page,
            title="天气",
            icon="ic_fluent_weather_partly_cloudy_day_20_regular",
        )

        self._restart_timer()
        # 城市库索引 + 拼音表提前在后台建好，用户第一次打开「添加城市」时就不用
        # 等（几十毫秒的 GIL 时间，放在这里付比放在点击之后好）。
        self._tasks.submit(
            "warmup", _warm_city_index, lambda *_: None, self.config.provider
        )
        # 首次安装（从未定过位）时按 IP 自动选一次城市，之后一律手动
        if not self.config.located_once and not self.config.cities:
            self.locateAutomatically()
        else:
            self.refresh()

    def on_unload(self):
        self._timer.stop()
        self._flush_config_sync()
        self._tasks.shutdown(300)
        workers = tuple(self._fetch_workers.values())
        for worker in workers:
            self._discard_fetch_worker(worker)
        self._fetch_workers.clear()
        # 抓取线程必须自己收尾：还在 run() 的 QThread 被销毁会让 Qt 直接 abort。
        # 网络请求没法中断，所以先正常等一段（共用一个截止时间，别一个一个等）；
        # 还没结束的（网络卡住）只能强制终止——线程此刻阻塞在 socket 读上，是
        # pthread 的取消点，terminate() 能立刻收回来。
        deadline = time.monotonic() + 1.2
        for worker in workers:
            worker.wait(max(0, int((deadline - time.monotonic()) * 1000)))
        for worker in workers:
            if worker.isRunning():
                logger.warning("抓取线程未在截止时间内结束，强制终止")
                worker.terminate()
                worker.wait(500)
        try:
            self.api.ui.unregister_settings_page(self._settings_page)
        except Exception:  # noqa: BLE001, S110 - 卸载时页面可能已经没了
            pass

    def _restart_timer(self):
        minutes = max(5, int(self.config.refresh_minutes or 5))
        self._timer.start(minutes * 60 * 1000)

    # ------------------------------------------------------------------ 天气数据

    @Property("QVariant", notify=dataChanged)
    def data(self) -> dict[str, Any]:
        return self._payload

    @Property(str, notify=configChanged)
    def provider(self) -> str:
        """当前数据源 id；设置页用它丢弃切换数据源之后迟到的城市列表。"""
        return self.config.provider

    @staticmethod
    def _discard_fetch_worker(worker: service.FetchWorker) -> None:
        """让抓取线程自己结束，但结果不再进 GUI 线程。"""
        try:
            worker.finishedWith.disconnect()
            worker.requestInterruption()
        except RuntimeError:
            pass  # C++ 对象已被删除

    def _cancel_fetch_workers(self) -> None:
        """取消正在运行的天气获取任务（不阻塞 GUI 线程）。"""
        for worker in tuple(self._fetch_workers.values()):
            self._discard_fetch_worker(worker)
        self._fetch_workers.clear()

    @Slot()
    def refresh(self) -> None:
        """异步刷新所有城市；``data`` 始终代表默认城市。"""
        cities = service.migrate_cities(self.config)
        provider = service.make_provider(self.config)
        if provider is None:
            self._apply_for_city(
                None,
                service.build_payload(
                    None,
                    service.location_from_config(self.config),
                    None,
                    "天气源不可用",
                ),
            )
            return
        if provider.requires_key and not provider.api_key:
            self._apply_for_city(
                None,
                service.build_payload(
                    None,
                    service.location_from_config(self.config),
                    provider,
                    "未填写 API Key",
                ),
            )
            return
        if not cities:
            self._apply_for_city(
                None,
                service.build_payload(
                    None,
                    service.location_from_config(self.config),
                    provider,
                    "未选择城市",
                ),
            )
            return
        for city in cities:
            city_id = str(city["id"])
            if city_id in self._fetch_workers:
                continue
            location = service.location_from_city(city, self.config.provider)
            if not location.configured:
                self._apply_for_city(
                    city_id,
                    service.build_payload(
                        None, location, provider, "当前数据源没有该城市映射"
                    ),
                )
                continue
            worker = service.FetchWorker(provider, location, self)
            worker.finishedWith.connect(
                lambda payload, cid=city_id: self._apply_for_city(cid, payload)
            )
            worker.finished.connect(worker.deleteLater)
            worker.finished.connect(
                lambda w=worker, cid=city_id: self._clear_fetch_worker(cid, w)
            )
            self._fetch_workers[city_id] = worker
            worker.start()

    @Slot("QVariant", dict)
    def _apply_for_city(self, city_id: str | None, payload: dict[str, Any]) -> None:
        if city_id:
            self._city_payloads[str(city_id)] = payload
        default_id = self.defaultCityId()
        self._payload = self._city_payloads.get(default_id, payload)
        self.dataChanged.emit()

    def _clear_fetch_worker(
        self, city_id: str = "", worker: service.FetchWorker | None = None
    ) -> None:
        """在 worker 完成后清空引用。

        比对一下身份：被取消的旧 worker 迟到时不能把新 worker 的引用清掉。
        """
        if city_id and self._fetch_workers.get(str(city_id)) is worker:
            self._fetch_workers.pop(str(city_id), None)

    # ------------------------------------------------------------------ 设置页接口

    @Slot(result="QVariant")
    def providers(self) -> list[dict[str, Any]]:
        return service.describe_providers()

    @Slot(result="QVariant")
    def settings(self) -> dict[str, Any]:
        """当前配置 + 城市列表 + 当前提供商能力。"""
        provider_class = get_provider_class(self.config.provider)
        api_keys = self.config.api_keys or {}
        cities = self._city_options()
        default_id = self.defaultCityId()
        default = next((item for item in cities if item["id"] == default_id), None)
        location = (
            service.location_from_city(default, self.config.provider)
            if default
            else service.location_from_config(self.config)
        )
        return {
            "provider": self.config.provider,
            "providerName": provider_class.name if provider_class else "",
            "requiresKey": bool(provider_class and provider_class.requires_key),
            "supportsCoordinates": bool(
                provider_class and provider_class.supports_coordinates
            ),
            "hasCityList": bool(provider_class and provider_class.database),
            "onlineSearch": bool(provider_class and not provider_class.database),
            "locationMode": location.mode,
            "cityCode": location.code,
            # 列表 / CityClip 用长名称，这个汇总行也跟着用长名称
            "cityName": location.name or location.label,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "apiKey": api_keys.get(self.config.provider, ""),
            "refreshMinutes": self.config.refresh_minutes,
            "located": bool(default and location.configured),
            "error": self._payload.get("error", ""),
            "locating": self._locating,
            "cities": cities,
            "defaultCityId": default_id,
        }

    @Slot(result=str)
    def defaultCityId(self) -> str:
        cities = service.migrate_cities(self.config)
        if self.config.default_city_id and any(
            str(item["id"]) == self.config.default_city_id for item in cities
        ):
            return self.config.default_city_id
        return str(cities[0]["id"]) if cities else ""

    def _city_options(self) -> list[dict[str, Any]]:
        cities = service.migrate_cities(self.config)
        default_id = self.defaultCityId()
        result: list[dict[str, Any]] = []
        for city in cities:
            item = service.normalize_city(city)
            resolved = service.city_for_provider(item, self.config.provider)
            payload = self._city_payloads.get(item["id"], {})
            item.update(
                {
                    "provider": self.config.provider,
                    "providerCode": (resolved or {}).get("code", ""),
                    "available": bool(payload.get("available")),
                    "temperature": payload.get("temperature", ""),
                    "description": payload.get("description", ""),
                    "error": payload.get("error", ""),
                    "isDefault": item["id"] == default_id,
                    "canRemove": len(cities) > 1,
                }
            )
            result.append(item)
        return result

    @Slot(result="QVariant")
    def cityOptions(self) -> list[dict[str, Any]]:
        return self._city_options()

    @Slot(str, result="QVariant")
    def dataForCity(self, city_id: str) -> dict[str, Any]:
        return self._city_payloads.get(str(city_id), self._payload)

    @Slot(str)
    def setProvider(self, provider_id: str) -> None:
        """切换提供商，保留规范城市并尽可能建立新的 provider 映射。"""
        if not provider_id or provider_id == self.config.provider:
            return
        if get_provider_class(provider_id) is None:
            return

        with self._config_batch():
            self.config.provider = provider_id
            self.config.located_once = True
            mapped = []
            for city in service.migrate_cities(self.config):
                resolved = service.city_for_provider(city, provider_id)
                if resolved:
                    city = service.normalize_city(resolved, provider_id)
                mapped.append(city)
            self.config.cities = mapped
            service.sync_legacy_location(
                self.config,
                next((c for c in mapped if c["id"] == self.defaultCityId()), None),
            )
        self.api.config.save()
        self.configChanged.emit()
        # 立即触发 dataChanged 确保前端能获取到最新的 requiresKey 状态
        self.dataChanged.emit()
        self.refresh()

    @Slot(str, str)
    def setApiKey(self, provider_id: str, key: str) -> None:
        keys = dict(self.config.api_keys or {})
        keys[provider_id or self.config.provider] = key or ""
        if keys == (self.config.api_keys or {}):
            return
        with self._config_batch():
            self.config.api_keys = keys
        self.api.config.save()
        self.configChanged.emit()
        self.refresh()

    @Slot(int)
    def setRefreshMinutes(self, minutes: int) -> None:
        minutes = max(5, int(minutes))
        if minutes == self.config.refresh_minutes:
            return
        with self._config_batch():
            self.config.refresh_minutes = minutes
        self.api.config.save()
        self._restart_timer()
        self.configChanged.emit()

    @Slot(str, str, float, float)
    def setCity(self, code: str, name: str, latitude: float, longitude: float) -> None:
        """兼容旧调用：添加或更新一个城市并设为默认。"""
        short_name = ""
        if code:
            # 旧调用没有短名称，按编号从城市库里补一个（小组件上显示的就是它）
            repository = service.repository_for(self.config.provider)
            row = repository.city_for_code(code) if repository else {}
            name = name or str(row.get("name") or "")
            short_name = str(row.get("short_name") or "")
        city = service.normalize_city(
            {
                "name": name,
                "short_name": short_name,
                "code": code,
                "mode": CITY,
                "latitude": latitude or None,
                "longitude": longitude or None,
            },
            self.config.provider,
        )
        self.addCityRecord(city)

    @Slot("QVariant")
    def addCityRecord(self, city: dict[str, Any]) -> None:
        city = _qml_to_python(city)
        if not isinstance(city, dict):
            logger.warning(
                "addCityRecord() ignored a non-object city argument: %r", city
            )
            return
        with self._config_batch():
            self.config.located_once = True
        latitude, longitude = _coordinates_of(city)
        if (
            not str(city.get("name") or "").strip()
            and latitude is not None
            and longitude is not None
        ):
            # 逆地理编码要走网络，放后台线程：主线程最多等它几百毫秒就会卡住
            # CW 的动画，也让「添加城市」按钮看起来像没反应。
            self._tasks.submit(
                f"geocode:{latitude:.4f},{longitude:.4f}",
                geolocate.city_name_at,
                self._apply_geocoded_city,
                latitude,
                longitude,
                5.0,
                dict(city),
            )
            return
        self._commit_city(city)

    def _apply_geocoded_city(
        self,
        result: Any,
        latitude: float,
        longitude: float,
        _timeout: float,
        city: dict[str, Any],
    ) -> None:
        """逆地理编码结束后回到 GUI 线程，把真名补上。"""
        name = str(result or "").strip()
        self._commit_city(city | {"name": name or f"{latitude:.2f}, {longitude:.2f}"})

    def _commit_city(self, city: dict[str, Any]) -> None:
        """把一条城市记录合并进配置并刷新天气（只在 GUI 线程执行）。"""
        with self._config_batch():
            record = service.normalize_city(city, self.config.provider)
            existing = service.migrate_cities(self.config)
            duplicate = next(
                (
                    item
                    for item in existing
                    if item.get("name") == record.get("name")
                    and item.get("mode") == record.get("mode")
                ),
                None,
            )
            if duplicate:
                record["id"] = duplicate["id"]
                merged_codes = dict(duplicate.get("provider_codes") or {})
                merged_codes.update(record.get("provider_codes") or {})
                record["provider_codes"] = merged_codes
                existing[existing.index(duplicate)] = record
            else:
                existing.append(record)
            self.config.cities = existing
            if not self.config.default_city_id:
                self.config.default_city_id = record["id"]
            default = next(
                (
                    item
                    for item in existing
                    if item["id"] == self.config.default_city_id
                ),
                record,
            )
            service.sync_legacy_location(self.config, default)
        self.api.config.save()
        self.configChanged.emit()
        self._cancel_fetch_workers()
        self.refresh()

    @Slot(str)
    def removeCity(self, city_id: str) -> None:
        current = service.migrate_cities(self.config)
        if len(current) <= 1:
            return
        cities = [item for item in current if str(item.get("id")) != str(city_id)]
        with self._config_batch():
            self.config.cities = cities
            if self.config.default_city_id == str(city_id):
                self.config.default_city_id = str(cities[0]["id"])
            service.sync_legacy_location(
                self.config,
                next(item for item in cities if item["id"] == self.defaultCityId()),
            )
        self.api.config.save()
        self.configChanged.emit()
        self._cancel_fetch_workers()
        self.refresh()

    @Slot(str)
    def setDefaultCity(self, city_id: str) -> None:
        if not any(
            str(item.get("id")) == str(city_id)
            for item in service.migrate_cities(self.config)
        ):
            return
        with self._config_batch():
            self.config.default_city_id = str(city_id)
            service.sync_legacy_location(
                self.config,
                next(
                    item
                    for item in self.config.cities
                    if item["id"] == self.config.default_city_id
                ),
            )
        self.api.config.save()
        self.configChanged.emit()
        self._apply_for_city(
            self.config.default_city_id,
            self._city_payloads.get(self.config.default_city_id, self._payload),
        )

    @Slot(float, float, str)
    def setCoordinates(self, latitude: float, longitude: float, name: str) -> None:
        """设置坐标位置；如果 name 为空，会在后台做逆地理编码。"""
        self.addCityRecord(
            {
                "name": name or "",
                "mode": COORDINATES,
                "latitude": latitude,
                "longitude": longitude,
            }
        )

    # ------------------------------------------------------------------ 城市列表（异步）

    @Slot(result="QVariant")
    def provinces(self) -> list[str]:
        """请求省份列表。结果通过 :attr:`provincesLoaded` 回传。

        已经拿到过数据时同步返回缓存，QML 可以立刻渲染，不必等后台线程。
        """
        provider_id = self.config.provider
        cached = self._province_cache.get(provider_id)
        if cached is not None:
            return cached
        self._tasks.submit(
            "provinces",
            _load_provinces,
            self._publish_provinces,
            provider_id,
        )
        return []

    def _publish_provinces(self, result: Any, provider_id: str) -> None:
        provinces = list(result or [])
        self._province_cache[provider_id] = provinces
        self.provincesLoaded.emit(provider_id, provinces)

    @Slot(int, result="QVariant")
    def citiesIn(self, province_index: int) -> list[dict[str, Any]]:
        """请求某个省份（或热门城市）的列表，结果通过 :attr:`cityListLoaded` 回传。"""
        provider_id = self.config.provider
        key = (provider_id, int(province_index))
        cached = self._city_cache.get(key)
        if cached is not None:
            return cached
        self._tasks.submit(
            "cities",
            _load_cities,
            self._publish_cities,
            provider_id,
            int(province_index),
        )
        return []

    def _publish_cities(
        self, result: Any, provider_id: str, province_index: int
    ) -> None:
        cities = list(result or [])
        if len(self._city_cache) >= _CITY_CACHE_LIMIT:
            self._city_cache.clear()
        self._city_cache[(provider_id, province_index)] = cities
        self.cityListLoaded.emit(provider_id, province_index, cities)

    @Slot(str, int)
    def searchCities(self, term: str, province_index: int = -1) -> None:
        """搜索城市，结果通过 :attr:`citiesFound` 回传。

        有内置城市库的数据源走本地索引（支持汉字 / 全拼 / 首字母），只认坐标的
        数据源走在线地理编码。两种都在后台线程里跑。
        """
        term = str(term or "").strip()
        provider_id = self.config.provider
        province_index = int(province_index)
        if not term:
            self.citiesIn(province_index)
            return
        key = (provider_id, term, province_index)
        cached = self._search_cache.get(key)
        if cached is not None:
            self.citiesFound.emit(term, province_index, cached)
            return
        provider = service.make_provider(self.config)
        if service.repository_for(provider_id) is not None:
            self._tasks.submit(
                "search",
                _search_local_cities,
                self._publish_search,
                provider_id,
                term,
                province_index,
            )
        elif provider is not None:
            self._tasks.submit(
                "search",
                _search_online_cities,
                self._publish_search,
                provider_id,
                term,
                province_index,
                provider,
            )
        else:
            self.citiesFound.emit(term, province_index, [])

    def _publish_search(
        self, result: Any, provider_id: str, term: str, province_index: int
    ) -> None:
        cities = list(result or [])
        if len(self._search_cache) >= _SEARCH_CACHE_LIMIT:
            self._search_cache.clear()
        self._search_cache[(provider_id, term, province_index)] = cities
        self.citiesFound.emit(term, province_index, cities)

    # ------------------------------------------------------------------ 自动定位

    @Property(bool, notify=locatingChanged)
    def locating(self) -> bool:
        return self._locating

    @Slot()
    def locateAutomatically(self) -> None:
        """按 IP 粗定位并写入配置（网络请求在后台线程）。"""
        if self._locating:
            return
        provider_id = self.config.provider
        self._locating = True
        self.locatingChanged.emit()
        self._tasks.submit("locate", _locate, self._apply_location, provider_id)

    def _apply_location(self, resolved: Any) -> None:
        """定位任务回到 GUI 线程后落配置。"""
        self._locating = False
        with self._config_batch():
            self.config.located_once = True
        if resolved:
            self.addCityRecord(dict(resolved))
        else:
            self.api.config.save()
            self.refresh()
        self.locatingChanged.emit()
        self.configChanged.emit()


def _load_provinces(provider_id: str) -> list[str]:
    repository = service.repository_for(provider_id)
    return repository.provinces() if repository else []


def _warm_city_index(provider_id: str) -> None:
    """后台预热城市库索引，见 :meth:`Plugin.on_load`。"""
    repository = service.repository_for(provider_id)
    if repository is not None:
        repository.warm_up()


def _load_cities(provider_id: str, province_index: int) -> list[dict[str, Any]]:
    """工作线程：建索引 + 取列表。"""
    repository = service.repository_for(provider_id)
    if repository is None:
        # 坐标型数据源也内置直辖市和省会，空搜索时直接展示
        return built_in_cities() if province_index < 0 else []
    return repository.cities(province_index, PROVINCE_CITY_LIMIT)


def _search_local_cities(
    provider_id: str, term: str, province_index: int
) -> list[dict[str, Any]]:
    repository = service.repository_for(provider_id)
    if repository is None:
        return []
    scope = None if province_index < 0 else province_index
    return repository.search(term, SEARCH_LIMIT, scope)


def _search_online_cities(
    provider_id: str, term: str, province_index: int, provider: Any
) -> list[dict[str, Any]]:
    try:
        return list(provider.search_cities(term) or [])[:SEARCH_LIMIT]
    except Exception:  # noqa: BLE001 - 在线搜索失败就当没搜到
        return []


def _locate(provider_id: str) -> dict[str, Any] | None:
    """工作线程：IP 粗定位 + 换算成当前数据源能用的位置。"""
    try:
        fix = geolocate.locate()
        if not fix:
            return None
        return service.resolve_auto_location(provider_id, fix) or {}
    except Exception:  # noqa: BLE001 - 定位失败退回手动选城市
        return {}
