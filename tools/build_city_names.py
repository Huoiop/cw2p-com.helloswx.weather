"""重写 data/*.db 的城市名。

城市库来自 Class Widgets，原始数据只有一列叶子名（小米是「武汉.黄陂」，高德是
「黄陂区」），既看不出归属城市，也没法按要求显示。这里把每个城市算成两列：

* ``name``——**长名称**，「城市 + 区域」并省掉行政后缀：``北京海淀`` / ``北京`` /
  ``武汉黄陂`` / ``武汉``。给「添加城市」列表、小组件的选择城市列表和 CityClip 用；
* ``short_name``——**短名称**，只留最小区域、市 / 区 / 县一个都不省：``海淀区`` /
  ``北京市`` / ``黄陂区`` / ``武汉市``。只给小组件上显示。小米库的原始名不带后缀
  （``武汉`` / ``海淀``），按规范补成 ``武汉市`` / ``海淀区``，两个库显示得一致。

省份**不出现在名称里**，只用来做「添加城市」时的筛选——``provinces`` 表就是筛选器。

顺带修一个上游数据问题：高德库的 ``citys.province_id`` 用的是国标省份顺序，而
``provinces`` 表是 CW2 的展示顺序，两者对不上（选「上海」会列出天津的城市），
而且青浦区、奉贤区、崇明区这三个上海 districts 被归进了江苏。这里一律按 adcode
前两位重新归省，并按「市 → 区县」重排 ``_id``。

    .venv/bin/python tools/build_city_names.py

改完记得重新生成拼音表：``.venv/bin/python tools/build_city_pinyin.py``
"""

import sqlite3
from pathlib import Path
from typing import NamedTuple

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PLUGIN_ROOT / "data"

#: 长名称里要省掉的行政后缀（长后缀排前面，「自治县」要赶在「县」前面匹配）。
#: 注意没有「州」：通州、徐州、杭州这种地名会被砍掉尾巴，自治州由下面的民族名规则处理；
#: 也没有「旗」：「达茂联合旗」「陈旗」省掉旗之后就不是地名了。
SUFFIXES = (
    "特别行政区",
    "自治区",
    "自治县",
    "自治州",
    "自治旗",
    "联合旗",
    "地区",
    "林区",
    "省",
    "市",
    "县",
    "区",
    "盟",
)

#: 复合后缀：省掉最后的「区」会砍掉地名的一部分（浦东新区 -> 浦东新），整个留着。
#: 不能把「园区」「台区」加进来：下花园区、丰台区、九台区这些是真地名。
COMPOUND_SUFFIXES = ("新区", "矿区", "郊区", "景区")

#: 民族自治单位形如「<地名><若干民族名>自治州 / 县 / 旗」，长名称取民族名之前的那截。
#: 「族」放在最后兜底（「鄂温克族自治旗」里前面的民族名就顶格了）。
ETHNIC_GROUPS = (
    "乌孜别克",
    "柯尔克孜",
    "塔吉克",
    "俄罗斯",
    "鄂伦春",
    "达斡尔",
    "鄂温克",
    "维吾尔",
    "哈萨克",
    "土家",
    "布依",
    "朝鲜",
    "哈尼",
    "傈僳",
    "景颇",
    "拉祜",
    "德昂",
    "基诺",
    "毛南",
    "独龙",
    "纳西",
    "赫哲",
    "塔塔尔",
    "门巴",
    "珞巴",
    "仡佬",
    "撒拉",
    "东乡",
    "保安",
    "裕固",
    "锡伯",
    "蒙古",
    "回",
    "藏",
    "满",
    "苗",
    "彝",
    "壮",
    "瑶",
    "羌",
    "白",
    "傣",
    "佤",
    "京",
    "黎",
    "畲",
    "水",
    "侗",
    "怒",
    "阿昌",
    "普米",
    "布朗",
    "族",
)

#: 长的民族名要先试，否则「达斡尔族」会被单字民族名截断
ETHNIC_GROUPS = tuple(sorted(ETHNIC_GROUPS, key=len, reverse=True))

