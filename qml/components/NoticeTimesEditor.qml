import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI

/*!
    到点提醒的时间编辑器。

    时间只通过 TimePicker 选择，不提供键盘输入；选好后点「添加」追加到
    ``timesModel``，每个已添加的时间以 chip 形式列出并可以单独删除。
*/
ColumnLayout {
    id: editor

    property var timesModel: null
    property string defaultTime: "07:30"

    signal timeAdded(string value)
    signal timeRemoved(int index)

    implicitWidth: 236
    Layout.preferredWidth: 236
    spacing: 8

    RowLayout {
        Layout.fillWidth: true
        spacing: 8

        TimePicker {
            id: picker
            use24Hour: true
            implicitWidth: 150
            Layout.preferredWidth: 150
            Component.onCompleted: setTime(editor.defaultTime)
        }

        Button {
            text: qsTr("添加")
            onClicked: editor.timeAdded(picker.time)
        }

        Item { Layout.fillWidth: true }
    }

    Text {
        visible: !editor.timesModel || editor.timesModel.count === 0
        text: qsTr("尚未添加时间")
        typography: Typography.Caption
        color: Theme.currentTheme.colors.textSecondaryColor
    }

    Flow {
        Layout.fillWidth: true
        spacing: 6

        Repeater {
            model: editor.timesModel

            delegate: Rectangle {
                id: chip
                implicitWidth: chipRow.implicitWidth + 12
                implicitHeight: chipRow.implicitHeight + 6
                radius: 6
                color: Theme.currentTheme.colors.controlSecondaryColor
                border.width: 1
                border.color: Theme.currentTheme.colors.cardBorderColor

                RowLayout {
                    id: chipRow
                    anchors.centerIn: parent
                    spacing: 2

                    Text {
                        text: model.time
                        typography: Typography.Body
                        color: Theme.currentTheme.colors.textColor
                    }

                    ToolButton {
                        flat: true
                        size: 12
                        implicitWidth: 20
                        implicitHeight: 20
                        icon.name: "ic_fluent_dismiss_20_regular"
                        onClicked: editor.timeRemoved(index)
                    }
                }
            }
        }
    }
}
