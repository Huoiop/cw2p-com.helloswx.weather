"""生成 data/city_pinyin.json。

城市库里的几千个中文名要按拼音 / 首字母搜索，就得先有拼音。运行时用 pypinyin
现算要导入词典 + 转换全库，~300ms，而且这段是纯 Python，会把 GIL 占住，
GUI 线程（连带 CW 自己的动画）会跟着卡一下。

城市库是随插件一起发布的静态文件，所以拼音表也离线算好一起发布：运行时只剩
一次 json 解析（几毫秒）。

长名称（``北京海淀``，列表里显示的）和短名称（``海淀区``，小组件上显示的）都收
进表里——短名称的首字母缩写（``hd`` -> ``海淀区``）只能靠它命中。

    .venv/bin/python tools/build_city_pinyin.py

先跑 tools/build_city_names.py 重写城市名，再跑本脚本。
"""

import json
import sqlite3
from pathlib import Path

from pypinyin.core import Pinyin

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PLUGIN_ROOT / "data"
OUTPUT = DATA_DIR / "city_pinyin.json"


def main() -> None:
    converter = Pinyin()
    tables: dict[str, dict[str, str]] = {}
    for database in sorted(DATA_DIR.glob("*.db")):
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            with connection:
                names = [
                    name
                    for row in connection.execute("SELECT name, short_name FROM citys")
                    for name in row
                ]
        finally:
            connection.close()
        tables[database.name] = {
            name: " ".join(converter.lazy_pinyin(name.replace(" ", "")))
            for name in names
        }
        print(f"{database.name}: {len(names)} 个名称")

    OUTPUT.write_text(
        json.dumps(tables, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"写入 {OUTPUT.relative_to(PLUGIN_ROOT)}（{OUTPUT.stat().st_size / 1024:.0f} KB）"
    )


if __name__ == "__main__":
    main()
