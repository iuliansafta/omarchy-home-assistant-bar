import QtQuick
import Quickshell
import Quickshell.Io
import "lib/HaModel.js" as Ha

// Service: owns the Unix-socket connection to the Python controller
// (systemd user service omarchy-home-assistant). One instance per shell,
// independent of popup visibility. No credentials, tokens, or secrets ever
// live here — the controller owns the keyring and the HA session entirely.
Item {
  id: root

  property var shell: null
  property int consumerCount: 0
  property var config: ({})

  // Controller session state: setup | validating | authorizing | connected |
  // offline | keyring_locked | credentials_invalid
  property string status: "starting"
  property string detail: ""
  property string serverVersion: ""
  property string serverUrl: ""
  property bool serverHttp: false
  property bool setupDone: false           // true once setup_complete

  // Registry state (replaced wholesale by each snapshot).
  property var areas: []
  property var lights: []
  property int seq: 0

  // Pending service calls: entity_id -> {target, until}. Cleared when an observed
  // state change confirms it or the safety timer expires.
  property var pending: ({})

  readonly property int lightsOn: Ha.countOn(lights)
  readonly property bool connected: status === "connected"
  readonly property bool controlsEnabled: connected
  readonly property bool inSetup: ["setup", "validating", "authorizing", "keyring_locked", "credentials_invalid"].indexOf(status) >= 0
  readonly property string tooltip: statusLabel()
  signal commandNotice(string message)
  onConnectedChanged: if (!connected) pending = ({})

  readonly property string socketPath: Quickshell.env("OMARCHY_HA_SOCKET")
      || (Quickshell.env("XDG_RUNTIME_DIR") || "") + "/omarchy-ha/controller.sock"

  function configure(settings) {
    config = settings || {};
  }

  function registerConsumer() { consumerCount++; if (consumerCount === 1) connect(); }
  function unregisterConsumer() {
    consumerCount = Math.max(0, consumerCount - 1);
    if (!consumerCount) disconnect();
  }

  function statusLabel() {
    if (status === "connected") {
      var label = "Home Assistant" + (serverVersion ? " · " + serverVersion : "");
      label += " · " + lightsOn + " light" + (lightsOn === 1 ? "" : "s") + " on";
      return label;
    }
    if (status === "validating") return "Home Assistant · checking server…";
    if (status === "authorizing") return "Home Assistant · waiting for sign-in";
    if (status === "keyring_locked") return "Home Assistant · keyring locked";
    if (status === "credentials_invalid") return "Home Assistant · session expired";
    if (status === "offline") return "Home Assistant · offline";
    if (status === "setup") return "Home Assistant · not set up";
    return "Home Assistant · " + (detail || status);
  }

  // ---- connection to the controller -------------------------------------
  property int backoffMs: 1000
  readonly property int backoffCapMs: 30000

  // Quickshell's Socket only creates a new QLocalSocket while it holds none, and
  // a failed connect never clears it (`disconnected` is emitted only for an
  // established connection). Re-assigning `connected = true` after a failure is
  // therefore a no-op, which wedged the widget at "connecting" until the shell
  // restarted. Recreate the element for every attempt instead.
  property var sock: null

  Component {
    id: socketComponent
    Socket {
      path: root.socketPath
      parser: SplitParser {
        onRead: function(data) { root.handleLine(data) }
      }
      onConnectedChanged: {
        if (connected) root.onSocketConnected();
        else root.onSocketDisconnected();
      }
      onError: function(err) {
        // QLocalSocket::ServerNotFoundError etc. — generic on purpose.
        root.detail = "Controller socket unavailable";
        root.scheduleReconnect();
      }
    }
  }

  function openSocket() {
    if (sock) { sock.destroy(); sock = null; }
    sock = socketComponent.createObject(root);
    return sock;
  }

  function connect() {
    status = "connecting";
    detail = "Connecting to controller";
    var fresh = openSocket();
    if (fresh) fresh.connected = true;
  }

  function disconnect() {
    reconnect.stop();
    if (sock) { sock.connected = false; sock.destroy(); sock = null; }
    status = "offline";
    detail = "No controller";
  }

  function scheduleReconnect() {
    if (!consumerCount) return;
    var jitter = Math.floor(Math.random() * 500);
    backoffMs = Math.min(backoffMs * 2, backoffCapMs);
    reconnect.interval = backoffMs + jitter;
    reconnect.restart();
  }

  function onSocketConnected() {
    backoffMs = 1000;
    reconnect.stop();
    // Snapshot push arrives immediately; status comes from it.
  }

  function onSocketDisconnected() {
    status = "offline";
    detail = "Controller offline";
    pending = {};
    var abandoned = callbacks;
    callbacks = {};
    for (var id in abandoned)
      abandoned[id].done({ok: false, error: "controller disconnected"});
    scheduleReconnect();
  }

  Timer {
    id: reconnect
    onTriggered: if (root.consumerCount && (!sock || !sock.connected)) root.connect()
  }

  // No retries: expired commands fall back to observed state, never replay.
  Timer {
    interval: 1000; repeat: true; running: root.consumerCount > 0
    onTriggered: {
      var changed = false;
      var next = {};
      var now = Date.now();
      for (var id in root.pending) {
        var item = root.pending[id];
        if (item && item.until > now) next[id] = item;
        else {
          changed = true;
          root.commandNotice("Light did not confirm the requested setting; showing its reported state.");
        }
      }
      if (changed) root.pending = next;
      for (var requestId in root.callbacks) {
        var callback = root.callbacks[requestId];
        if (callback.until > now) continue;
        delete root.callbacks[requestId];
        callback.done({ok: false, error: "controller request timed out"});
      }
    }
  }

  // ---- protocol ---------------------------------------------------------
  property int nextId: 1
  property var callbacks: ({})

  function send(msg, done) {
    if (!sock || !sock.connected) {
      if (done) done({ ok: false, error: "controller unreachable" });
      return;
    }
    var id = nextId++;
    msg.id = id;
    if (done) callbacks[id] = {done: done, until: Date.now() + 15000};
    sock.write(JSON.stringify(msg) + "\n");
  }

  function applyStatus(msg) {
    status = msg.status || status;
    detail = msg.detail || "";
    seq = msg.seq !== undefined ? msg.seq : seq + 1;
  }

  function applySnapshot(msg) {
    status = msg.status || "offline";
    detail = msg.detail || "";
    serverUrl = (msg.server && msg.server.url) || "";
    serverVersion = (msg.server && msg.server.version) || "";
    serverHttp = !!(msg.server && msg.server.http);
    areas = (msg.areas || []).slice();
    lights = (msg.lights || []).slice();
    reconcilePending();
    seq = msg.seq !== undefined ? msg.seq : seq + 1;
    setupDone = status === "connected";
  }

  function applyUpdate(msg) {
    if (!msg.changes) return;
    var updated = lights.slice();
    for (var c = 0; c < msg.changes.length; c++) {
      var change = msg.changes[c];
      if (!change || !change.entity_id) continue;
      if (change.state === "removed") {
        var kept = [];
        for (var r = 0; r < updated.length; r++)
          if (updated[r].entity_id !== change.entity_id) kept.push(updated[r]);
        updated = kept;
      } else {
        for (var i = 0; i < updated.length; i++) {
          if (updated[i] && updated[i].entity_id === change.entity_id) {
            var next = JSON.parse(JSON.stringify(updated[i]));
            for (var key in change) next[key] = change[key];
            updated[i] = next;
            break;
          }
        }
      }
    }
    lights = updated;
    reconcilePending();
    seq = msg.seq !== undefined ? msg.seq : seq + 1;
  }

  function reconcilePending() {
    var next = {};
    for (var i = 0; i < lights.length; i++) {
      var light = lights[i];
      var item = pending[light.entity_id];
      if (item && light.state !== "unavailable" && !Ha.matchesTarget(light, item.target))
        next[light.entity_id] = item;
    }
    pending = next;
  }

  function handleLine(data) {
    var msg;
    try { msg = JSON.parse(data); } catch (e) { return; }
    if (msg.type === "snapshot") applySnapshot(msg);
    else if (msg.type === "status") applyStatus(msg);
    else if (msg.type === "update") applyUpdate(msg);
    else if (msg.type === "reply") {
      var done = callbacks[msg.id];
      delete callbacks[msg.id];
      if (done) done.done({ ok: !!msg.ok, error: msg.error || null, extra: msg });
    }
  }

  // ---- onboarding / session commands -------------------------------------

  function setupUrl(url, done) { send({ cmd: "setup.url", url: url }, done) }
  function setupBrowser(done) { send({ cmd: "setup.browser" }, done) }
  function setupCancel() { send({ cmd: "setup.cancel" }, null) }
  function signOut() { send({ cmd: "signout" }, null) }
  function retryKeyring() { send({ cmd: "retry.keyring" }, null) }

  // ---- picker (choose lights) -------------------------------------------
  function pickerData(done) { send({ cmd: "picker.data" }, done) }
  function pickerSave(areas, entities, done) {
    send({ cmd: "picker.save", areas: areas, entities: entities }, done)
  }

  // Explicit desired state (plan §4: never toggle — no stale-state inversions).
  function setLight(entityId, on, done) {
    setLightSettings(entityId, {on: on}, done);
  }

  function setLightSettings(entityId, settings, done) {
    if (!Ha.validEntityId(entityId)) {
      if (done) done({ ok: false, error: "invalid entity_id" });
      return;
    }
    if (!root.connected || !sock || !sock.connected) {
      if (done) done({ok: false, error: "offline"});
      return;
    }
    if (pending[entityId]) {
      if (done) done({ok: false, error: "wait for the current light setting"});
      return;
    }
    var target = {on: settings.on !== undefined ? settings.on : true};
    for (var key in settings) target[key] = settings[key];
    var msg = {cmd: "light.set", entity_id: entityId};
    for (var field in target) msg[field] = target[field];
    if (target.brightness === 0) target.on = false;
    var light = lights.find(function(item) { return item.entity_id === entityId; });
    if (!light || light.state === "unavailable") {
      if (done) done({ok: false, error: "light unavailable"});
      return;
    }
    if (Ha.matchesTarget(light, target)) {
      if (done) done({ok: true});
      return;
    }
    var entry = {target: target, until: Date.now() + 10000};
    var next = {};
    for (var id in pending) next[id] = pending[id];
    next[entityId] = entry;
    pending = next;
    send(msg, function(reply) {
      if (done) done(reply);
      if (!reply.ok && pending[entityId] === entry) {
        var next = {};
        for (var id in pending) if (id !== entityId) next[id] = pending[id];
        pending = next;
      }
    });
  }

  function setAreaAll(areaId, on, done) {
    if (!root.connected) return;
    // Do not overlap a group action with a slider command still in flight.
    for (var i = 0; i < lights.length; i++) {
      var light = lights[i];
      if (light.area === areaId && pending[light.entity_id]) {
        if (done) done({ok: false, error: "wait for the current light setting"});
        return;
      }
    }
    var next = {}, entries = {};
    for (var id in pending) next[id] = pending[id];
    for (var j = 0; j < lights.length; j++) {
      var item = lights[j];
      if (item.area !== areaId || item.state === "unavailable" || Ha.matchesTarget(item, {on: on})) continue;
      entries[item.entity_id] = {target: {on: on}, until: Date.now() + 10000};
      next[item.entity_id] = entries[item.entity_id];
    }
    pending = next;
    send({cmd: "area.all", area_id: areaId, on: on}, function(reply) {
      if (!reply.ok) {
        var rest = {};
        for (var id in pending) if (pending[id] !== entries[id]) rest[id] = pending[id];
        pending = rest;
      }
      if (done) done(reply);
    });
  }

  Component.onDestruction: {
    reconnect.stop();
    if (sock) { sock.connected = false; sock.destroy(); sock = null; }
  }

  // Single IPC surface on the service: widget instances exist per monitor,
  // and an IPC target routes to exactly one handler (apple-music pattern).
  IpcHandler {
    target: "iulian.home-assistant"

    function open(): void { if (root.consumerCount) panelRequest(true) }
    function close(): void { if (root.consumerCount) panelRequest(false) }
    function toggle(): void { if (root.consumerCount) panelRequest(null) }
    function picker(): void { if (root.consumerCount) panelRequest("picker") }
    function status(): string {
      return JSON.stringify({ status: root.status, detail: root.detail,
        serverVersion: root.serverVersion, lightsOn: root.lightsOn,
        lights: root.lights.length, areas: root.areas.length })
    }
  }

  signal panelRequested(var request)
  function panelRequest(request) { panelRequested(request) }
}
