"""城市数据库查询（data/*.db 来自 Class Widgets，名字由 tools/build_city_names.py 重写）。

每个城市有两套名字：``name`` 是长名称（``北京海淀``，列表和 CityClip 用），
``short_name`` 是短名称（``海淀区``，只给小组件显示）。

GUI 线程上绝不能做的事都集中在这里，所以查询分成两类：

* **轻量查询**（``provinces`` / ``search_by_name`` / ``name_for_code`` / ``popular``）
  只走一次 SQL，几毫秒以内，可以在主线程直接调用；
* **重量查询**（``cities`` / ``search``）要读全库 + 拼音表（每个库几千条），
  第一次十几毫秒，之后才是纯 Python 过滤，因此只允许在工作线程里调用。

每个城市库的索引在进程内只建一次，多个线程共用（:class:`_CityIndex` 内部
带锁）。
"""

import json
import sqlite3
import threading
from typing import Any

from loguru import logger

from . import DATA_DIR

# Used by coordinate-only providers so the add-city dialog is useful before
# the first online search. Database-backed providers still expose their full
# local list; these records also make provider switching deterministic.
MAJOR_CHINESE_CITIES = [
    ("北京", 39.9042, 116.4074),
    ("天津", 39.3434, 117.3616),
    ("上海", 31.2304, 121.4737),
    ("重庆", 29.5630, 106.5516),
    ("石家庄", 38.0428, 114.5149),
    ("太原", 37.8706, 112.5489),
    ("呼和浩特", 40.8426, 111.7492),
    ("沈阳", 41.8057, 123.4315),
    ("长春", 43.8171, 125.3235),
    ("哈尔滨", 45.8038, 126.5349),
    ("南京", 32.0603, 118.7969),
    ("杭州", 30.2741, 120.1551),
    ("合肥", 31.8206, 117.2272),
    ("福州", 26.0745, 119.2965),
    ("南昌", 28.6820, 115.8579),
    ("济南", 36.6512, 117.1201),
    ("郑州", 34.7466, 113.6254),
    ("武汉", 30.5928, 114.3055),
    ("长沙", 28.2282, 112.9388),
    ("广州", 23.1291, 113.2644),
    ("南宁", 22.8170, 108.3669),
    ("海口", 20.0440, 110.1999),
    ("成都", 30.5728, 104.0668),
    ("贵阳", 26.6470, 106.6302),
    ("昆明", 25.0389, 102.7183),
    ("拉萨", 29.6500, 91.1000),
    ("西安", 34.3416, 108.9398),
    ("兰州", 36.0611, 103.8343),
    ("西宁", 36.6171, 101.7782),
    ("银川", 38.4872, 106.2309),
    ("乌鲁木齐", 43.8256, 87.6168),
]
_MAJOR_COORDINATES = {
    name: (latitude, longitude) for name, latitude, longitude in MAJOR_CHINESE_CITIES
}

#: 单次搜索返回的结果上限
SEARCH_LIMIT = 60
#: 单个省份列表返回的结果上限
PROVINCE_CITY_LIMIT = 300
#: 「按名字找城市」结果的缓存条目上限
_NAME_CACHE_LIMIT = 256
#: 离线生成的拼音表，见 tools/build_city_pinyin.py
PINYIN_TABLE = DATA_DIR / "city_pinyin.json"

#: 取一行的列：长名称、短名称、编号、省下标、排序权重
_COLUMNS = "name, short_name, city_num, province_id, _id"

_pinyin_tables: dict[str, dict[str, str]] = {}
_pinyin_tables_lock = threading.Lock()
_converter: Any = None
_converter_lock = threading.Lock()


def _pinyin_table(database: str) -> dict[str, str]:
    """读离线生成的拼音表（城市名 -> 空格分隔的音节）。

    运行时用 pypinyin 现算要 ~300ms 的纯 Python 计算，会把 GIL 占住，GUI 线程
    跟着卡一下；表在构建期算好，这里只剩一次 json 解析。
    """
    with _pinyin_tables_lock:
        table = _pinyin_tables.get(database)
        if table is None:
            try:
                tables = json.loads(PINYIN_TABLE.read_text(encoding="utf-8"))
                table = tables.get(database) or {}
            except (OSError, ValueError) as error:
                logger.warning(
                    f"拼音表不可用（{PINYIN_TABLE.name}: {error}），改用 pypinyin 现算"
                )
                table = {}
            _pinyin_tables[database] = table
    return table


