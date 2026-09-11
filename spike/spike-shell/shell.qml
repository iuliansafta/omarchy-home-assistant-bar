import QtQuick
import Quickshell
import Quickshell.Io

// Phase-1 spike shell: proves Quickshell.Io.Socket connects to the Python
// controller, sends line-delimited JSON commands, receives snapshot/push/
// reply messages, and that keyring access stays out of QML entirely.
ShellRoot {
  id: root

  property var socketPath: Quickshell.env("XDG_RUNTIME_DIR") + "/omarchy-ha/spike.sock"
  property int nextId: 1
  property var pending: ({})   // id -> callback label
  property string log: ""

  function send(msg, tag) {
    var id = nextId++
    msg.id = id
    pending[id] = tag || "reply"
    sock.write(JSON.stringify(msg) + "\n")
    append("→ " + JSON.stringify(msg))
  }

  function append(line) {
    log = log + (log !== "" ? "\n" : "") + line
    console.info("spike:", line)
  }

  Timer { id: quitter; interval: 7000; onTriggered: Qt.exit(0) }

  Socket {
    id: sock
    path: root.socketPath
    connected: true

    parser: SplitParser {
      onRead: function(data) {
        var msg = JSON.parse(data)
        if (msg.type === "snapshot") {
          root.append("← snapshot: connected=" + msg.connected
            + " server=" + msg.server.version
            + " areas=" + msg.areas.length
            + " lights=" + msg.lights.length)
          // Issue commands once the snapshot lands.
          root.send({ cmd: "ping" }, "ping")
          root.send({ cmd: "light.set", entity_id: "light.ceiling", on: true }, "light.set")
          root.send({ cmd: "secret.check" }, "secret.check")
          root.send({ cmd: "light.set", entity_id: "not-a-light" }, "light.set-invalid")
        } else if (msg.type === "update") {
          root.append("← push update: " + JSON.stringify(msg.changes))
        } else if (msg.type === "reply") {
          var tag = root.pending[msg.id] || "reply"
          delete root.pending[msg.id]
          root.append("← reply [" + tag + "]: ok=" + msg.ok
            + (msg.error ? " error=" + msg.error : ""))
        } else {
          root.append("← " + JSON.stringify(msg))
        }
      }
    }

    onConnectedChanged: {
      if (connected) {
        root.append("connected to " + root.socketPath)
        root.send({ cmd: "hello", client: "spike-shell" })
      } else {
        root.append("disconnected")
      }
    }
    onError: function(err) { root.append("socket error: " + err) }
  }

  PanelWindow {
    visible: true
    exclusiveZone: 0
    anchors { top: true; left: true }
    implicitWidth: 900
    implicitHeight: 320
    color: "black"

    Text {
      anchors.fill: parent
      anchors.margins: 16
      text: "Home Assistant IPC spike\n" + root.log
      color: "white"
      font.pixelSize: 14
      font.family: "monospace"
      wrapMode: Text.WrapAnywhere
    }
  }

  IpcHandler {
    target: "ha.spike"
    function status(): string { return "spike-ok" }
  }

  Component.onCompleted: quitter.start()
}
