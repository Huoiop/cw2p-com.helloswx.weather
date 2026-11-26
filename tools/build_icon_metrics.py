"""生成 assets/lottie/metrics.json。

Meteocons 的每个 Lottie 在 128×128 画布里留白比例不一样（0.44 ~ 0.75），
直接按设计稿的 38px 摆放会让图标看起来明显偏小、而且各状态大小不一。
这里离线量出每个图标实际绘制内容的最大占比，运行时据此放大渲染框，
使不同天气图标的视觉尺寸一致。

    .venv/bin/python tools/build_icon_metrics.py
"""

import json
from pathlib import Path

from rlottie_python import LottieAnimation

LOTTIE_DIR = Path(__file__).resolve().parent.parent / "assets" / "lottie"
CANVAS = 128
SAMPLES = 16


def content_ratio(path: Path) -> float:
    """图标内容在画布中占的最大比例（取动画全程的最大包围盒）。"""
    animation = LottieAnimation.from_file(str(path))
    total = animation.lottie_animation_get_totalframe()
    widest = tallest = 0
    for index in range(SAMPLES):
        frame = round(index * (total - 1) / (SAMPLES - 1))
        alpha = (
            animation.render_pillow_frame(frame_num=frame, width=CANVAS, height=CANVAS)
            .convert("RGBA")
            .getchannel("A")
        )
        box = alpha.getbbox()
        if box:
            widest = max(widest, box[2] - box[0])
            tallest = max(tallest, box[3] - box[1])
    return max(widest, tallest) / CANVAS


def main() -> None:
    metrics = {}
    for path in sorted(LOTTIE_DIR.glob("*.json")):
        if path.name == "metrics.json":
            continue
        ratio = content_ratio(path)
        metrics[path.stem] = round(ratio, 4)
        print(f"{path.stem:28s} {ratio:.4f}")

    target = LOTTIE_DIR / "metrics.json"
    target.write_text(json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\n已写入 {target} （{len(metrics)} 个图标）")


if __name__ == "__main__":
    main()