def _pinyin_engine() -> Any:
    """兜底用的 pypinyin 转换器，只有拼音表缺名字时才会真的导入它。

    ``import pypinyin`` 要执行两份大词典，~190ms 的 C 层 dict 构造，抢 GIL 很凶，
    所以它既不能出现在插件导入路径上，也不该出现在用户正在输入的时候。
    """
    global _converter
    if _converter is None:
        with _converter_lock:
            if _converter is None:
                from pypinyin.core import Pinyin

                _converter = Pinyin()
    return _converter


def _pinyin_syllables(name: str) -> list[str]:
    return list(_pinyin_engine().lazy_pinyin(_plain(name)))


def coordinates_for_city(name: str) -> tuple[float, float] | None:
    return _MAJOR_COORDINATES.get(_plain(str(name or "")))


def built_in_cities() -> list[dict[str, Any]]:
    """坐标型数据源的内置城市（直辖市 + 省会）。

    这里**不能**算拼音：坐标型数据源的城市名来自在线搜索，本地这份只是
    「空搜索时的推荐列表」，而 pypinyin 的导入要 ~190ms 的 GIL 时间。
    """
    return [
        {
            "name": name,
            "short_name": name,
            "code": "",
            "pinyin": "",
            "pinyin_abbr": "",
            "mode": "coordinates",
            "latitude": latitude,
            "longitude": longitude,
        }
        for name, latitude, longitude in MAJOR_CHINESE_CITIES
    ]


def _plain(text: str) -> str:
    """去掉分隔符，只留汉字：``北京 海淀`` -> ``北京海淀``。"""
    return str(text or "").replace(" ", "").replace(".", "")


def _coordinates_of(name: str) -> dict[str, float]:
    coordinates = _MAJOR_COORDINATES.get(_plain(name))
    return (
        {"latitude": coordinates[0], "longitude": coordinates[1]} if coordinates else {}
    )


