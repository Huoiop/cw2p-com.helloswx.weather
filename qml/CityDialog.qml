import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI

/*!
    添加城市对话框。

    城市数据来自后端的 sqlite 城市库（每个库两千多条），所以 provinces() /
    citiesIn() / searchCities() 都是异步的：这里只负责发请求，结果分别从
    provincesLoaded / cityListLoaded / citiesFound 回来。过滤也在后端做
    （汉字 / 全拼 / 首字母都行），列表拿到什么就显示什么，不再在前端过滤一遍
    两千多条。
*/
Dialog {
    id: root

    property var backend
    property var info

    title: qsTr("添加城市")
    modal: true
    implicitWidth: 560

    readonly property bool coordinatesSupported: info?.supportsCoordinates ?? false
    readonly property bool hasLocalCityList: info?.hasCityList ?? false

    property bool useCoordinateInput: false
    property var currentSelection: null
    property var availableCities: []
    property bool provincesLoading: false
    property bool listLoading: false
    property string searchText: ""

    // 当前正在等的后端请求：迟到的响应直接丢掉
    property int pendingProvinceRequest: -2
    property string pendingListRequest: ""
    property string pendingSearchTerm: ""

    ListModel { id: filterProvinceList }

    onOpened: initialize()

    function initialize() {
        resetInputs()
        requestProvinces()
        requestCities(-1)
    }

    // 不能叫 reset()——Dialog 自带同名 signal，会变成非法重写
    function resetInputs() {
        // 输入模式必须在每次打开时按 info 重算：Component.onCompleted 时 info 还是 {}
        useCoordinateInput = coordinatesSupported && info?.locationMode === "coordinates"
        // PillButton 是 checkable 的，点一下就会写掉 checked 上的绑定，
        // 所以这里显式回填一次，交给 ButtonGroup 保证互斥。
        if (useCoordinateInput) {
            coordinatePill.checked = true
        } else {
            cityPill.checked = true
        }

        currentSelection = null
        availableCities = []
        provincesLoading = false
        listLoading = false
        pendingProvinceRequest = -2
        pendingListRequest = ""
        pendingSearchTerm = ""
        // 先停掉防抖，否则清空 searchBox 触发的那次 executeSearch 会在 280ms 后
        // 把用户刚点的城市清掉
        searchDebounceTimer.stop()
        searchText = ""
        searchBox.text = ""
        latitudeInput.text = info?.latitude ? String(info.latitude) : ""
        longitudeInput.text = info?.longitude ? String(info.longitude) : ""
    }

    /*! 后端命中缓存时会同步返回，命中就不用等后台线程 */
    function requestProvinces() {
        if (!backend) return
        pendingProvinceRequest = -1
        const cached = backend.provinces()
        if (cached && cached.length > 0) {
            applyProvinces(cached)
            return
        }
        provincesLoading = true
    }

    function applyProvinces(provinces) {
        pendingProvinceRequest = -2
        provincesLoading = false
        filterProvinceList.clear()
        filterProvinceList.append({ displayName: qsTr("热门城市"), id: -1 })
        for (let i = 0; i < provinces.length; i++) {
            filterProvinceList.append({ displayName: provinces[i], id: i })
        }
        provinceSelector.currentIndex = 0
    }

    function requestCities(provinceId) {
        if (!backend) return
        // -1 是热门城市列表，它同时也是「全局搜索」的哨兵值
        pendingListRequest = String(provinceId)
        pendingSearchTerm = ""
        currentSelection = null
        cityListView.currentIndex = -1
        const cached = backend.citiesIn(provinceId)
        if (cached && cached.length > 0) {
            applyCities(cached)
            return
        }
        availableCities = []
        listLoading = true
    }

    function applyCities(cities) {
        availableCities = cities || []
        listLoading = false
    }

    function executeSearch() {
        if (!backend) return

        const query = searchBox.text.trim()

        if (query === "") {
            requestCities(provinceSelector.currentIndex > 0
                          ? filterProvinceList.get(provinceSelector.currentIndex).id
                          : -1)
            return
        }

        const provinceId = provinceSelector.currentIndex > 0
            ? filterProvinceList.get(provinceSelector.currentIndex).id
            : -1
        pendingListRequest = ""
        pendingSearchTerm = query
        currentSelection = null
        cityListView.currentIndex = -1
        listLoading = true
        backend.searchCities(query, provinceId)
    }

    function getDisplayMessage() {
        if (listLoading) {
            return qsTr("正在加载…")
        }

        const count = root.availableCities.length
        if (count === 0) {
            return root.searchText.trim() === "" ? qsTr("没有可添加的城市") : qsTr("没有匹配的城市")
        }

        return qsTr("共 %1 个结果").arg(count)
    }

    readonly property bool canConfirm: {
        if (useCoordinateInput) {
            const lat = parseFloat(latitudeInput.text)
            const lon = parseFloat(longitudeInput.text)
            return !isNaN(lat) && !isNaN(lon)
        }
        return currentSelection !== null
    }

    Connections {
        target: root.backend
        enabled: root.backend !== null && root.backend !== undefined

        function onProvincesLoaded(providerId, provinces) {
            if (!root.backend || providerId !== root.backend.provider) return
            if (root.pendingProvinceRequest === -2) return
            root.applyProvinces(provinces || [])
        }

        function onCityListLoaded(providerId, provinceIndex, cities) {
            if (!root.backend || providerId !== root.backend.provider) return
            if (String(provinceIndex) !== root.pendingListRequest) return
            root.pendingListRequest = ""
            root.applyCities(cities)
        }

        function onCitiesFound(term, provinceIndex, results) {
            if (!root.backend) return
            // 只认最后一次输入的结果，忽略迟到的旧请求
            if (term !== root.pendingSearchTerm) return
            root.pendingSearchTerm = ""
            root.listLoading = false
            root.applyCities(results)
        }
    }

    Timer {
        id: searchDebounceTimer
        interval: 280
        onTriggered: root.executeSearch()
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            visible: coordinatesSupported

            ButtonGroup {
                id: inputModeGroup
                exclusive: true
            }

            PillButton {
                id: cityPill
                ButtonGroup.group: inputModeGroup
                text: qsTr("城市")
                onClicked: root.useCoordinateInput = false
            }

            PillButton {
                id: coordinatePill
                ButtonGroup.group: inputModeGroup
                text: qsTr("经纬度")
                onClicked: root.useCoordinateInput = true
            }

            Item { Layout.fillWidth: true }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 10
            visible: !root.useCoordinateInput

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                ComboBox {
                    id: provinceSelector
                    Layout.preferredWidth: 150
                    visible: hasLocalCityList
                    model: filterProvinceList
                    textRole: "displayName"
                    onActivated: {
                        root.executeSearch()
                    }
                }

                TextField {
                    id: searchBox
                    Layout.fillWidth: true
                    placeholderText: hasLocalCityList ? qsTr("搜索城市") : qsTr("搜索城市，例如「柏林」")
                    onTextChanged: {
                        root.searchText = text
                        searchDebounceTimer.restart()
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                typography: Typography.Caption
                color: Theme.currentTheme.colors.textSecondaryColor
                text: root.getDisplayMessage()
            }



            Frame {
                Layout.fillWidth: true
                Layout.preferredHeight: 300

                ListView {
                    id: cityListView
                    anchors.fill: parent
                    clip: true
                    model: root.availableCities
                    currentIndex: -1

                    delegate: ListViewDelegate {
                        required property var modelData
                        required property int index

                        width: ListView.view.width
                        text: modelData.name || ""
                        highlighted: cityListView.currentIndex === index

                        onClicked: {
                            cityListView.currentIndex = index
                            root.currentSelection = modelData
                        }
                    }

                    ScrollBar.vertical: ScrollBar {
                        policy: ScrollBar.AsNeeded
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 10
            visible: root.useCoordinateInput

            Text {
                Layout.fillWidth: true
                typography: Typography.Caption
                color: Theme.currentTheme.colors.textSecondaryColor
                wrapMode: Text.Wrap
                text: qsTr("纬度范围 -90 ~ 90，经度范围 -180 ~ 180。可在地图应用中长按获取坐标。")
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 16

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Text {
                        text: qsTr("纬度")
                        typography: Typography.Body
                    }

                    TextField {
                        id: latitudeInput
                        Layout.fillWidth: true
                        placeholderText: "39.9042"
                        validator: DoubleValidator {
                            bottom: -90
                            top: 90
                            decimals: 6
                            notation: DoubleValidator.StandardNotation
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Text {
                        text: qsTr("经度")
                        typography: Typography.Body
                    }

                    TextField {
                        id: longitudeInput
                        Layout.fillWidth: true
                        placeholderText: "116.4074"
                        validator: DoubleValidator {
                            bottom: -180
                            top: 180
                            decimals: 6
                            notation: DoubleValidator.StandardNotation
                        }
                    }
                }
            }
        }
    }

    footer: DialogButtonBox {
        standardButtons: DialogButtonBox.Ok | DialogButtonBox.Cancel

        property Button confirmButton: standardButton(DialogButtonBox.Ok)

        Component.onCompleted: {
            confirmButton.enabled = Qt.binding(() => root.canConfirm)
        }

        onAccepted: {
            if (!backend) {
                root.close()
                return
            }

            if (root.useCoordinateInput) {
                const lat = parseFloat(latitudeInput.text)
                const lon = parseFloat(longitudeInput.text)
                // 名字留空，后端会在后台做逆地理编码再补上
                backend.addCityRecord({
                    "name": "",
                    "mode": "coordinates",
                    "latitude": lat,
                    "longitude": lon
                })
            } else if (root.currentSelection) {
                // name 是长名称（列表里显示的那个），short_name 是短名称（小组件上显示）
                backend.addCityRecord({
                    "name": root.currentSelection.name ?? "",
                    "short_name": root.currentSelection.short_name ?? "",
                    "code": root.currentSelection.code ?? "",
                    "mode": root.currentSelection.mode ?? "city",
                    "latitude": parseFloat(root.currentSelection.latitude ?? 0) || null,
                    "longitude": parseFloat(root.currentSelection.longitude ?? 0) || null
                })
            }

            root.close()
        }

        onRejected: {
            root.close()
        }
    }
}
