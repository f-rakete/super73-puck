// SUPER73 BLE helpers for the Puck.js REPL (Phase 1: replay the settings write).
// Untested: written before the Puck arrived. Protocol: docs/protocol.md.
//
// Send to RAM from the Espruino Web IDE, then in the left-hand console:
//   bike.find()        scan 10 s, remember the bike (copy the printed id into BIKE_ID)
//   bike.read()        connect, print SETTINGS, disconnect
//   bike.setLight(1)   safest first write test: headlight on (0 = off)
//   bike.setMode(4)    write mode, keep light/assist, verify, disconnect
//
// LED: green = write verified, red = failed. Every call disconnects at the end
// and gives up after TIMEOUT_MS, so the radio never stays connected.

var BIKE_ID = null; // e.g. "aa:bb:cc:dd:ee:ff public", from bike.find()
var BOND = false;   // set true if the bike rejects reads/writes with an authentication error

var SVC = "00001554-1212-efde-1523-785feabcd123";
var CHR_REGISTER_ID = "00001564-1212-efde-1523-785feabcd123";
var CHR_REGISTER = "0000155f-1212-efde-1523-785feabcd123";
var SETTINGS_ID = [0x03, 0x00];
var LEGAL_MODE = 4; // EU EPAC, 25 km/h / 250 W
var TIMEOUT_MS = 15000;

function flash(led) {
  led.set();
  setTimeout(function () { led.reset(); }, 300);
}

function hex(a) {
  var s = [];
  for (var i = 0; i < a.length; i++) s.push((a[i] < 16 ? "0" : "") + a[i].toString(16));
  return s.join(" ");
}

// Connect, run fn(chrRegisterId, chrRegister) -> Promise, always disconnect.
function session(fn) {
  if (!BIKE_ID) return Promise.reject("BIKE_ID not set, run bike.find() first");
  return new Promise(function (resolve, reject) {
    var gatt, chrId, done = false;
    var timer = setTimeout(function () { finish("timeout"); }, TIMEOUT_MS);
    function finish(err, value) {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (gatt && gatt.connected) gatt.disconnect();
      if (err) { flash(LED1); reject(err); } else resolve(value);
    }
    NRF.connect(BIKE_ID).then(function (g) {
      gatt = g;
      if (done) { gatt.disconnect(); throw "timeout"; }
      return BOND ? gatt.startBonding() : null;
    }).then(function () {
      return gatt.getPrimaryService(SVC);
    }).then(function (svc) {
      return svc.getCharacteristic(CHR_REGISTER_ID).then(function (c) {
        chrId = c;
        return svc.getCharacteristic(CHR_REGISTER);
      });
    }).then(function (chrReg) {
      return fn(chrId, chrReg);
    }).then(function (v) { finish(null, v); }, function (e) { finish(e || "error"); });
  });
}

function readSettings(chrId, chrReg) {
  return chrId.writeValue(SETTINGS_ID).then(function () {
    return chrReg.readValue();
  }).then(function (dv) {
    var f = new Uint8Array(dv.buffer);
    if (f.length != 10 || f[0] != 0x03 || f[1] != 0x00) throw "unexpected frame " + hex(f);
    return { assist: f[2], light: f[4], mode: f[5], raw: hex(f) };
  });
}

// change: any of {light, assist, mode}; the rest is copied from the bike.
function writeSettings(change) {
  return session(function (chrId, chrReg) {
    var want;
    return readSettings(chrId, chrReg).then(function (cur) {
      print("Current " + cur.raw);
      want = { light: cur.light, assist: cur.assist, mode: cur.mode };
      for (var k in change) want[k] = change[k];
      return chrReg.writeValue([0x00, 0xD1, want.light, want.assist, want.mode, 0, 0, 0, 0, 0]);
    }).then(function () {
      return new Promise(function (r) { setTimeout(r, 300); });
    }).then(function () {
      return readSettings(chrId, chrReg);
    }).then(function (now) {
      print("Now     " + now.raw);
      if (now.light != want.light || now.assist != want.assist || now.mode != want.mode) throw "not applied";
      flash(LED2);
      return now;
    });
  });
}

function report(e) { print("Failed: " + e); }

var bike = {
  find: function () {
    return NRF.requestDevice({
      filters: [{ services: [SVC] }, { namePrefix: "SUPER73" }, { namePrefix: "Super73" }],
      active: true,
      timeout: 10000
    }).then(function (dev) {
      BIKE_ID = dev.id;
      print("Found " + dev.name + " at " + dev.id);
    }).catch(report);
  },
  read: function () {
    return session(readSettings).then(function (s) {
      print(s.raw + "  mode " + s.mode + ", assist " + s.assist + ", light " + s.light);
    }).catch(report);
  },
  setLight: function (on) {
    return writeSettings({ light: on ? 1 : 0 }).catch(report);
  },
  setMode: function (mode, allowUnrestricted) {
    if (!(mode >= 0 && mode <= 7)) return Promise.reject("mode must be 0-7").catch(report);
    if (mode != LEGAL_MODE && !allowUnrestricted) {
      return Promise.reject("mode " + mode + " is above the Austrian pedelec limit, use bike.setMode(" + mode + ", true)").catch(report);
    }
    return writeSettings({ mode: mode }).catch(report);
  }
};
