import QtQuick
import qs.Commons

Item {
  id: root

  property real value: 0
  property real minimum: 0
  property real maximum: 100
  property real step: 1
  property string label: ""
  property color tint: Color.accent
  property Gradient spectrum: null
  readonly property bool dragging: pointer.pressed
  property real draft: value
  readonly property real liveValue: dragging ? draft : value
  readonly property real progress: Math.max(0, Math.min(1,
    (liveValue - minimum) / Math.max(1, maximum - minimum)))
  signal committed(real value)
  signal interacted()

  implicitHeight: Style.space(30)
  activeFocusOnTab: enabled && visible
  opacity: enabled ? 1 : 0.4
  Accessible.role: Accessible.Slider
  Accessible.name: label
  Accessible.description: Math.round(liveValue).toString()
  onActiveFocusChanged: if (activeFocus) interacted()

  function adjust(delta) {
    if (enabled) committed(Math.max(minimum, Math.min(maximum, value + delta)));
  }

  Keys.onPressed: function(event) {
    if (event.key === Qt.Key_Left || event.key === Qt.Key_Right) {
      adjust(event.key === Qt.Key_Right ? step : -step);
      event.accepted = true;
    }
  }

  Rectangle {
    id: rail
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    height: Style.space(10)
    radius: height / 2
    color: Qt.rgba(Color.popups.background.r * 0.88 + Color.foreground.r * 0.12,
      Color.popups.background.g * 0.88 + Color.foreground.g * 0.12,
      Color.popups.background.b * 0.88 + Color.foreground.b * 0.12, 1)
    gradient: root.spectrum

    Rectangle {
      visible: !root.spectrum
      width: parent.width * root.progress
      height: parent.height
      radius: parent.radius
      color: root.tint
    }
  }

  Rectangle {
    width: Style.space(root.dragging || root.activeFocus ? 22 : 18)
    height: width
    radius: width / 2
    x: root.progress * (parent.width - width)
    anchors.verticalCenter: rail.verticalCenter
    color: Color.foreground
    border.width: Style.space(3)
    border.color: root.activeFocus ? Color.accent : Color.popups.background
    Behavior on width { NumberAnimation { duration: 100 } }
  }

  MouseArea {
    id: pointer
    anchors.fill: parent
    enabled: root.enabled
    preventStealing: true
    cursorShape: Qt.PointingHandCursor

    function preview(x) {
      var fraction = Math.max(0, Math.min(1, x / Math.max(1, width)));
      root.draft = Math.round(root.minimum + fraction * (root.maximum - root.minimum));
    }
    onPressed: function(mouse) {
      root.forceActiveFocus();
      root.interacted();
      preview(mouse.x);
    }
    onPositionChanged: function(mouse) { if (pressed) preview(mouse.x); }
    onReleased: {
      // A canceled drag never emits a command; there is no offline queue.
      if (root.enabled) root.committed(root.draft);
    }
    onCanceled: root.draft = root.value
  }
}
