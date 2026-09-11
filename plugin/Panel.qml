import QtQuick
import QtQuick.Window
import Quickshell
import qs.Ui as Ui
import qs.Commons
import "lib/HaModel.js" as Ha

Ui.KeyboardPanel {
  id: root
  required property var service

  property int selected: -1  // no row highlighted until the user navigates
  property string mode: "main"           // main | picker
  property var pickerAreas: []
  property var pickerLights: []
  property var selAreas: ({})
  property var selLights: ({})
  property string pickerMessage: ""
  property string expandedEntity: ""
  property string availabilityFilter: "all"

  readonly property bool connected: service ? service.connected : false
  readonly property string status: service ? service.status : "starting"
  readonly property int availableCount: {
    var count = 0;
    var lights = service ? service.lights : [];
    for (var i = 0; i < lights.length; i++)
      if (lights[i] && lights[i].state !== "unavailable") count++;
    return count;
  }
  readonly property int unavailableCount: {
    var count = 0;
    var lights = service ? service.lights : [];
    for (var i = 0; i < lights.length; i++)
      if (lights[i] && lights[i].state === "unavailable") count++;
    return count;
  }
  readonly property var filteredLights: {
    var lights = service ? service.lights : [];
    if (availabilityFilter === "all") return lights;
    var unavailable = availabilityFilter === "unavailable";
    return lights.filter(function(light) {
      return light && (light.state === "unavailable") === unavailable;
    });
  }
  readonly property var groups: service
    ? Ha.groupByArea(filteredLights, service.areas) : []
  readonly property var lightRows: {
    var rows = [];
    for (var g = 0; g < groups.length; g++)
      for (var i = 0; i < groups[g].lights.length; i++)
        rows.push({ light: groups[g].lights[i], group: groups[g].name });
    return rows;
  }
  function lightsOfArea(areaId) {
    var out = [];
    for (var i = 0; i < pickerLights.length; i++)
      if (pickerLights[i] && pickerLights[i].area === areaId) out.push(pickerLights[i]);
    return out;
  }

  function pendingFor(entityId) {
    return service && service.pending[entityId] ? service.pending[entityId] : null;
  }
  function setAvailabilityFilter(value) {
    if (availabilityFilter === value) return;
    availabilityFilter = value;
    selected = -1;
    expandedEntity = "";
    lightsScroll.contentY = 0;
  }
  function focusedItem() {
    return keys.Window.window && keys.Window.window.activeFocusItem
      ? keys.Window.window.activeFocusItem : keys;
  }
  property string urlInput: ""
  property bool httpAcknowledged: false
  property string setupError: ""
  property string notice: ""
  function showNotice(text) {
    notice = text;
    noticeReset.restart();
  }
  // ---- picker (choose lights) -------------------------------------------
  function openPicker() {
    if (!service) return;
    pickerMessage = "";
    service.pickerData(function(reply) {
      if (!reply.ok) { pickerMessage = reply.error || "Could not load lights"; return; }
      var extra = reply.extra || {};
      pickerAreas = extra.areas || [];
      pickerLights = extra.lights || [];
      var sel = extra.selection || {};
      selAreas = {}; selLights = {};
      var arr = sel.areas || [];
      for (var i = 0; i < arr.length; i++) selAreas[arr[i]] = true;
      arr = sel.entities || [];
      for (var j = 0; j < arr.length; j++) selLights[arr[j]] = true;
      mode = "picker";
    });
  }
  function toggleDraftArea(areaId) {
    var adding = !selAreas[areaId];
    var next = {};
    for (var id in selAreas) if (selAreas[id]) next[id] = true;
    if (adding) next[areaId] = true;
    selAreas = next;
    // Area toggle also (de)selects its lights so the draft stays consistent.
    var lightsNext = {};
    for (var l in selLights) if (selLights[l]) lightsNext[l] = true;
    for (var k = 0; k < pickerLights.length; k++) {
      var light = pickerLights[k];
      if (light && light.area === areaId) {
        if (adding) lightsNext[light.entity_id] = true;
        else delete lightsNext[light.entity_id];
      }
    }
    selLights = lightsNext;
  }
  function toggleDraftLight(entityId) {
    var adding = !selLights[entityId];
    var next = {};
    for (var id in selLights) if (selLights[id]) next[id] = true;
    if (adding) next[entityId] = true;
    else delete next[entityId];
    selLights = next;
  }
  function savePicker() {
    if (!service) return;
    var areas = [], entities = [];
    for (var a in selAreas) if (selAreas[a]) areas.push(a);
    for (var e in selLights) if (selLights[e]) entities.push(e);
    service.pickerSave(areas, entities, function(reply) {
      if (!reply.ok) { pickerMessage = reply.error || "Could not save"; return; }
      mode = "main";
    });
  }

  function toggleLight(light) {
    if (!service) return;
    if (!connected) { showNotice("Offline — reconnecting"); return; }
    if (!light || light.state === "unavailable") {
      showNotice("\u201C" + (light ? light.name : "") + "\u201D is unreachable — check its wall switch or the Hue bridge");
      return;
    }
    service.setLight(light.entity_id, light.state !== "on", function(reply) {
      if (!reply.ok) root.showNotice("Command failed: " + (reply.error || "unknown"));
    });
  }
  function setLightSettings(light, settings) {
    if (!service || !connected || !light || light.state === "unavailable") return;
    service.setLightSettings(light.entity_id, settings, function(reply) {
      if (!reply.ok) root.showNotice("Command failed: " + (reply.error || "unknown"));
    });
  }
  function submitUrl() {
    if (!service || urlInput.trim() === "") return;
    setupError = "";
    httpAcknowledged = false;
    service.setupUrl(urlInput, function(reply) {
      if (!reply.ok) { setupError = reply.error || "Could not reach the server"; return; }
      urlInput = reply.extra.url || urlInput;
      // HTTP warning gate: acknowledged resets until the user confirms.
    });
  }
  function startBrowser() {
    if (!service) return;
    setupError = "";
    service.setupBrowser(function(reply) {
      if (!reply.ok) setupError = reply.error || "Could not start sign-in";
    });
  }

  function setArea(group, on) {
    if (!service || !connected) { showNotice("Offline — reconnecting"); return; }
    var controllable = 0;
    for (var i = 0; i < group.lights.length; i++)
      if (group.lights[i].state !== "unavailable") controllable++;
    if (!controllable) {
      showNotice("No reachable lights in " + group.name + " — check wall switches or the Hue bridge");
      return;
    }
    service.setAreaAll(group.id, on, function(reply) {
      if (!reply.ok) root.showNotice("Command failed: " + (reply.error || "unknown"));
    });
  }

  focusTarget: keys
  contentWidth: fittedContentWidth(Style.space(420))
  contentHeight: fittedContentHeight(column.implicitHeight)

  onOpenChanged: {
    if (open) selected = -1;
    else { setupError = ""; httpAcknowledged = false; mode = "main"; expandedEntity = ""; }
  }
  onLightRowsChanged: if (selected >= lightRows.length) selected = lightRows.length - 1
  onStatusChanged: if (status !== "setup") { setupError = ""; }

  Ui.PanelKeyCatcher {
    id: keys
    anchors.fill: parent
    blocked: urlField.activeFocus && root.status === "setup"
    onMoveRequested: function(dx, dy) {
      if (root.mode !== "main") return;
      var focused = root.focusedItem();
      if (dx && focused && "adjust" in focused) {
        focused.adjust(dx * focused.step);
        return;
      }
      var current = root.lightRows[root.selected];
      if (dx && current && current.light.supports_brightness) {
        var percent = current.light.state === "on" ? Math.round((current.light.brightness || 0) / 255 * 100) : 0;
        root.setLightSettings(current.light, {brightness: Math.round(Math.max(0, Math.min(100, percent + dx * 5)) / 100 * 255)});
        return;
      }
      var delta = dy || dx;
      if (!delta) return;
      var count = root.lightRows.length;
      if (!count) return;
      if (root.selected < 0) {
        // Nothing selected yet: start at the end we moved toward.
        root.selected = delta > 0 ? 0 : count - 1;
        return;
      }
      root.selected = (root.selected + delta + count) % count;
      if (dy) keys.forceActiveFocus(Qt.OtherFocusReason);
    }
    onActivateRequested: {
      if (root.mode !== "main") return;
      var focused = root.focusedItem();
      if (focused !== keys) {
        if ("activate" in focused) focused.activate();
        return;
      }
      var row = root.lightRows[root.selected];
      if (row) root.toggleLight(row.light);
    }
    onCloseRequested: root.close()
    onTabRequested: function(direction) {
      var focused = root.focusedItem();
      var next = focused.nextItemInFocusChain(direction > 0);
      if (next) next.forceActiveFocus(Qt.TabFocusReason);
    }
    onTextKey: function(text) {
      var row = root.lightRows[root.selected];
      if (root.mode === "main" && text.toLowerCase() === "c" && row)
        root.expandedEntity = root.expandedEntity === row.light.entity_id ? "" : row.light.entity_id;
    }

    Column {
      id: column
      width: parent.width
      spacing: Style.space(10)

      // Non-visual: lives inside the content column because the panel's
      // default child property accepts Items only.
      Timer {
        id: noticeReset
        interval: 4000
        onTriggered: root.notice = ""
      }
      Connections {
        target: root.service
        function onCommandNotice(message) { root.showNotice(message); }
      }

      // ---- Header: title + connection status ----
      Item {
        width: parent.width
        implicitHeight: headerTitle.implicitHeight

        Text {
          id: headerTitle
          anchors.left: parent.left
          text: "Home Assistant"
          textFormat: Text.PlainText
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
        }
        Text {
          anchors.right: parent.right
          anchors.baseline: headerTitle.baseline
          text: {
            if (!root.service) return "";
            if (root.connected) return (root.service.serverVersion || "") + " · connected";
            return root.service.statusLabel().replace("Home Assistant", "").replace(/^ · /, "");
          }
          textFormat: Text.PlainText
          color: root.connected ? Color.muted : Color.urgent
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          font.bold: !root.connected
        }
      }

      // ---- Onboarding: URL ----
      Column {
        width: parent.width
        spacing: Style.space(8)
        visible: root.mode === "main" && (root.status === "setup" || root.status === "validating")

        Text {
          width: parent.width
          text: "1 · Server address"
          textFormat: Text.PlainText
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          font.bold: true
        }
        Ui.TextField {
          id: urlField
          width: parent.width
          text: root.service && root.service.serverUrl !== "" ? root.service.serverUrl : root.urlInput
          placeholderText: "http://homeassistant.local:8123"
          onTextChanged: if (activeFocus) root.urlInput = text
          onAccepted: root.submitUrl()
          Keys.onPressed: function(event) {
            if (event.key === Qt.Key_Escape) { root.close(); event.accepted = true; }
          }
        }
        Text {
          width: parent.width
          visible: root.status === "validating"
          text: "Checking server…"
          textFormat: Text.PlainText
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
        }
        // Explicit unencrypted-transport warning for plain HTTP (plan §3).
        Text {
          width: parent.width
          visible: root.service && root.service.serverUrl !== ""
              && root.service.serverHttp && !root.httpAcknowledged
              && root.status === "setup"
          text: "This server uses plain HTTP. Credentials would travel unencrypted — only continue on a trusted LAN."
          textFormat: Text.PlainText
          color: Color.urgent
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.Wrap
        }
        Rectangle {
          width: parent.width
          height: httpAckLabel.implicitHeight + Style.space(12)
          radius: Style.cornerRadius
          visible: root.service && root.service.serverUrl !== ""
              && root.service.serverHttp && !root.httpAcknowledged
              && root.status === "setup"
          color: Style.hoverFillFor(Color.foreground, Color.accent)

          Text {
            id: httpAckLabel
            anchors.centerIn: parent
            text: "I understand — continue on trusted LAN"
            textFormat: Text.PlainText
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.httpAcknowledged = true
          }
        }
        Rectangle {
          width: parent.width
          height: continueLabel.implicitHeight + Style.space(12)
          radius: Style.cornerRadius
          visible: root.status === "setup"
              && (!root.service || !root.service.serverHttp || root.httpAcknowledged)
              && root.service && root.service.serverUrl !== ""
          color: Style.hoverFillFor(Color.foreground, Color.accent)

          Text {
            id: continueLabel
            anchors.centerIn: parent
            text: "Continue"
            textFormat: Text.PlainText
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            font.bold: true
          }
          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.submitUrl()
          }
        }
      }

      // ---- Onboarding: browser sign-in ----
      Column {
        width: parent.width
        spacing: Style.space(8)
        visible: root.mode === "main" && root.status === "setup" && root.service && root.service.serverUrl !== ""
            && (!root.service.serverHttp || root.httpAcknowledged)

        Text {
          width: parent.width
          text: "2 · Sign in"
          textFormat: Text.PlainText
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          font.bold: true
        }
        Text {
          width: parent.width
          text: "Sign-in happens in your browser, on Home Assistant's own page. This app never sees your password."
          textFormat: Text.PlainText
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.Wrap
        }
        Rectangle {
          width: parent.width
          height: browserLabel.implicitHeight + Style.space(12)
          radius: Style.cornerRadius
          color: Style.hoverFillFor(Color.foreground, Color.accent)

          Text {
            id: browserLabel
            anchors.centerIn: parent
            text: "Continue in browser"
            textFormat: Text.PlainText
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.body
            font.bold: true
          }
          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.startBrowser()
          }
        }
      }

      // ---- Onboarding: waiting for browser ----
      Column {
        width: parent.width
        spacing: Style.space(8)
        visible: root.mode === "main" && root.status === "authorizing"

        Text {
          width: parent.width
          text: "Finish the sign-in in your browser window…"
          textFormat: Text.PlainText
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.Wrap
        }
        Text {
          width: parent.width
          text: "Close this popup to keep waiting, or cancel and try again."
          textFormat: Text.PlainText
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          wrapMode: Text.Wrap
        }
        Rectangle {
          width: parent.width
          height: cancelLabel.implicitHeight + Style.space(12)
          radius: Style.cornerRadius
          color: Style.hoverFillFor(Color.foreground, Color.accent)

          Text {
            id: cancelLabel
            anchors.centerIn: parent
            text: "Cancel sign-in"
            textFormat: Text.PlainText
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: if (root.service) root.service.setupCancel()
          }
        }
      }

      // ---- Keyring locked / credentials invalid ----
      Column {
        width: parent.width
        spacing: Style.space(8)
        visible: root.mode === "main" && (root.status === "keyring_locked" || root.status === "credentials_invalid")

        Text {
          width: parent.width
          text: root.status === "keyring_locked"
            ? "The password keyring is unavailable. Unlock it (e.g. log in again) and retry."
            : "The saved session expired or was revoked."
          textFormat: Text.PlainText
          color: Color.urgent
          font.family: Style.font.family
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.Wrap
        }
        Rectangle {
          width: parent.width
          height: retryLabel.implicitHeight + Style.space(12)
          radius: Style.cornerRadius
          color: Style.hoverFillFor(Color.foreground, Color.accent)

          Text {
            id: retryLabel
            anchors.centerIn: parent
            text: root.status === "keyring_locked" ? "Unlock keyring" : "Sign in again"
            textFormat: Text.PlainText
            color: Color.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.bodySmall
          }
          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: {
              if (!root.service) return;
              if (root.status === "keyring_locked") root.service.retryKeyring();
              else root.startBrowser();
            }
          }
        }
      }

      // ---- Offline note ----
      Text {
        width: parent.width
        visible: root.mode === "main" && root.status === "offline"
        text: root.service ? (root.service.detail || "Controller offline") + " — controls disabled" : ""
        textFormat: Text.PlainText
        color: Color.urgent
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        wrapMode: Text.Wrap
      }

      Text {
        width: parent.width
        visible: root.setupError !== ""
        text: root.setupError
        textFormat: Text.PlainText
        color: Color.urgent
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        wrapMode: Text.Wrap
      }

      Text {
        width: parent.width
        visible: root.notice !== ""
        text: root.notice
        textFormat: Text.PlainText
        color: Color.urgent
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        wrapMode: Text.Wrap
      }

      // ---- Availability filter ----
      Row {
        width: parent.width
        spacing: Style.space(4)
        visible: root.mode === "main" && root.service && root.service.setupDone
            && root.service.lights.length > 0

        Repeater {
          model: [
            { value: "all", label: "All", count: root.service ? root.service.lights.length : 0 },
            { value: "available", label: "Available", count: root.availableCount },
            { value: "unavailable", label: "Unavailable", count: root.unavailableCount }
          ]

          Rectangle {
            id: availabilityButton
            required property var modelData
            readonly property bool active: root.availabilityFilter === modelData.value
            width: availabilityLabel.implicitWidth + Style.space(14)
            height: availabilityLabel.implicitHeight + Style.space(8)
            radius: Style.cornerRadius
            color: active
              ? Style.selectedFillFor(Color.foreground, Color.accent)
              : availabilityMouse.containsMouse
                ? Style.hoverFillFor(Color.foreground, Color.accent) : "transparent"

            Text {
              id: availabilityLabel
              anchors.centerIn: parent
              text: availabilityButton.modelData.label + " " + availabilityButton.modelData.count
              textFormat: Text.PlainText
              color: availabilityButton.active ? Color.accent : Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
              font.bold: availabilityButton.active
            }

            MouseArea {
              id: availabilityMouse
              anchors.fill: parent
              hoverEnabled: true
              cursorShape: Qt.PointingHandCursor
              onClicked: root.setAvailabilityFilter(availabilityButton.modelData.value)
            }
          }
        }
      }

      // ---- Lights (connected session) ----
      Flickable {
        id: lightsScroll
        width: parent.width
        height: Math.min(lightsColumn.implicitHeight, Style.space(480),
          Math.max(Style.space(100), root.availableCardHeight - root.verticalContentInset - Style.space(150)))
        contentWidth: width
        contentHeight: lightsColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        visible: root.mode === "main" && root.groups.length > 0

        Column {
          id: lightsColumn
          width: parent.width
          spacing: Style.space(8)

        Repeater {
           model: root.groups.length

          Column {
            id: areaSection
            required property int index
            readonly property var modelData: root.groups[index]
            width: parent.width
            spacing: Style.space(8)

            Item {
              width: parent.width
              implicitHeight: Math.max(areaLabel.implicitHeight, areaControls.implicitHeight)

              Text {
                id: areaLabel
                anchors.left: parent.left
                anchors.right: areaControls.left
                anchors.rightMargin: Style.space(8)
                elide: Text.ElideRight
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.name + "   " + modelData.onCount + "/" + modelData.lights.length + " on"
                textFormat: Text.PlainText
                color: Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.bodySmall
                font.bold: true
              }

              Row {
                id: areaControls
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(4)

                Rectangle {
                  width: allOnText.implicitWidth + Style.space(12)
                  height: allOnText.implicitHeight + Style.space(6)
                  radius: Style.cornerRadius
                  color: Style.hoverFillFor(Color.foreground, Color.accent)
                  visible: root.availabilityFilter !== "unavailable"
                      && modelData.onCount < modelData.lights.length

                  Text {
                    id: allOnText
                    anchors.centerIn: parent
                    text: "All on"
                    textFormat: Text.PlainText
                    color: Color.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                  MouseArea {
                    anchors.fill: parent
                    enabled: root.connected
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.setArea(areaSection.modelData, true)
                  }
                }
                Rectangle {
                  width: allOffText.implicitWidth + Style.space(12)
                  height: allOffText.implicitHeight + Style.space(6)
                  radius: Style.cornerRadius
                  color: Style.hoverFillFor(Color.foreground, Color.accent)
                  visible: root.availabilityFilter !== "unavailable" && modelData.onCount > 0

                  Text {
                    id: allOffText
                    anchors.centerIn: parent
                    text: "All off"
                    textFormat: Text.PlainText
                    color: Color.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                  MouseArea {
                    anchors.fill: parent
                    enabled: root.connected
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.setArea(areaSection.modelData, false)
                  }
                }
              }
            }

            Repeater {
              model: areaSection.modelData.lights.length

              LightCard {
                id: lightRow
                required property int index
                light: areaSection.modelData.lights[index]
                pending: root.pendingFor(light.entity_id)
                connected: root.connected
                selected: root.selected >= 0
                    && !!root.lightRows[root.selected]
                    && root.lightRows[root.selected].light.entity_id === light.entity_id
                expanded: root.expandedEntity === light.entity_id
                width: parent.width
                onPowerRequested: root.toggleLight(light)
                onSettingsRequested: function(settings) { root.setLightSettings(light, settings); }
                onExpandRequested: root.expandedEntity = expanded ? "" : light.entity_id
                onInteracted: {
                  for (var i = 0; i < root.lightRows.length; i++)
                    if (root.lightRows[i].light.entity_id === light.entity_id) root.selected = i;
                }
                function reveal() {
                  if (!selected) return;
                  var top = mapToItem(lightsColumn, 0, 0).y;
                  if (top < lightsScroll.contentY) lightsScroll.contentY = top;
                  else if (top + height > lightsScroll.contentY + lightsScroll.height)
                    lightsScroll.contentY = Math.min(top, top + height - lightsScroll.height);
                }
                onSelectedChanged: if (selected) Qt.callLater(reveal)
                onExpandedChanged: Qt.callLater(reveal)
              }
            }
          }
        }
      }

      }

      Text {
        width: parent.width
        visible: root.mode === "main" && root.connected && root.groups.length === 0
        text: root.availabilityFilter === "all"
          ? "No lights to show. Use Choose lights to update your selection."
          : root.availabilityFilter === "available"
            ? "No available lights."
            : "No unavailable lights."
        textFormat: Text.PlainText
        color: Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
        wrapMode: Text.Wrap
      }

      // ---- Picker: choose lights ---------------------------------------
      Column {
        width: parent.width
        spacing: Style.space(8)
        visible: root.mode === "picker"

        Text {
          width: parent.width
          text: "Choose lights"
          textFormat: Text.PlainText
          color: Color.foreground
          font.family: Style.font.family
          font.pixelSize: Style.font.body
          font.bold: true
        }
        Text {
          width: parent.width
          text: "Select whole areas or individual lights. Nothing selected shows everything."
          textFormat: Text.PlainText
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          wrapMode: Text.Wrap
        }

        Flickable {
          width: parent.width
          height: Math.min(pickerList.implicitHeight, Style.space(280))
          contentWidth: width
          contentHeight: pickerList.implicitHeight
          clip: true
          boundsBehavior: Flickable.StopAtBounds

          Column {
            id: pickerList
            width: parent.width
            spacing: Style.space(2)

            Repeater {
              model: root.pickerAreas

              delegate: Column {
                id: pickerAreaSection
                required property var modelData
                width: parent.width
                spacing: Style.space(2)

                // Area header with checkbox.
                Rectangle {
                  width: parent.width
                  height: pickerAreaCheck.implicitHeight + Style.space(10)
                  radius: Style.cornerRadius
                  color: pickerAreaMouse.containsMouse ? Style.hoverFillFor(Color.foreground, Color.accent) : "transparent"

                  Row {
                    anchors.fill: parent
                    anchors.leftMargin: Style.space(4)
                    anchors.rightMargin: Style.space(4)
                    spacing: Style.space(8)

                    Text {
                      id: pickerAreaCheck
                      text: root.selAreas[modelData.id] ? "\uF046" : "\uF096"
                      textFormat: Text.PlainText
                      color: root.selAreas[modelData.id] ? Color.accent : Color.muted
                      font.family: Style.font.family
                      font.pixelSize: Style.font.body
                      anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                      text: modelData.name
                      textFormat: Text.PlainText
                      color: Color.foreground
                      font.family: Style.font.family
                      font.pixelSize: Style.font.bodySmall
                      font.bold: true
                      anchors.verticalCenter: parent.verticalCenter
                    }
                  }
                  MouseArea {
                    id: pickerAreaMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.toggleDraftArea(modelData.id)
                  }
                }

                Repeater {
                  model: pickerAreaSection.modelData.id !== undefined ? root.lightsOfArea(pickerAreaSection.modelData.id) : []

                  delegate: Rectangle {
                    id: pickerLightRow
                    required property var modelData
                    width: pickerList.width - Style.space(16)
                    anchors.left: parent.left
                    anchors.leftMargin: Style.space(16)
                    height: pickerLightCheck.implicitHeight + Style.space(8)
                    radius: Style.cornerRadius
                    color: pickerLightMouse.containsMouse ? Style.hoverFillFor(Color.foreground, Color.accent) : "transparent"

                    Row {
                      anchors.fill: parent
                      anchors.leftMargin: Style.space(4)
                      spacing: Style.space(8)

                      Text {
                        id: pickerLightCheck
                        text: root.selLights[modelData.entity_id] ? "\uF046" : "\uF096"
                        textFormat: Text.PlainText
                        color: root.selLights[modelData.entity_id] ? Color.accent : Color.muted
                        font.family: Style.font.family
                        font.pixelSize: Style.font.body
                        anchors.verticalCenter: parent.verticalCenter
                      }
                      Text {
                        text: pickerLightRow.modelData.name
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        width: pickerList.width - Style.space(80)
                        color: Color.foreground
                        font.family: Style.font.family
                        font.pixelSize: Style.font.bodySmall
                        anchors.verticalCenter: parent.verticalCenter
                      }
                    }
                    MouseArea {
                      id: pickerLightMouse
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.toggleDraftLight(pickerLightRow.modelData.entity_id)
                    }
                  }
                }
              }
            }

            Repeater {
              // Unassigned bucket: lights without an area.
              model: root.pickerLights.filter(function(l) { return !l.area; })

              delegate: Rectangle {
                id: unassignedRow
                required property var modelData
                width: pickerList.width
                height: unassignedCheck.implicitHeight + Style.space(8)
                radius: Style.cornerRadius
                color: unassignedMouse.containsMouse ? Style.hoverFillFor(Color.foreground, Color.accent) : "transparent"

                Row {
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(4)
                  spacing: Style.space(8)

                  Text {
                    id: unassignedCheck
                    text: root.selLights[unassignedRow.modelData.entity_id] ? "\uF046" : "\uF096"
                    textFormat: Text.PlainText
                    color: root.selLights[unassignedRow.modelData.entity_id] ? Color.accent : Color.muted
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                    anchors.verticalCenter: parent.verticalCenter
                  }
                  Text {
                    text: unassignedRow.modelData.name + "   (Unassigned)"
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    width: pickerList.width - Style.space(64)
                    color: Color.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.bodySmall
                    anchors.verticalCenter: parent.verticalCenter
                  }
                }
                MouseArea {
                  id: unassignedMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.toggleDraftLight(unassignedRow.modelData.entity_id)
                }
              }
            }
          }
        }

        Text {
          width: parent.width
          visible: root.pickerMessage !== ""
          text: root.pickerMessage
          textFormat: Text.PlainText
          color: Color.urgent
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          wrapMode: Text.Wrap
        }

        // Picker footer: Save / Cancel
        Row {
          width: parent.width
          spacing: Style.space(12)

          Rectangle {
            width: saveLabel.implicitWidth + Style.space(16)
            height: saveLabel.implicitHeight + Style.space(10)
            radius: Style.cornerRadius
            color: Style.hoverFillFor(Color.foreground, Color.accent)
            Text {
              id: saveLabel
              anchors.centerIn: parent
              text: "Save"
              textFormat: Text.PlainText
              color: Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
              font.bold: true
            }
            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.savePicker()
            }
          }
          Text {
            text: "Cancel"
            textFormat: Text.PlainText
            color: Color.muted
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            anchors.verticalCenter: parent.verticalCenter
            MouseArea {
              anchors.fill: parent
              cursorShape: Qt.PointingHandCursor
              onClicked: root.mode = "main"
            }
          }
        }
      }

      Ui.PanelSeparator { visible: root.mode === "main"; width: parent.width }

      // ---- Footer actions ----
      Row {
        width: parent.width
        spacing: Style.space(12)
        visible: root.mode === "main"

        Text {
          text: "Open HA"
          textFormat: Text.PlainText
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          MouseArea {
            anchors.fill: parent
            enabled: !!root.service && root.service.serverUrl !== ""
            cursorShape: Qt.PointingHandCursor
            onClicked: if (root.service) Quickshell.execDetached(["omarchy-launch-browser", root.service.serverUrl])
          }
        }
        Text {
          text: "Choose lights"
          textFormat: Text.PlainText
          color: Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          visible: root.service && root.service.setupDone
          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.openPicker()
          }
        }
        Text {
          id: signoutButton
          property bool armed: false
          text: armed ? "Sign out — click again to confirm" : "Sign out"
          textFormat: Text.PlainText
          color: armed ? Color.urgent : Color.muted
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
          visible: root.service && root.service.setupDone
          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: {
              if (!root.service) return;
              if (signoutButton.armed) {
                signoutButton.armed = false;
                root.service.signOut();
              } else {
                signoutButton.armed = true;
                signoutReset.restart();
              }
            }
          }
          Timer {
            id: signoutReset
            interval: 4000
            onTriggered: signoutButton.armed = false
          }
        }
      }
    }
  }
}
