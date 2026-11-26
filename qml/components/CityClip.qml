import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI

Clip {
    id: root

    property var city: ({})
    signal makeDefaultRequested(string cityId)
    signal removeRequested(string cityId)

    implicitWidth: 210
    implicitHeight: 132
    radius: 6

    onClicked: root.makeDefaultRequested(String(city.id || ""))

    // ToolTip 要写成子对象：`import RinUI` 排在 `import QtQuick.Controls` 后面，
    // 裸名字 ToolTip 解析到 RinUI 的组件（附加属性拿到的还是 Qt 自带样式）。
    ToolTip {
        delay: 450
        text: {
            const location = city.latitude !== undefined && city.latitude !== null
                           ? qsTr("坐标 %1, %2").arg(Number(city.latitude).toFixed(2)).arg(Number(city.longitude).toFixed(2))
                           : qsTr("城市代码 %1").arg(city.providerCode || qsTr("暂无映射"))
            const status = city.error ? city.error : qsTr("当前数据源：%1").arg(city.provider || "")
            return [location, status].join("\n")
        }
        visible: root.hovered
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 14
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            spacing: 6

            IconWidget {
                size: 18
                icon: root.city.isDefault
                      ? "ic_fluent_star_20_filled"
                      : "ic_fluent_location_20_regular"
                color: root.city.isDefault
                       ? Colors.proxy.primaryColor
                       : Colors.proxy.textSecondaryColor
            }

            Text {
                Layout.fillWidth: true
                typography: Typography.BodyStrong
                text: root.city.name || qsTr("未命名城市")
                elide: Text.ElideRight
            }

            ToolButton {
                id: moreButton
                flat: true
                icon.name: "ic_fluent_more_horizontal_20_regular"
                onClicked: moreMenu.open()

                ToolTip {
                    text: qsTr("城市操作")
                    visible: moreButton.hovered
                }

                Menu {
                    id: moreMenu

                    MenuItem {
                        enabled: !root.city.isDefault
                        text: qsTr("设为默认城市")
                        onTriggered: root.makeDefaultRequested(String(root.city.id || ""))
                    }
                    MenuItem {
                        enabled: root.city.canRemove !== false
                        text: qsTr("移除")
                        onTriggered: root.removeRequested(String(root.city.id || ""))
                    }
                }
            }
        }

        Item { Layout.fillHeight: true }

        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Text {
                Layout.fillWidth: true
                typography: Typography.Caption
                color: Theme.currentTheme.colors.textSecondaryColor
                text: root.city.description || (root.city.error ? qsTr("暂不可用") : qsTr("等待刷新"))
                elide: Text.ElideRight
            }

            Text {
                typography: Typography.Title
                font.bold: false
                text: root.city.temperature !== undefined && root.city.temperature !== ""
                      ? root.city.temperature + "°"
                      : "--°"
            }
        }
    }
}
