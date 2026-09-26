import QtQuick
import QtQuick.Controls

Item {
    id: root
    readonly property bool active: bar.state !== "idle"
    readonly property bool showToast: bar.message !== ""
    readonly property var win: Window.window
    // Layer-shell surfaces can't report their position, so during a drag the surface
    // covers the screen and the pill is drawn at the bar position inside it.
    readonly property bool fullscreenDrag: win.layerShell && bar.dragging

    Item {
        id: frame
        width: win.barWidth
        height: win.barHeight
        x: root.fullscreenDrag ? bar.posX : (root.width - width) / 2
        y: root.fullscreenDrag ? bar.posY : (root.height - height) / 2
        // During a drag only the surface on the monitor under the cursor draws the bar.
        // Opacity, not visibility: hiding the item would cancel the drag's mouse grab.
        opacity: !root.fullscreenDrag || bar.dragScreen === Screen.name ? 1 : 0

    Rectangle {
        id: pill
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.verticalCenter: parent.verticalCenter
        // Everything inside is laid out at 100% and scaled as a whole (Settings > Interface).
        scale: bar.uiScale
        height: root.active || root.showToast ? 40 : 30
        width: root.showToast && !root.active ? Math.min(frame.width / bar.uiScale - 8, toast.implicitWidth + 28)
             : bar.state === "processing" ? procRow.implicitWidth + 32
             : root.active ? (bar.state === "locked" ? 212 : 148) : idleRow.implicitWidth + 26
        readonly property var t: bar.theme
        radius: height / 2 * t.roundness
        color: Qt.alpha(t.background, t.opacity)
        border.color: bar.mode === "command" && root.active ? t.command : t.border
        border.width: 1
        Behavior on width { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
        Behavior on height { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }

        // Drag the pill to move the bar; a click without movement acts as a click.
        MouseArea {
            id: dragArea
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            cursorShape: moved ? Qt.ClosedHandCursor : Qt.PointingHandCursor
            property point pressAt
            property point grab
            property bool moved: false

            function winPos(mouse) { return mapToItem(null, mouse.x, mouse.y) }

            // Cursor position on the screen, in the same coordinates as bar.posX/posY.
            function screenPos(mouse) {
                const p = winPos(mouse)
                if (!win.layerShell)
                    return Qt.point(win.x - Screen.virtualX + p.x, win.y - Screen.virtualY + p.y)
                // Until the compositor has resized the surface, events are still in
                // small-window coordinates; skip them.
                if (win.width < Screen.width) return null
                return p
            }

            onPressed: (mouse) => {
                if (mouse.button !== Qt.LeftButton) return
                pressAt = winPos(mouse)
                // Offset of the cursor inside the bar-sized frame.
                grab = mapToItem(frame, mouse.x, mouse.y)
                moved = false
            }
            onPositionChanged: (mouse) => {
                if (!(mouse.buttons & Qt.LeftButton)) return
                if (!moved) {
                    const p = winPos(mouse)
                    if (Math.abs(p.x - pressAt.x) + Math.abs(p.y - pressAt.y) < 5) return
                    moved = true
                    if (!bar.hasPos) {
                        const d = win.dockedPos()
                        bar.moveTo(Math.round(d.x), Math.round(d.y))
                    }
                    bar.setDragging(true)
                }
                const c = screenPos(mouse)
                if (!c) return
                if (win.layerShell) {
                    // The press keeps the pointer grab, so coordinates keep coming even when the
                    // cursor is over another monitor; Python maps them to that monitor.
                    bar.dragTo(Math.round(c.x), Math.round(c.y), Math.round(grab.x), Math.round(grab.y))
                    return
                }
                const nx = Math.min(Math.max(0, c.x - grab.x), Screen.width - frame.width)
                const ny = Math.min(Math.max(0, c.y - grab.y), Screen.height - frame.height)
                bar.moveTo(Math.round(nx), Math.round(ny))
            }
            onReleased: (mouse) => {
                if (moved) bar.endDrag()
            }
            onCanceled: {
                if (moved) bar.endDrag()
                moved = false
            }
            onClicked: (mouse) => {
                if (moved) { moved = false; return }
                if (mouse.button === Qt.RightButton) menu.popup()
                else if (!root.active) bar.openHub()
            }
        }

        // idle widget: status dot + name; click opens Talki
        Row {
            id: idleRow
            visible: !root.active && !root.showToast
            anchors.centerIn: parent
            spacing: 8
            Rectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: 9; height: 9; radius: 4.5
                // green: ready to dictate, amber: speech model loading or unavailable
                color: bar.ready ? pill.t.ready : "#e5a13a"
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: pill.t.label
                visible: text !== ""
                color: pill.t.text
                font.pixelSize: 13
                font.weight: Font.DemiBold
            }
        }

        // recording indicator
        Rectangle {
            visible: bar.state === "recording"
            anchors.verticalCenter: parent.verticalCenter
            anchors.right: parent.right
            anchors.rightMargin: 12
            width: 8; height: 8; radius: 4
            color: pill.t.recording
            SequentialAnimation on opacity {
                loops: Animation.Infinite
                running: bar.state === "recording"
                NumberAnimation { from: 1; to: 0.3; duration: 600 }
                NumberAnimation { from: 0.3; to: 1; duration: 600 }
            }
        }

        // waveform
        Row {
            id: wave
            visible: bar.state === "recording" || bar.state === "locked"
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: bar.state === "locked" ? 44 : 18
            spacing: 3
            Repeater {
                model: bar.levels
                Rectangle {
                    width: 4
                    radius: 2
                    anchors.verticalCenter: parent.verticalCenter
                    height: Math.max(4, Math.min(26, 4 + modelData * 30))
                    color: bar.mode === "command" ? pill.t.command : pill.t.accent
                    Behavior on height { NumberAnimation { duration: 90 } }
                }
            }
        }

        // cancel
        Rectangle {
            visible: bar.state === "locked"
            width: 26; height: 26; radius: 13
            anchors.verticalCenter: parent.verticalCenter
            anchors.left: parent.left
            anchors.leftMargin: 8
            color: Qt.alpha(pill.t.text, cancelArea.containsMouse ? 0.33 : 0.15)
            Text { anchors.centerIn: parent; text: "✕"; color: pill.t.text; font.pixelSize: 12 }
            MouseArea { id: cancelArea; anchors.fill: parent; hoverEnabled: true; onClicked: bar.cancel() }
        }

        // stop
        Rectangle {
            visible: bar.state === "locked"
            width: 26; height: 26; radius: 13
            anchors.verticalCenter: parent.verticalCenter
            anchors.right: parent.right
            anchors.rightMargin: 8
            color: stopArea.containsMouse ? Qt.lighter(pill.t.recording, 1.15) : pill.t.recording
            Rectangle { anchors.centerIn: parent; width: 9; height: 9; radius: 2; color: "white" }
            MouseArea { id: stopArea; anchors.fill: parent; hoverEnabled: true; onClicked: bar.stop() }
        }

        // processing: animated dots + current step ("Transcribing…", "Polishing…")
        Row {
            id: procRow
            visible: bar.state === "processing"
            anchors.centerIn: parent
            spacing: 10
            Row {
            anchors.verticalCenter: parent.verticalCenter
            spacing: 6
            Repeater {
                model: 3
                Rectangle {
                    id: dot
                    width: 7; height: 7; radius: 3.5
                    color: bar.mode === "command" || bar.mode === "transform" ? pill.t.command : pill.t.accent
                    SequentialAnimation on opacity {
                        loops: Animation.Infinite
                        running: bar.state === "processing"
                        PauseAnimation { duration: index * 150 }
                        NumberAnimation { from: 0.25; to: 1; duration: 300 }
                        NumberAnimation { from: 1; to: 0.25; duration: 300 }
                        PauseAnimation { duration: (2 - index) * 150 }
                    }
                }
            }
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                visible: text !== ""
                text: bar.progress !== "" ? bar.progress
                    : bar.mode === "transform" || bar.mode === "command" ? "Editing…" : "Processing…"
                color: pill.t.text
                font.pixelSize: 13
            }
        }

        Text {
            id: toast
            visible: root.showToast && !root.active
            anchors.centerIn: parent
            text: bar.message
            color: pill.t.text
            font.pixelSize: 12
            elide: Text.ElideRight
            width: Math.min(implicitWidth, frame.width / bar.uiScale - 36)
        }
    }
    }

    Menu {
        id: menu
        popupType: Popup.Window
        Menu {
            title: "Microphone"
            popupType: Popup.Window
            Instantiator {
                model: bar.mics
                delegate: MenuItem {
                    text: modelData.label
                    checkable: true
                    checked: modelData.selected
                    onTriggered: bar.selectMic(modelData.name)
                }
                onObjectAdded: (index, object) => micMenuInsert(index, object)
                onObjectRemoved: (index, object) => micMenuRemove(object)
            }
        }
        Menu {
            id: langMenu
            title: "Language"
            popupType: Popup.Window
            Instantiator {
                model: bar.languageChoices
                delegate: MenuItem {
                    text: modelData.label
                    checkable: true
                    checked: modelData.selected
                    onTriggered: bar.selectLanguage(modelData.code)
                }
                onObjectAdded: (index, object) => langMenu.insertItem(index, object)
                onObjectRemoved: (index, object) => langMenu.removeItem(object)
            }
        }
        MenuSeparator {}
        MenuItem { text: "Start dictation"; enabled: !root.active; onTriggered: bar.startHandsFree() }
        MenuItem { text: "Reset position"; enabled: bar.hasPos; onTriggered: bar.resetPos() }
        MenuItem { text: "Hide for 1 hour"; onTriggered: bar.hideForHour() }
        MenuItem { text: "Open Talki"; onTriggered: bar.openHub() }
    }

    function micMenuInsert(index, object) { menu.menuAt(0).insertItem(index, object) }
    function micMenuRemove(object) { menu.menuAt(0).removeItem(object) }
}
