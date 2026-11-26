"""隔离测试 qt-lottie 的播放行为。

.venv/bin/python tools/lottie_probe.py
"""

import sys
import time
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from qtlottie import init_qml

ROOT = Path(__file__).resolve().parent.parent


def uri(name: str) -> str:
    return (ROOT / "assets" / "lottie" / f"{name}.json").resolve().as_uri()


PROBE_URI = uri("code-yellow")

QML = f"""
import QtQuick
import QtQuick.Window
import QtLottie 1.0

Window {{
    width: 120; height: 120; visible: true
    LottieAnimation {{
        objectName: "anim"
        anchors.fill: parent
        loops: -1
        autoPlay: true
        source: "{PROBE_URI}"
        playing: true
    }}
}}
"""

app = QGuiApplication(sys.argv)
init_qml()
engine = QQmlApplicationEngine()
engine.loadData(QML.encode())
anim = engine.rootObjects()[0].findChild(object, "anim")

log = []
start = time.monotonic()


def sample():
    log.append((round(time.monotonic() - start, 2), anim.property("currentFrame")))


QTimer(app, interval=100, timeout=sample).start()
# 中途换图标，验证换 source 之后还会不会继续播放
QTimer.singleShot(1500, lambda: anim.setProperty("source", uri("clear-day")))
QTimer.singleShot(3200, app.quit)
app.exec()

before = [f for t, f in log if t < 1.4]
after = [f for t, f in log if t > 1.7]
span = 1.4 - log[0][0] if log else 1
print("总帧数 361 / 6s → 期望 60fps")
print(
    f"换 source 前：帧号 {before[0]} → {before[-1]}，约 {(before[-1] - before[0]) / span:.0f} fps"
)
print(
    f"换 source 后：帧号 {after[0]} → {after[-1]}  ({'继续播放' if after[-1] > after[0] else '停住了'})"
)
