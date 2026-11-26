import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI
import ClassWidgets.Plugins
import "components"

SettingsLayout {
    id: layout

    // WidgetSettings is loaded inside the widgets window, whose context does
    // not expose PluginBackendBridge. Read the persisted plugin model through
    // the standard CW2 Configs context instead.
    property var backend: null
    ListModel { id: cityModel }
    ListModel { id: outdoorTimesModel }
    ListModel { id: tomorrowTimesModel }

    Component.onCompleted: {
        reloadCities()
        loadNoticeTimes(outdoorTimesModel, "outdoor_notice_times")
        loadNoticeTimes(tomorrowTimesModel, "tomorrow_notice_times")
    }

    // 对话框通过 Loader.setSource 注入 settings，个别时序下可能晚于
    // Component.onCompleted；这里再兜底加载一次。
    onSettingsChanged: {
        loadNoticeTimes(outdoorTimesModel, "outdoor_notice_times")
        loadNoticeTimes(tomorrowTimesModel, "tomorrow_notice_times")
    }

    /*! 兼容旧配置里的逗号分隔字符串，统一成去重、排序后的 HH:MM 数组。 */
    function normalizeNoticeTimes(raw) {
        var result = []
        if (!raw)
            return result
        var items = typeof raw === "string"
                ? String(raw).split(/[,，、;；\s]+/)
                : raw
        if (typeof items.length === "undefined")
            return result
        for (var i = 0; i < items.length; i++) {
            var text = String(items[i]).trim().replace(/：/g, ":")
            var match = /^([01]?\d|2[0-3]):([0-5]\d)$/.exec(text)
            if (!match)
                continue
            var hour = match[1].length < 2 ? "0" + match[1] : match[1]
            var value = hour + ":" + match[2]
            if (result.indexOf(value) < 0)
                result.push(value)
        }
        result.sort()
        return result
    }

    function loadNoticeTimes(model, key) {
        model.clear()
        var values = normalizeNoticeTimes(settings ? settings[key] : null)
        for (var i = 0; i < values.length; i++)
            model.append({"time": values[i]})
    }

    function noticeTimesFromModel(model) {
        var values = []
        for (var i = 0; i < model.count; i++)
            values.push(model.get(i).time)
        values.sort()
        return values
    }

    function commitNoticeTimes(model, key) {
        if (settings)
            settings[key] = noticeTimesFromModel(model)
    }

    function addNoticeTime(model, key, value) {
        var picked = normalizeNoticeTimes([value])
        if (picked.length === 0)
            return
        var values = noticeTimesFromModel(model)
        if (values.indexOf(picked[0]) >= 0)
            return
        values.push(picked[0])
        values.sort()
        model.clear()
        for (var i = 0; i < values.length; i++)
            model.append({"time": values[i]})
        commitNoticeTimes(model, key)
    }

    function removeNoticeTime(model, key, index) {
        if (index < 0 || index >= model.count)
            return
        model.remove(index)
        commitNoticeTimes(model, key)
    }

    function reloadCities() {
        cityModel.clear()
        cityModel.append({"text": qsTr("跟随默认城市"), "value": ""})
        let cities = []
        if (backend && backend.cityOptions) {
            cities = backend.cityOptions()
        } else if (typeof Configs !== "undefined" && Configs.data) {
            const rootConfig = Configs.data
            const pluginConfigs = rootConfig.plugins && rootConfig.plugins.configs
            const pluginConfig = pluginConfigs && pluginConfigs["com.helloswx.weather"]
            cities = (pluginConfig && pluginConfig.cities) || []
        }
        for (let i = 0; i < (cities || []).length; i++) {
            const city = cities[i]
            cityModel.append({"text": city.name || qsTr("未命名城市"), "value": String(city.id)})
        }
        const wanted = settings && settings.city_id ? settings.city_id : ""
        for (let i = 0; i < cityModel.count; i++) {
            if (cityModel.get(i).value === String(wanted)) {
                citySelector.currentIndex = i
                return
            }
        }
        citySelector.currentIndex = 0
    }

    Connections {
        target: typeof Configs !== "undefined" ? Configs : null
        function onConfigChanged() { layout.reloadCities() }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_location_20_regular"
        title: qsTr("显示城市")
        description: qsTr("选择该小组件显示的城市，或跟随默认城市")

        ComboBox {
            id: citySelector
            Layout.preferredWidth: 220
            model: cityModel
            textRole: "text"
            onActivated: settings.city_id = cityModel.get(currentIndex).value
        }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_temperature_20_regular"
        title: qsTr("主体显示")
        description: qsTr("主体屏幕展示实时温度，或当日最高 / 最低温")

        ComboBox {
            id: contentModeBox
            Layout.preferredWidth: 180
            property var values: ["current", "high_low"]
            model: ListModel {
                ListElement { text: qsTr("实时温度") }
                ListElement { text: qsTr("最高 / 最低温") }
            }
            onActivated: settings.content_mode = values[currentIndex]
            Component.onCompleted: currentIndex = Math.max(0, values.indexOf(settings.content_mode || "current"))
        }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_clock_20_regular"
        title: qsTr("加入「未来 3 小时」屏幕")
        description: qsTr("部分数据源没有逐小时预报，此时该屏幕不会出现")

        Switch {
            id: hourlySwitch
            onToggled: settings.show_hourly = checked
            Component.onCompleted: checked = settings.show_hourly === true
        }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_slide_multiple_20_regular"
        title: qsTr("轮播降水预报与天气预警")
        description: qsTr("关闭后只保留主体屏幕，降水与预警仅显示为右上角摘要")

        Switch {
            id: carouselSwitch
            onToggled: settings.carousel = checked
            Component.onCompleted: checked = settings.carousel !== false
        }
    }

    SettingCard {
        Layout.fillWidth: true
        visible: carouselSwitch.checked
        icon.name: "ic_fluent_timer_20_regular"
        title: qsTr("主体屏幕停留时长")
        description: qsTr("优先级最高，停留时间应长于次级屏幕（秒）")

        SpinBox {
            Layout.preferredWidth: 150
            from: 4
            to: 120
            stepSize: 1
            // RinUI 的上下箭头先发 valueModified 再改 value，推迟一轮再读最终值
            onValueModified: Qt.callLater(function() {
                if (settings) settings.main_seconds = value
            })
            Component.onCompleted: value = settings.main_seconds || 12
        }
    }

    SettingCard {
        Layout.fillWidth: true
        visible: carouselSwitch.checked
        icon.name: "ic_fluent_warning_20_regular"
        title: qsTr("降水 / 预警屏幕停留时长")
        description: qsTr("次级屏幕的停留时间（秒）")

        SpinBox {
            Layout.preferredWidth: 150
            from: 2
            to: 60
            stepSize: 1
            onValueModified: Qt.callLater(function() {
                if (settings) settings.detail_seconds = value
            })
            Component.onCompleted: value = settings.detail_seconds || 6
        }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_weather_sunny_20_regular"
        title: qsTr("室外状态提醒")
        description: qsTr("选择时间后点「添加」，可添加多个；到点显示当前天气")

        NoticeTimesEditor {
            objectName: "outdoorTimesEditor"
            Layout.preferredWidth: 236
            Layout.maximumWidth: 236
            defaultTime: "07:30"
            timesModel: outdoorTimesModel
            onTimeAdded: function(value) { layout.addNoticeTime(outdoorTimesModel, "outdoor_notice_times", value) }
            onTimeRemoved: function(index) { layout.removeNoticeTime(outdoorTimesModel, "outdoor_notice_times", index) }
        }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_calendar_20_regular"
        title: qsTr("明日天气提醒")
        description: qsTr("选择时间后点「添加」，可添加多个；到点显示明日天气、气温变化与是否降水")

        NoticeTimesEditor {
            objectName: "tomorrowTimesEditor"
            Layout.preferredWidth: 236
            Layout.maximumWidth: 236
            defaultTime: "20:00"
            timesModel: tomorrowTimesModel
            onTimeAdded: function(value) { layout.addNoticeTime(tomorrowTimesModel, "tomorrow_notice_times", value) }
            onTimeRemoved: function(index) { layout.removeNoticeTime(tomorrowTimesModel, "tomorrow_notice_times", index) }
        }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_timer_20_regular"
        title: qsTr("提醒显示时长")
        description: qsTr("每条到点提醒持续显示的秒数（5~180），默认 30 秒")

        SpinBox {
            objectName: "noticeSecondsBox"
            Layout.preferredWidth: 150
            from: 5
            to: 180
            stepSize: 5
            // RinUI 的上下箭头先发 valueModified 再改 value，直接写入会把
            // “上一格”存进配置；推迟到下一轮事件循环再读最终值。
            onValueModified: Qt.callLater(function() {
                if (settings) settings.notice_seconds = value
            })
            Component.onCompleted: value = settings && settings.notice_seconds ? settings.notice_seconds : 30
        }
    }
}