#: 省直辖的县级市（济源、仙桃、兵团师市……）挂在本省末尾，别插在城市中间
DIRECT_GROUP_OFFSET = 100000

#: 行政后缀。高德库的名字本来就带这些，小米库的名字是光秃秃的（``武汉`` / ``海淀``），
#: 短名称按规范补上：``武汉`` -> ``武汉市``、``海淀`` -> ``海淀区``
SUFFIXED = (
    "特别行政区",
    "自治区",
    "自治州",
    "自治县",
    "自治旗",
    "地区",
    "林区",
    "市",
    "区",
    "县",
    "盟",
    "旗",
    "州",
    "省",
)


class City(NamedTuple):
    """一条城市记录的两个名字。"""

    raw_name: str
    city_num: str
    short: str  # 最小区域，带后缀
    long: str  # 城市 + 区域，不带后缀


def shorten(name: str) -> str:
    """省掉行政后缀：``黄陂区`` -> ``黄陂``、``锡林郭勒盟`` -> ``锡林郭勒``。

    后两种情况原样留着：后缀前只剩一个字时不省（``邱县`` 得留 ``县``，不然长名称会
    变成没头没脑的「邱」），复合后缀也不省（``浦东新区`` 不能变成 ``浦东新``）。
    """
    if name.endswith(COMPOUND_SUFFIXES):
        return name
    for suffix in SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            return name[: -len(suffix)]
    return name


def shorten_autonomous(name: str) -> str:
    """带民族名的单位只取地名那一截：``白沙黎族自治县`` -> ``白沙``。

    从「自治」往前一格格地剥民族名（``陵水黎族自治县`` -> ``陵水``，``水`` 留住了，
    因为「陵水」的地名比「三都水族自治县」里的民族名长）。
    """
    if "自治" in name:
        cut = name.index("自治")
    elif name.endswith(("族区", "族县", "族市", "族镇", "族乡")):
        cut = name.rindex("族") + 1
    else:
        return name
    while cut > 0:
        for group in ETHNIC_GROUPS:
            # 剥掉后至少要剩下两字，否则会把地名本身也剥掉（鄂温克族自治旗）
            if cut - len(group) >= 2 and name[:cut].endswith(group):
                cut -= len(group)
                break
        else:
            break
    return name[:cut]


def with_suffix(name: str, suffix: str) -> str:
    """小米库的原始名不带行政后缀，短名称补上：``武汉`` -> ``武汉市``。"""
    return name if name.endswith(SUFFIXED) else f"{name}{suffix}"


def join(city: str, area: str, full: str) -> str:
    """长名称。

    两种情况不拼城市名：同名父子（黄山市 / 黄山区，否则会变成「黄山区黄山」），
    以及区县名里本来就带着城市名（黄山风景区，否则是「黄山黄山风景」）。
    """
    if not city:
        return area
    if city == area or area.startswith(city):
        return full
    return f"{city}{area}"


