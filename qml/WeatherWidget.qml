import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI
import ClassWidgets.Theme
import "components"

/*!
    天气小组件。

    屏幕优先级（见 Figma node 25:579 / 427:240）：
      · 主体（天气图标 + 温度）优先级最高，停留时间最长；
        此时降水预报 / 天气预警只以右上角摘要图标出现。
      · 降水预报、天气预警各自是一块独立屏幕，优先级较低、停留时间较短。
      · 两级信息同时存在时轮播。
*/
Widget {
    id: root

    // ------------------------------------------------------------------ 数据
    readonly property var payload: backend ? backend.data : null
    readonly property string selectedCityId: settings && settings.city_id ? String(settings.city_id) : ""
    property int backendRevision: 0
    readonly property var selectedPayload: {
        backendRevision
        return backend && selectedCityId ? backend.dataForCity(selectedCityId) : payload
    }

    Connections {
        target: backend
        function onDataChanged() { root.backendRevision++ }
        function onConfigChanged() { root.backendRevision++ }
    }
    readonly property bool available: !!selectedPayload && selectedPayload.available === true
    readonly property var alertInfo: (selectedPayload && selectedPayload.alert) || ({ "active": false })
    readonly property var precipInfo: (selectedPayload && selectedPayload.precipitation) || ({ "active": false })
    readonly property var hourly: (selectedPayload && selectedPayload.hourly) || []

    readonly property string contentMode: settings && settings.content_mode ? settings.content_mode : "current"
    readonly property bool carousel: !settings || settings.carousel !== false
    readonly property int mainSeconds: settings && settings.main_seconds ? settings.main_seconds : 12
    readonly property int detailSeconds: settings && settings.detail_seconds ? settings.detail_seconds : 6

    // ------------------------------------------------------------------ 到点提醒
    // 室外状态 / 明日天气两种提醒各自配置每日触发时刻（HH:MM 数组）。
    // 到点后接管屏幕，时长由 notice_seconds 控制（默认 30 秒）；
    // 同一时刻只触发一次，多组撞车时排队播放。
    readonly property int noticeSeconds: {
        var value = settings && settings.notice_seconds ? Number(settings.notice_seconds) : 30
        if (isNaN(value))
            value = 30
        return Math.max(5, Math.min(180, Math.round(value)))
    }
    readonly property var outdoorNoticeTimes: parseNoticeTimes(settings && settings.outdoor_notice_times)
    readonly property var tomorrowNoticeTimes: parseNoticeTimes(settings && settings.tomorrow_notice_times)
    property string outdoorNoticeFired: ""
    property string tomorrowNoticeFired: ""
    property var noticeQueue: []
    property string noticeKind: ""
    property var noticeData: ({})
    readonly property bool noticeActive: noticeKind !== ""

    function pad2(value) {
        return value < 10 ? "0" + value : String(value)
    }

    /*! 解析时间配置：新配置是 ["07:30", "12:10"]，也兼容旧的逗号分隔字符串。 */
    function parseNoticeTimes(raw) {
        var result = []
        if (!raw)
            return result
        var parts = typeof raw === "string"
                ? String(raw).split(/[,，、;；\s]+/)
                : raw
        if (typeof parts.length === "undefined")
            return result
        for (var i = 0; i < parts.length; i++) {
            var text = String(parts[i]).trim().replace(/：/g, ":")
            var match = /^([01]?\d|2[0-3]):([0-5]\d)$/.exec(text)
            if (match) {
                var hour = match[1].length < 2 ? "0" + match[1] : match[1]
                result.push(hour + ":" + match[2])
            }
        }
        return result
    }

    function minuteStamp(now) {
        return now.getFullYear() + "-" + pad2(now.getMonth() + 1) + "-" + pad2(now.getDate())
                + " " + pad2(now.getHours()) + ":" + pad2(now.getMinutes())
    }

    function checkNotices() {
        var now = new Date()
        var hhmm = pad2(now.getHours()) + ":" + pad2(now.getMinutes())
        var stamp = minuteStamp(now)
        var data = selectedPayload
        if (!data || data.available !== true)
            return

        // 只有真正播出才记下这一分钟：应用启动 / 刷新恰好卡在触发点时，
        // 等天气数据回来还能补上这次提醒。
        if (outdoorNoticeTimes.indexOf(hhmm) >= 0 && outdoorNoticeFired !== stamp) {
            if (data.outdoorText) {
                outdoorNoticeFired = stamp
                enqueueNotice({
                    "kind": "outdoor",
                    "text": String(data.outdoorText),
                    "iconPath": data.iconPath || "",
                    "iconScale": data.iconScale || 1,
                    "glowColor": data.glowColor || "#F8AF18",
                    "glowAlpha": data.glowAlpha || 0.15
                })
            }
        }

        if (tomorrowNoticeTimes.indexOf(hhmm) >= 0 && tomorrowNoticeFired !== stamp) {
            var tomorrow = data.tomorrow
            if (tomorrow && tomorrow.available === true && tomorrow.text) {
                tomorrowNoticeFired = stamp
                enqueueNotice({
                    "kind": "tomorrow",
                    "text": String(tomorrow.text),
                    "iconPath": tomorrow.iconPath || "",
                    "iconScale": tomorrow.iconScale || 1,
                    "glowColor": tomorrow.glowColor || "#F8AF18",
                    "glowAlpha": tomorrow.glowAlpha || 0.15
                })
            }
        }
    }

    function enqueueNotice(notice) {
        noticeQueue = noticeQueue.concat([notice])
        if (!noticeActive)
            showNextNotice()
    }

    function showNextNotice() {
        if (noticeQueue.length === 0) {
            noticeKind = ""
            noticeData = ({})
            return
        }
        var queue = noticeQueue.slice()
        var next = queue.shift()
        noticeQueue = queue
        noticeKind = next.kind
        noticeData = next
        noticeTimer.restart()
    }

    // ------------------------------------------------------------------ 屏幕
    property var screens: ["main"]
    property int screenIndex: 0
    property bool summaryFlip: false

    readonly property string screen: noticeActive
        ? "notice"
        : (screens[Math.min(screenIndex, screens.length - 1)] || "main")
    readonly property bool hasAlert: alertInfo.active === true
    readonly property bool hasPrecipitation: precipInfo.active === true
    /*! 主体屏幕上是否要显示摘要（右上角小图标 + 交替标题） */
    readonly property bool hasSummary: available && !noticeActive && (hasAlert || hasPrecipitation)
    readonly property var summarySource: hasAlert ? alertInfo : precipInfo

    function rebuildScreens() {
        var list = []
        if (!available) {
            list = ["unavailable"]
        } else {
            list.push("main")
            if (settings && settings.show_hourly && hourly.length >= 3) {
                list.push("hourly")
            }
            if (carousel) {
                // 直接从 payload 读取，避免依赖属性绑定的更新时序
                var currentAlertInfo = (selectedPayload && selectedPayload.alert) || ({ "active": false })
                var currentPrecipInfo = (selectedPayload && selectedPayload.precipitation) || ({ "active": false })
                if (currentAlertInfo.active === true) list.push("alert")
                if (currentPrecipInfo.active === true) list.push("precip")
            }
        }
        screens = list
        if (screenIndex >= list.length) screenIndex = 0
    }

    function dwell(name) {
        return (name === "alert" || name === "precip" ? detailSeconds : mainSeconds) * 1000
    }

    onPayloadChanged: rebuildScreens()
    onSelectedPayloadChanged: rebuildScreens()
    onSettingsChanged: rebuildScreens()
    Component.onCompleted: rebuildScreens()

    Timer {
        id: rotator
        running: root.screens.length > 1 && !root.noticeActive
        repeat: true
        interval: root.dwell(root.screen)
        onTriggered: root.screenIndex = (root.screenIndex + 1) % root.screens.length
    }

    Timer {
        id: summaryRotator
        running: root.hasSummary && root.screen === "main"
        repeat: true
        interval: 4000
        onTriggered: root.summaryFlip = !root.summaryFlip
        onRunningChanged: if (!running) root.summaryFlip = false
    }

    // 到点提醒：每秒对一次本地时间，命中配置里的 HH:MM 就播放一次。
    Timer {
        id: noticeClock
        running: root.outdoorNoticeTimes.length > 0 || root.tomorrowNoticeTimes.length > 0
        repeat: true
        interval: 1000
        triggeredOnStart: true
        onTriggered: root.checkNotices()
    }

    // 单条提醒停留 noticeSeconds 秒，结束后播放队列里的下一条。
    Timer {
        id: noticeTimer
        interval: root.noticeSeconds * 1000
        repeat: false
        onTriggered: root.showNextNotice()
    }

    // ------------------------------------------------------------------ 外观
    readonly property int iconSize: miniMode ? 28 : 38
    readonly property color glowColor: {
        switch (screen) {
        case "notice": return noticeData.glowColor || "#F8AF18"
        case "alert": return alertInfo.glowColor || "#EF4444"
        case "precip": return precipInfo.glowColor || "#0A5AD4"
        default: return (selectedPayload && selectedPayload.glowColor) || "#F8AF18"
        }
    }
    readonly property real glowAlpha: {
        switch (screen) {
        case "notice": return noticeData.glowAlpha || 0.15
        case "alert": return alertInfo.glowAlpha || 0.15
        case "precip": return precipInfo.glowAlpha || 0.15
        default: return (selectedPayload && selectedPayload.glowAlpha) || 0.15
        }
    }

    text: {
        switch (screen) {
        case "notice":
            return noticeKind === "tomorrow" ? qsTr("明日天气") : qsTr("当前天气")
        case "unavailable":
            return (selectedPayload && selectedPayload.city) || qsTr("天气")
        case "hourly":
            return qsTr("未来 3 小时")
        case "alert":
            return alertInfo.title || qsTr("天气预警")
        case "precip":
            return precipInfo.title || qsTr("降水预报")
        default:
            if (hasSummary && summaryFlip) {
                return summarySource.title || ""
            }
            var description = (selectedPayload && selectedPayload.description) || ""
            var city = (selectedPayload && selectedPayload.city) || ""
            return [description, city].filter(function (part) { return !!part }).join(" ")
        }
    }

    // 设计稿的内边距是 32；基类的 implicitWidth 固定按 24 计算，这里一并覆盖
    padding: miniMode ? 16 : 32
    implicitWidth: Math.max(titleMetrics.width + 8 + actionsWidth, screenStack.width) + padding * 2

    readonly property real actionsWidth: 16 + (hasSummary && screen === "main" ? 8 + 20 : 0)

    TextMetrics {
        id: titleMetrics
        font.family: AppCentral.getQFont(Configs.data.preferences.font, Utils.fontFamily).family
        font.pixelSize: 16
        font.weight: Configs.data.preferences.font_weight || 600
        text: root.text
    }

    backgroundArea: GlowBackground {
        anchors.fill: parent
        glowColor: root.glowColor
        glowAlpha: root.glowAlpha
        cornerRadius: root.height * 0.22
    }

    actions: RowLayout {
        spacing: 8

        Icon {
            name: "ic_fluent_location_arrow_20_filled"
            size: 16
            opacity: 0.6
        }

        WeatherIcon {
            box: 20
            contentScale: root.summarySource.badgeIconScale || 1
            path: root.summarySource.badgeIconPath || ""
            visible: root.hasSummary && root.screen === "main"
        }
    }

    // ------------------------------------------------------------------ 内容
    Item {
        id: screenStack
        anchors.horizontalCenter: parent.horizontalCenter
        height: parent.height
        width: {
            switch (root.screen) {
            case "unavailable": return unavailableScreen.implicitWidth
            case "hourly": return hourlyScreen.implicitWidth
            case "alert": return alertScreen.implicitWidth
            case "precip": return precipScreen.implicitWidth
            case "notice": return noticeScreen.implicitWidth
            default: return mainScreen.implicitWidth
            }
        }

        Behavior on width {
            NumberAnimation { duration: 350; easing.type: Easing.OutQuint }
        }

        // 主体：天气图标 + 温度
        RowLayout {
            id: mainScreen
            anchors.centerIn: parent
            spacing: 12
            opacity: root.screen === "main" ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 250 } }

            WeatherIcon {
                box: root.iconSize
                contentScale: (root.selectedPayload && root.selectedPayload.iconScale) || 1
                path: (root.selectedPayload && root.selectedPayload.iconPath) || ""
            }

            Numeral {
                visible: root.contentMode !== "high_low"
                px: root.miniMode ? 24 : 36
                text: root.selectedPayload && root.selectedPayload.temperature ? root.selectedPayload.temperature + "°" : "--°"
            }

            ColumnLayout {
                visible: root.contentMode === "high_low"
                spacing: 0

                Numeral {
                    px: root.miniMode ? 14 : 18
                    text: "↑ " + ((root.selectedPayload && root.selectedPayload.temperatureHigh) || "--") + "°"
                }
                Numeral {
                    px: root.miniMode ? 14 : 18
                    opacity: 0.59
                    text: "↓ " + ((root.selectedPayload && root.selectedPayload.temperatureLow) || "--") + "°"
                }
            }
        }

        // 到点提醒：天气图标 + 一行文案，字号 / 字重与内置小组件标题一致
        RowLayout {
            id: noticeScreen
            objectName: "noticeScreen"
            anchors.centerIn: parent
            spacing: 12
            opacity: root.screen === "notice" ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 250 } }

            WeatherIcon {
                objectName: "noticeIcon"
                box: root.iconSize
                contentScale: root.noticeData.iconScale || 1
                path: root.noticeData.iconPath || ""
            }

            Title {
                objectName: "noticeText"
                text: root.noticeData.text || ""
            }
        }

        // 未来 3 小时
        RowLayout {
            id: hourlyScreen
            anchors.centerIn: parent
            spacing: 4
            opacity: root.screen === "hourly" ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 250 } }

            Repeater {
                model: root.hourly

                delegate: RowLayout {
                    spacing: 4

                    Icon {
                        name: "ic_fluent_chevron_right_20_regular"
                        size: 12
                        opacity: 0.5
                        visible: index > 0
                    }

                    ColumnLayout {
                        spacing: 3

                        WeatherIcon {
                            Layout.alignment: Qt.AlignHCenter
                            box: 24
                            contentScale: modelData.iconScale || 1
                            path: modelData.iconPath || ""
                        }
                        Numeral {
                            Layout.alignment: Qt.AlignHCenter
                            px: 14
                            text: (modelData.temperature || "--") + "°"
                        }
                    }
                }
            }
        }

        // 天气预警
        RowLayout {
            id: alertScreen
            anchors.centerIn: parent
            spacing: 12
            opacity: root.screen === "alert" ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 250 } }

            WeatherIcon {
                box: root.iconSize
                contentScale: root.alertInfo.iconScale || 1
                path: root.alertInfo.iconPath || ""
            }

            // 能从预警正文解析出指标时展示「N 小时内 / ≥50 毫米」
            Repeater {
                model: root.alertInfo.metrics || []

                delegate: RowLayout {
                    spacing: 3
                    Numeral {
                        px: root.miniMode ? 24 : 36
                        text: modelData.value || ""
                    }
                    Numeral {
                        px: root.miniMode ? 19 : 28
                        text: modelData.unit || ""
                    }
                }
            }

            // 解析不出指标时退回展示当前温度
            Numeral {
                visible: !root.alertInfo.metrics || root.alertInfo.metrics.length === 0
                px: root.miniMode ? 24 : 36
                text: root.selectedPayload && root.selectedPayload.temperature ? root.selectedPayload.temperature + "°" : "--°"
            }
        }

        // 降水预报
        RowLayout {
            id: precipScreen
            anchors.centerIn: parent
            spacing: 12
            opacity: root.screen === "precip" ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 250 } }

            WeatherIcon {
                box: root.iconSize
                contentScale: root.precipInfo.iconScale || 1
                path: root.precipInfo.iconPath || ""
            }

            RowLayout {
                spacing: 3
                Numeral {
                    px: root.miniMode ? 24 : 36
                    text: root.precipInfo.value || ""
                }
                Numeral {
                    px: root.miniMode ? 19 : 28
                    text: root.precipInfo.unit || ""
                }
            }
        }

        // 不可用
        RowLayout {
            id: unavailableScreen
            anchors.centerIn: parent
            spacing: 12
            opacity: root.screen === "unavailable" ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 250 } }

            WeatherIcon {
                box: root.iconSize
                contentScale: (root.selectedPayload && root.selectedPayload.iconScale) || 1
                path: (root.selectedPayload && root.selectedPayload.iconPath) || ""
            }

            Numeral {
                px: root.miniMode ? 19 : 28
                text: qsTr("不可用")
            }
        }
    }

    // 点击小组件立即刷新
    TapHandler {
        onTapped: if (root.backend) root.backend.refresh()
    }
}