class _CityIndex:
    """单个城市库的内存索引，建一次、多次复用。

    ``_lock`` 是可重入的：建索引的临界区里还会调用 :meth:`_ensure_rows`，
    而建索引本身是「检查缓存 → 构建 → 写回」，不可重入锁会死锁。
    """

    def __init__(self, path: Any, database: str):
        self._path = path
        self._database = database
        self._lock = threading.RLock()
        self._provinces: list[str] | None = None
        self._rows: list[dict[str, Any]] | None = None
        self._searchable: list[dict[str, Any]] | None = None
        self._by_name: dict[str, dict[str, Any]] = {}
        self._by_code: dict[str, dict[str, Any]] = {}
        self._row_lookup: dict[str, dict[str, Any]] = {}
        self._name_cache: dict[tuple[str, int], list[dict[str, Any]]] = {}

    @property
    def available(self) -> bool:
        return self._path.exists()

    def _query(
        self, sql: str, parameters: tuple[Any, ...] = ()
    ) -> list[tuple[Any, ...]]:
        connection = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
        try:
            with connection:
                return connection.execute(sql, parameters).fetchall()
        finally:
            connection.close()

    # ------------------------------------------------------------ 轻量查询

    def provinces(self) -> list[str]:
        with self._lock:
            if self._provinces is None:
                self._provinces = [
                    row[0]
                    for row in self._query("SELECT name FROM provinces ORDER BY _id")
                ]
            return self._provinces

    def name_for_code(self, code: str) -> str:
        return str(self.city_for_code(code).get("name") or "")

    def city_for_code(self, code: str) -> dict[str, Any]:
        """按编号取一行（长名称 + 短名称）。

        全库索引还没建时只查这一行：把整张表读成 dict 比一次全表扫描还贵
        （amap 的城市库有三千多行，几毫秒的差别在主线程上是能感觉到的）。
        """
        if not code:
            return {}
        with self._lock:
            if self._rows is not None:
                row = self._by_code.get(str(code))
                return dict(row) if row else {}
            cached = self._row_lookup.get(str(code))
        if cached is not None:
            return dict(cached)
        rows = self._query(
            f"SELECT {_COLUMNS} FROM citys WHERE city_num = ? LIMIT 1", (str(code),)
        )
        city = _row_to_city(*rows[0]) if rows else {}
        with self._lock:
            if len(self._row_lookup) >= _NAME_CACHE_LIMIT:
                self._row_lookup.clear()
            self._row_lookup[str(code)] = dict(city)
        return city

    def find_by_name(self, term: str, limit: int = 1) -> list[dict[str, Any]]:
        """按汉字名模糊匹配（只用汉字名，不碰拼音表）。

        数据源切换时 / 每次刷新都要给已有城市找编号，会在主线程上调用，所以这里
        必须保持毫秒级：全库索引已经建好就直接在内存里过滤，否则退回一次 SQL。
        """
        term = _strip_region(_plain(term))
        if not term:
            return []
        key = (term, int(limit))
        with self._lock:
            cached = self._name_cache.get(key)
            rows = self._rows
        if cached is not None:
            return [dict(row) for row in cached]
        if rows is None:
            matched = [
                _row_to_city(*row)
                for row in self._query(
                    f"SELECT {_COLUMNS} FROM citys WHERE name LIKE ? OR short_name LIKE ?"
                    " ORDER BY LENGTH(name), _id LIMIT ?",
                    (f"%{term}%", f"%{term}%", int(limit)),
                )
            ]
        else:
            matched = [row for row in rows if _name_hit(row, term)]
            matched.sort(
                key=lambda row: (_name_rank(row, term), len(row["name"]), row["order"])
            )
            matched = [dict(row) for row in matched[:limit]]
        with self._lock:
            if len(self._name_cache) >= _NAME_CACHE_LIMIT:
                self._name_cache.clear()
            self._name_cache[key] = [dict(row) for row in matched]
        return matched

    def popular(self, limit: int = 31) -> list[dict[str, Any]]:
        """热门城市：拿内置直辖市 / 省会的名字去库里换编号。"""
        self._ensure_rows()  # 副作用：建 _by_name 索引
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for name, latitude, longitude in MAJOR_CHINESE_CITIES[:limit]:
            row = self._by_name.get(name)
            if row is None:
                matches = self.find_by_name(name, limit=1)
                row = matches[0] if matches else None
            if row is not None:
                if row["code"] in seen:
                    continue
                seen.add(row["code"])
                result.append(dict(row))
            else:
                # 库里没有这个名字时仍然展示，方便只认坐标的数据源
                result.append(
                    {
                        "name": name,
                        "short_name": name,
                        "code": "",
                        "pinyin": "",
                        "pinyin_abbr": "",
                        "latitude": latitude,
                        "longitude": longitude,
                    }
                )
        return result

    # ------------------------------------------------------------ 重量查询（工作线程）

    def _ensure_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            if self._rows is not None:
                return self._rows
            rows = _build_index_rows(
                self._query(f"SELECT {_COLUMNS} FROM citys ORDER BY province_id, _id")
            )
            by_name: dict[str, dict[str, Any]] = {}
            by_code: dict[str, dict[str, Any]] = {}
            for row in rows:
                by_name.setdefault(row["name"], row)
                by_code.setdefault(row["code"], row)
            self._rows = rows
            self._by_name = by_name
            self._by_code = by_code
            return rows

    def searchable(self) -> list[dict[str, Any]]:
        """带拼音的全量索引；第一次调用要读一次拼音表。

        长名称和短名称各有一套拼音：列表里显示长名称，但「海淀区」这种只带短名的
        关键词、以及 ``hd`` 这样的首字母缩写，得靠短名称的拼音才命中。
        """
        with self._lock:
            if self._searchable is None:
                table = _pinyin_table(self._database)
                searchable: list[dict[str, Any]] = []
                for row in self._ensure_rows():
                    searchable.append(
                        row
                        | {
                            **_pinyin_fields(row["name"], table),
                            **{
                                f"short_{key}": value
                                for key, value in _pinyin_fields(
                                    row["short_name"], table
                                ).items()
                            },
                        }
                    )
                self._searchable = searchable
            return self._searchable

    def warm_up(self) -> None:
        """提前把全库索引 + 拼音表准备好。**只允许在工作线程调用。**"""
        self.searchable()

    def cities(
        self, province_index: int, limit: int = PROVINCE_CITY_LIMIT
    ) -> list[dict[str, Any]]:
        """``provinces._id`` 从 1 开始，所以这里用 ``cities.province_id``。

        ``province_index < 0`` 返回热门城市而不是全库几千条——设置页拿到的是
        QJSValue，全量列表既慢又没法看。
        """
        if province_index < 0:
            return self.popular()
        rows = self._ensure_rows()
        return [dict(row) for row in rows if row["province_id"] == province_index][
            :limit
        ]

    def search(
        self,
        term: str,
        limit: int = SEARCH_LIMIT,
        province_index: int | None = None,
    ) -> list[dict[str, Any]]:
        """汉字 / 全拼 / 首字母三种匹配，跨省（可限定省份）。"""
        term = _strip_region(_plain(term))
        if not term:
            return []
        needle = term.lower()
        # 只有拼音 / 首字母查询才需要拼音表，纯汉字查询直接用普通索引。
        rows = self.searchable() if _is_pinyin_query(needle) else self._ensure_rows()
        if province_index is not None and province_index >= 0:
            rows = [row for row in rows if row["province_id"] == province_index]

        matched: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
        for row in rows:
            rank = _match_rank(row, needle)
            if rank is not None:
                # 同分时名字短的先来（武汉 ≺ 武汉黄陂，xian 命中西安 ≺ 咸阳乾县）
                matched.append(((rank, len(row["name"]), row["name"]), row))
        matched.sort(key=lambda item: item[0])
        return [dict(row) for _, row in matched[:limit]]


