"""修复 rlottie 渲染不出来的图层。

rlottie（qt-lottie 的渲染后端）遇到「带关键帧的图层缩放」时会直接丢掉整个图层：
Meteocons 的 code-red / code-yellow 等预警图标，三角形本体正是靠 100% → 110%
的呼吸缩放做动效，于是只剩下中间那根感叹号。

这里把出问题的图层缩放拍平成第一帧的值——预警三角形本来在设计稿里就是静态的，
其余会动的元素（云、雨滴、太阳光芒）不受影响。

    .venv/bin/python tools/fix_lottie_scale.py
"""

import json
import os
import tempfile
from pathlib import Path

from rlottie_python import LottieAnimation

LOTTIE_DIR = Path(__file__).resolve().parent.parent / "assets" / "lottie"
PROBE_FRAMES = (0, 90, 180, 270)


def visible_area(path: str, frame: int) -> int:
    animation = LottieAnimation.from_file(path)
    alpha = (
        animation.render_pillow_frame(frame_num=frame, width=128, height=128)
        .convert("RGBA")
        .getchannel("A")
    )
    box = alpha.getbbox()
    return (box[2] - box[0]) * (box[3] - box[1]) if box else 0


def measure(data: dict) -> list:
    handle, name = tempfile.mkstemp(suffix=".json")
    os.close(handle)
    Path(name).write_text(json.dumps(data), encoding="utf-8")
    try:
        return [visible_area(name, frame) for frame in PROBE_FRAMES]
    finally:
        Path(name).unlink(missing_ok=True)


def flatten(data: dict) -> dict | None:
    """把所有带关键帧的图层缩放拍平；没有可拍平的就返回 None。"""
    patched = json.loads(json.dumps(data))
    changed = False
    for layer in patched.get("layers", []):
        scale = layer.get("ks", {}).get("s")
        if (
            isinstance(scale, dict)
            and scale.get("a") == 1
            and isinstance(scale.get("k"), list)
        ):
            first = scale["k"][0].get("s")
            if first:
                layer["ks"]["s"] = {"a": 0, "k": first}
                changed = True
    return patched if changed else None


def main() -> None:
    fixed = []
    for path in sorted(LOTTIE_DIR.glob("*.json")):
        if path.name == "metrics.json":
            continue
        original = json.loads(path.read_text(encoding="utf-8"))
        before = measure(original)
        # 第一帧能画出来、后续帧掉了一半以上 → 判定为图层被丢弃
        if not (before[0] > 0 and min(before[1:]) < before[0] * 0.5):
            continue

        patched = flatten(original)
        if patched is None:
            continue
        after = measure(patched)
        if min(after[1:]) > min(before[1:]) * 1.5:
            path.write_text(
                json.dumps(patched, separators=(",", ":")), encoding="utf-8"
            )
            fixed.append(path.stem)
            print(f"  修复 {path.stem:16s} 可见面积 {before} -> {after}")
        else:
            print(f"  跳过 {path.stem:16s} 拍平后无改善（应为正常动画）")

    print(f"\n共修复 {len(fixed)} 个图标: {fixed}")
    print("请接着重新生成 metrics： .venv/bin/python tools/build_icon_metrics.py")


if __name__ == "__main__":
    main()