def build_amap(
    rows: list[tuple[int, str, str]], provinces: list[str]
) -> list[tuple[int, City, int]]:
    """高德：只有叶子名，靠 adcode 的形状还原层级。

    返回 ``(省下标, 城市, 排序键)``；排序键让区县紧跟在它所属的城市后面。
    """
    province_of_code: dict[str, int] = {}
    province_is_city: dict[
        str, bool
    ] = {}  # 省级行本身是不是「市」（直辖市、特别行政区）
    for _id, name, code in rows:
        code = str(code)
        if not code.endswith("0000"):
            continue
        province_is_city[code[:2]] = str(name).endswith(("市", "特别行政区"))
        for index, province in enumerate(provinces):
            # 省名是省级行全名的前缀：内蒙古 / 内蒙古自治区
            if str(name).startswith(province):
                province_of_code[code[:2]] = index
                break

    groups: dict[str, int] = {}  # adcode 前缀 -> 组序号（省级行 / 市级行）
    city_of: dict[str, str] = {}  # adcode 前缀 -> 所属城市的长名称前缀
    counter = 0
    cities: list[tuple[int, City, int]] = []
    for _id, name, code in rows:
        code, raw = str(code), str(name)
        prefix = code[:2]
        province = province_of_code.get(prefix)
        if province is None:
            raise SystemExit(f"{raw}（{code}）找不到所属省份")
        if code.endswith("0000"):
            counter += 1
            groups[prefix] = counter  # 直辖市的市就是省
            city_of[prefix] = shorten(shorten_autonomous(raw))
            cities.append((province, City(raw, code, raw, city_of[prefix]), counter))
        elif code.endswith("00"):
            counter += 1
            groups[code[:4]] = counter
            city_of[code[:4]] = shorten(shorten_autonomous(raw))
            cities.append((province, City(raw, code, raw, city_of[code[:4]]), counter))
        else:
            group = groups.get(code[:4])
            parent = city_of.get(code[:4])
            if parent is None:
                # 没有市级行可挂靠：直辖市 / 特别行政区就当成它自己的市（正常），
                # 省直辖的县级市只用自己的名字，并排到本省最后
                counter += 1
                group = (
                    counter
                    if province_is_city.get(prefix)
                    else counter + DIRECT_GROUP_OFFSET
                )
                parent = city_of.get(prefix, "") if province_is_city.get(prefix) else ""
            area_short = shorten(shorten_autonomous(raw))
            long_name = join(parent, area_short, raw)
            cities.append((province, City(raw, code, raw, long_name), group))
    return cities


def build_xiaomi(
    rows: list[tuple[int, str, str]], provinces: list[str]
) -> list[tuple[int, City, int]]:
    """小米：名字自带层级（``武汉`` / ``武汉.黄陂``）。"""
    cities: list[tuple[int, City, int]] = []
    for _id, name, code in rows:
        raw, code = str(name), str(code)
        # 编号形如 101200101：101 + 省份(20) + 市(01) + 区县(01)，省份序号与 provinces 表同序
        province = int(code[3:5]) - 1 if len(code) == 9 else -1
        if not 0 <= province < len(provinces):
            raise SystemExit(f"{raw}（{code}）找不到所属省份")
        if "." in raw:
            city_part, area_part = raw.split(".", 1)
            long_name = join(shorten(city_part), shorten(area_part), area_part)
            cities.append(
                (
                    province,
                    City(raw, code, with_suffix(area_part, "区"), long_name),
                    _id,
                )
            )
        else:
            short = with_suffix(raw, "市")
            cities.append((province, City(raw, code, short, shorten(raw)), _id))
    return cities


def rewrite(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        with connection:
            provinces = [
                row[0]
                for row in connection.execute("SELECT name FROM provinces ORDER BY _id")
            ]
            columns = {row[1] for row in connection.execute("PRAGMA table_info(citys)")}
            # raw_name 留着，重新跑本脚本时不再依赖已经被改写过的 name
            raw_column = "raw_name" if "raw_name" in columns else "name"
            rows = [
                (row[0], row[1], row[2])
                for row in connection.execute(
                    f"SELECT _id, {raw_column}, city_num FROM citys ORDER BY _id"
                )
            ]
            flavour = (
                build_xiaomi
                if any("." in str(name) for _id, name, _code in rows)
                else build_amap
            )
            cities = sorted(
                flavour(rows, provinces),
                key=lambda item: (item[0], item[2], item[1].city_num),
            )

            connection.execute("DROP TABLE citys")
            connection.execute(
                "CREATE TABLE citys ("
                "_id integer primary key autoincrement, "
                "province_id integer not null, "
                "raw_name text not null, "
                "name text not null, "
                "short_name text not null, "
                "city_num text)"
            )
            connection.executemany(
                "INSERT INTO citys (province_id, raw_name, name, short_name, city_num)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (province, city.raw_name, city.long, city.short, city.city_num)
                    for province, city, _ in cities
                ],
            )
        connection.execute("VACUUM")
    finally:
        connection.close()
    print(f"{database.name}: {len(cities)} 个城市")


def main() -> None:
    for database in sorted(DATA_DIR.glob("*.db")):
        rewrite(database)


if __name__ == "__main__":
    main()