_INDEXES: dict[str, _CityIndex] = {}
_INDEX_LOCK = threading.Lock()


def index_for(database: str) -> _CityIndex:
    path = DATA_DIR / database
    key = str(path)
    with _INDEX_LOCK:
        index = _INDEXES.get(key)
        if index is None:
            index = _CityIndex(path, database)
            _INDEXES[key] = index
    return index


def _strip_region(term: str) -> str:
    """去掉末尾的行政后缀：``武汉市`` -> ``武汉``、``海淀区`` -> ``海淀``。"""
    text = str(term or "").strip()
    while len(text) > 2 and text[-1] in ("市", "区", "县"):
        text = text[:-1]
    return text


def _build_index_rows(
    rows: list[tuple[str, str, str, int, int]],
) -> list[dict[str, Any]]:
    """把 SQL 行变成内存索引；拼音留给 :meth:`_CityIndex.searchable` 补齐。"""
    return [_row_to_city(*row) for row in rows]


def _row_to_city(
    name: str, short_name: str, code: str, province_id: int, order: int
) -> dict[str, Any]:
    """一行城市记录。``name`` 是长名称（列表用），``short_name`` 是短名称（小组件用）。"""
    name = str(name)
    return {
        "name": name,
        "short_name": str(short_name or name),
        "code": str(code),
        "province_id": int(province_id),
        "order": int(order),
        **_coordinates_of(name),
    }


def _pinyin_fields(name: str, table: dict[str, str]) -> dict[str, str]:
    """一个名字的全拼 / 首字母，以及带音节边界的搜索串。"""
    syllables = table.get(name)
    if syllables is None:
        syllables = " ".join(_pinyin_syllables(name))
    parts = syllables.split()
    return {
        "pinyin": "".join(parts),
        "pinyin_abbr": "".join(part[:1] for part in parts),
        # 音节之间留空格：子串匹配必须落在音节边界上，否则
        # 「hangzhou」会命中「changzhou」。
        "pinyin_parts": " " + " ".join(parts),
        "abbr_parts": " " + " ".join(part[:1] for part in parts),
    }


def _is_pinyin_query(needle: str) -> bool:
    """查询里有没有需要靠拼音才能匹配的部分。"""
    return any(character.isascii() and character.isalpha() for character in needle)


