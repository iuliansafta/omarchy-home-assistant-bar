import QtQuick
import qs.Commons
import "lib/HaModel.js" as Ha

Rectangle {
  id: root

  required property var light
  property var pending: null
  property bool connected: false
  property bool selected: false
  property bool expanded: false
  property string colorTab: light.supports_color ? "color" : "white"
  readonly property bool available: connected && light.state !== "unavailable"
  readonly property bool controllable: available && !pending
  readonly property bool isOn: light.state === "on"
  readonly property var target: pending ? pending.target : ({})
  readonly property real brightness: target.brightness !== undefined
    ? Math.round(target.brightness / 255 * 100)
    : isOn && light.brightness !== null ? Math.round((light.brightness || 0) / 255 * 100) : 0
  readonly property var hs: Ha.rgbToHs(target.rgb_color || light.rgb_color)
  readonly property color tint: light.supports_color && light.color_mode !== "color_temp"
    && light.rgb_color ? Qt.rgba(light.rgb_color[0] / 255, light.rgb_color[1] / 255,
      light.rgb_color[2] / 255, 1) : Color.accent
  signal powerRequested()
  signal settingsRequested(var settings)
  signal expandRequested()
  signal interacted()

  function blend(base, overlay, amount) {
    return Qt.rgba(base.r * (1 - amount) + overlay.r * amount,
      base.g * (1 - amount) + overlay.g * amount,
      base.b * (1 - amount) + overlay.b * amount, 1);
  }

  implicitHeight: content.implicitHeight + Style.space(24)
  radius: Style.space(14)
  color: blend(Color.popups.background, isOn ? tint : Color.foreground, isOn ? 0.09 : 0.035)
  border.width: Math.max(1, Style.space(1))
  border.color: blend(color, selected ? Color.accent : isOn ? tint : Color.foreground,
    selected ? 0.8 : isOn ? 0.32 : 0.12)
  Behavior on color { ColorAnimation { duration: 180 } }

  function sendColor(hue, saturation) {
    if (controllable) settingsRequested({rgb_color: Ha.hsToRgb(hue, saturation)});
  }

  Column {
    id: content
    x: Style.space(12)
    y: Style.space(12)
    width: parent.width - Style.space(24)
    spacing: Style.space(8)

    Item {
      width: parent.width
      height: Style.space(40)

      Rectangle {
        id: bulb
        width: Style.space(40)
        height: width
        radius: width / 2
        color: root.blend(root.color, root.isOn ? root.tint : Color.foreground, 0.14)
        Text {
          anchors.centerIn: parent
          text: Ha.iconFor(root.light)
          textFormat: Text.PlainText
          color: root.isOn ? root.tint : Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.title
        }
      }

      Column {
        anchors.left: bulb.right
        anchors.leftMargin: Style.space(10)
        anchors.right: power.left
        anchors.rightMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(3)
        Text {
          width: parent.width
          text: root.light.name
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          font.bold: true
        }
        Text {
          width: parent.width
          text: !root.connected ? "Offline" : root.light.state === "unavailable" ? "Unavailable"
            : root.pending ? "Waiting for light..." : root.isOn ? "On" : "Off"
          textFormat: Text.PlainText
          elide: Text.ElideRight
          color: root.available ? Color.muted : Color.urgent
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }

      Rectangle {
        id: power
        objectName: "powerButton"
        anchors.right: parent.right
        width: Style.space(36)
        height: width
        radius: width / 2
        color: root.blend(root.color, root.isOn ? root.tint : Color.foreground,
          powerMouse.containsMouse ? 0.3 : 0.13)
        border.width: activeFocus ? 2 : 0
        border.color: Color.accent
        enabled: root.controllable
        opacity: enabled ? 1 : 0.4
        activeFocusOnTab: true
        Accessible.role: Accessible.Button
        Accessible.name: (root.isOn ? "Turn off " : "Turn on ") + root.light.name
        Accessible.onPressAction: root.powerRequested()
        onActiveFocusChanged: if (activeFocus) root.interacted()
        function activate() { root.powerRequested(); }
        Keys.onSpacePressed: root.powerRequested()
        Keys.onReturnPressed: root.powerRequested()
        Text {
          anchors.centerIn: parent
          text: "\uF011"
          textFormat: Text.PlainText
          color: root.isOn ? root.tint : Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.body
        }
        MouseArea {
          id: powerMouse
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onClicked: { root.interacted(); root.powerRequested(); }
        }
      }
    }

    Column {
      width: parent.width
      visible: !!root.light.supports_brightness
      spacing: 0
      Item {
        width: parent.width
        height: brightnessLabel.implicitHeight
        Text {
          id: brightnessLabel
          text: "Brightness"
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
        Text {
          anchors.right: parent.right
          text: Math.round(intensity.liveValue) + "%"
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
      }
      LightSlider {
        id: intensity
        objectName: "brightnessSlider"
        width: parent.width
        value: root.brightness
        step: 5
        tint: root.tint
        label: root.light.name + " brightness"
        enabled: root.controllable
        onInteracted: root.interacted()
        onCommitted: function(value) {
          root.settingsRequested({brightness: Math.round(value / 100 * 255)});
        }
      }
    }

    Rectangle {
      objectName: "expandButton"
      width: parent.width
      height: Style.space(28)
      radius: Style.space(7)
      visible: !!root.light.supports_color || !!root.light.supports_color_temp
      color: root.blend(root.color, Color.foreground, colorMouse.containsMouse || activeFocus ? 0.09 : 0.04)
      activeFocusOnTab: visible
      Accessible.role: Accessible.Button
      Accessible.name: "Color controls for " + root.light.name
      Accessible.onPressAction: root.expandRequested()
      onActiveFocusChanged: if (activeFocus) root.interacted()
      function activate() { root.expandRequested(); }
      Keys.onSpacePressed: root.expandRequested()
      Keys.onReturnPressed: root.expandRequested()
      Text {
        anchors.centerIn: parent
        text: (root.expanded ? "Hide " : "Adjust ")
          + (root.light.supports_color && root.light.supports_color_temp ? "color & warmth"
            : root.light.supports_color ? "color" : "warmth") + (root.expanded ? "  -" : "  +")
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
      }
      MouseArea {
        id: colorMouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: { root.interacted(); root.expandRequested(); }
      }
    }

    Column {
      width: parent.width
      spacing: Style.space(8)
      visible: root.expanded && (!!root.light.supports_color || !!root.light.supports_color_temp)

      Row {
        width: parent.width
        spacing: Style.space(6)
        visible: !!root.light.supports_color && !!root.light.supports_color_temp
        Repeater {
          model: ["color", "white"]
          Rectangle {
            required property string modelData
            objectName: "colorTabButton"
            width: (parent.width - Style.space(6)) / 2
            height: Style.space(28)
            radius: Style.space(7)
            color: root.blend(root.color, Color.foreground,
              root.colorTab === modelData || activeFocus ? 0.15 : 0.04)
            activeFocusOnTab: true
            Accessible.role: Accessible.Button
            Accessible.name: modelData === "color" ? "Color" : "White temperature"
            onActiveFocusChanged: if (activeFocus) root.interacted()
            function activate() { root.colorTab = modelData; }
            Keys.onSpacePressed: root.colorTab = modelData
            Keys.onReturnPressed: root.colorTab = modelData
            Text {
              anchors.centerIn: parent
              text: modelData === "color" ? "Color" : "White"
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.colorTab = parent.modelData
            }
          }
        }
      }

      Column {
        width: parent.width
        spacing: Style.space(4)
        visible: !!root.light.supports_color && (root.colorTab === "color" || !root.light.supports_color_temp)

        Row {
          width: parent.width
          spacing: Style.space(8)
          Repeater {
            model: ["#ffb347", "#ff654a", "#ff66a3", "#ab68ff", "#568aff", "#46e6bc", "#ffffff"]
            Rectangle {
              required property string modelData
              objectName: "colorPresetButton"
              width: (parent.width - Style.space(48)) / 7
              height: Style.space(26)
              radius: Style.space(8)
              color: modelData
              border.width: activeFocus ? 3 : 1
              border.color: activeFocus ? Color.accent : Util.alpha(Color.foreground, 0.25)
              enabled: root.controllable
              opacity: enabled ? 1 : 0.4
              activeFocusOnTab: true
              Accessible.role: Accessible.Button
              Accessible.name: "Set color " + modelData
              onActiveFocusChanged: if (activeFocus) root.interacted()
              function choose() {
                root.settingsRequested({rgb_color: [Math.round(color.r * 255),
                  Math.round(color.g * 255), Math.round(color.b * 255)]});
              }
              function activate() { choose(); }
              Keys.onSpacePressed: choose()
              Keys.onReturnPressed: choose()
              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: parent.choose()
              }
            }
          }
        }

        Text {
          text: "Hue  " + Math.round(hue.liveValue) + "\u00b0"
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
        LightSlider {
          id: hue
          objectName: "hueSlider"
          width: parent.width
          maximum: 360
          step: 5
          value: root.hs[0]
          enabled: root.controllable
          label: root.light.name + " hue"
          spectrum: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: "#ff5555" }
            GradientStop { position: 0.167; color: "#ffff55" }
            GradientStop { position: 0.333; color: "#55ff55" }
            GradientStop { position: 0.5; color: "#55ffff" }
            GradientStop { position: 0.667; color: "#5555ff" }
            GradientStop { position: 0.833; color: "#ff55ff" }
            GradientStop { position: 1; color: "#ff5555" }
          }
          onInteracted: root.interacted()
          onCommitted: function(value) { root.sendColor(value, root.hs[1] || 100); }
        }
        Text {
          text: "Saturation  " + Math.round(saturation.liveValue) + "%"
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
        LightSlider {
          id: saturation
          objectName: "saturationSlider"
          width: parent.width
          value: root.hs[1]
          step: 5
          enabled: root.controllable
          label: root.light.name + " saturation"
          spectrum: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: "#ffffff" }
            GradientStop { position: 1; color: Qt.hsva(hue.liveValue / 360, 1, 1, 1) }
          }
          onInteracted: root.interacted()
          onCommitted: function(value) { root.sendColor(root.hs[0], value); }
        }
      }

      Column {
        width: parent.width
        spacing: Style.space(4)
        visible: !!root.light.supports_color_temp && (root.colorTab === "white" || !root.light.supports_color)
        Text {
          text: "Warmth  " + Math.round(temperature.liveValue) + " K"
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
        LightSlider {
          id: temperature
          objectName: "temperatureSlider"
          width: parent.width
          minimum: root.light.min_color_temp_kelvin || 0
          maximum: root.light.max_color_temp_kelvin || 0
          value: root.target.color_temp_kelvin || root.light.color_temp_kelvin || minimum
          step: 100
          enabled: root.controllable
          label: root.light.name + " white temperature in kelvin"
          spectrum: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: "#ffb45c" }
            GradientStop { position: 0.5; color: "#fff2dc" }
            GradientStop { position: 1; color: "#bddcff" }
          }
          onInteracted: root.interacted()
          onCommitted: function(value) { root.settingsRequested({color_temp_kelvin: Math.round(value)}); }
        }
        Item {
          width: parent.width
          height: warmLabel.implicitHeight
          Text {
            id: warmLabel
            text: "Warm"
            color: Color.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
          Text {
            anchors.right: parent.right
            text: "Cool"
            color: Color.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }
}
