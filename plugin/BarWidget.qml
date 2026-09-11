import QtQuick
import qs.Ui as Ui
import qs.Commons
import "lib/HaModel.js" as Ha

Ui.Panel {
  id: root
  moduleName: "iulian.home-assistant"
  manageIpc: false

  readonly property var service: bar?.shell?.serviceFor("iulian.home-assistant") ?? null
  property var registeredService: null

  function syncService() {
    if (registeredService !== service) {
      if (registeredService) registeredService.unregisterConsumer();
      registeredService = service;
      if (registeredService) {
        registeredService.configure(settings);
        registeredService.registerConsumer();
      }
    } else if (service) service.configure(settings);
  }
  onServiceChanged: syncService()
  onSettingsChanged: syncService()
  Component.onCompleted: syncService()
  Component.onDestruction: if (registeredService) registeredService.unregisterConsumer()

  // IPC summon routing: the service relays panel requests to whichever live
  // widget instance exists (first instance wins, like bar.broadcast).
  function handlePanelRequest(request) {
    if (request === "picker") {
      root.open();
      if (panel.openPicker) panel.openPicker();
      return;
    }
    if (request === true) root.open();
    else if (request === false) root.close();
    else root.toggle();
  }
  Connections {
    target: root.service
    function onPanelRequested(request) { root.handlePanelRequest(request) }
  }

  readonly property int lightsOn: service ? service.lightsOn : 0
  readonly property bool connected: service ? service.connected : false

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Ui.BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // [home icon] — accent while any light is on (count removed by user preference).
    text: "\uF015"
    slotSize: Style.bar.statusSlot
    active: root.lightsOn > 0
    dimmed: root.service && !root.connected
    tooltipText: root.service ? root.service.tooltip : "Home Assistant"
    onPressed: function(code) {
      if (code === Qt.RightButton) root.toggle();
      else root.toggle();
    }
  }

  Panel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    service: root.service
  }
}