def _name_hit(row: dict[str, Any], term: str) -> bool:
    return term in row["name"] or term in row["short_name"]


def _name_rank(row: dict[str, Any], term: str) -> int:
    """按名字找编号时的优先级：长名称全等 > 短名称全等 > 长名称前缀 > 其它。"""
    if row["name"] == term:
        return 0
    if row["short_name"] == term:
        return 1
    if row["name"].startswith(term):
        return 2
    return 3


def _match_rank(row: dict[str, Any], needle: str) -> int | None:
    """命中返回排序权重（越小越靠前），不命中返回 ``None``。

    同一档匹配质量下长名称优先于短名称（「海淀区」和「北京海淀」都能搜到海淀）；
    拼音子串一律要求落在音节边界上，否则「changzhou」（常州）会被「hangzhou」命中。
    索引没建拼音时（纯汉字查询）只按名字匹配。
    """
    name, short = row["name"], row["short_name"]
    if needle == name:
        return 0
    if needle == short:
        return 1
    if name.startswith(needle):
        return 2
    if needle in name:
        return 3
    if short.startswith(needle):
        return 4
    if needle in short:
        return 5
    if not row.get("pinyin"):
        return None
    # 按「匹配质量」分档，同一档里长名称优先：``hdq`` 命中的是「海淀区」而不是海东市
    for step in range(5):
        for key, base in (("", 6), ("short_", 7)):
            if _pinyin_step(row, needle, key, step):
                return base + step
    return None


def _pinyin_step(row: dict[str, Any], needle: str, key: str, step: int) -> bool:
    """一套名字（长名称或短名称）的第 ``step`` 档拼音匹配：全等 -> 前缀 -> 音节前缀 -> 音节子串。"""
    abbreviation = row.get(f"{key}pinyin_abbr") or ""
    if not abbreviation:
        return False
    if step == 0:
        return abbreviation == needle
    if step == 1:
        return abbreviation.startswith(needle)
    if step == 2:
        return row[f"{key}pinyin"].startswith(needle)
    if step == 3:
        return f" {needle}" in row[f"{key}pinyin_parts"]
    return needle in row[f"{key}abbr_parts"]


class CityRepository:
    """只读访问城市表，``citys(_id, province_id, name, city_num)``。"""

    def __init__(self, database: str):
        self.database = database
        self._index = index_for(database)

    @property
    def available(self) -> bool:
        return self._index.available

    def provinces(self) -> list[str]:
        if not self._index.available:
            return []
        return self._index.provinces()

    def cities(
        self, province_index: int, limit: int = PROVINCE_CITY_LIMIT
    ) -> list[dict[str, str]]:
        """``provinces._id`` 从 1 开始，``citys.province_id`` 从 0 开始。

        ``province_index < 0`` 时返回热门城市。**会建内存索引，只在工作线程调用。**
        """
        if not self._index.available:
            return []
        return self._index.cities(province_index, limit)

    def popular(self) -> list[dict[str, str]]:
        if not self._index.available:
            return []
        return self._index.popular()

    def warm_up(self) -> None:
        """后台预热全库索引 + 拼音表。**只允许在工作线程调用。**"""
        if not self._index.available:
            return
        self._index.warm_up()

    def search(
        self,
        term: str,
        limit: int = SEARCH_LIMIT,
        province_index: int | None = None,
    ) -> list[dict[str, str]]:
        """汉字 / 全拼 / 首字母匹配。**会建内存索引，只在工作线程调用。**"""
        if not self._index.available:
            return []
        return self._index.search(term, limit, province_index)

    def search_by_name(self, term: str, limit: int = 1) -> list[dict[str, str]]:
        """只按汉字名匹配，不建索引，主线程可安全调用。"""
        if not self._index.available:
            return []
        return self._index.find_by_name(term, limit)

    def name_for_code(self, code: str) -> str:
        if not code or not self._index.available:
            return ""
        return self._index.name_for_code(code)

    def city_for_code(self, code: str) -> dict[str, Any]:
        """按编号取一行城市记录（含长名称与短名称）。"""
        if not code or not self._index.available:
            return {}
        return self._index.city_for_code(code)
