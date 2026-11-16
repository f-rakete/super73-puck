// SUPER73 mode button for Puck.js (Phase 2, first version).
// Protocol: docs/protocol.md. Flash with:  py tools/puck.py --flash src/main.js
//
// Short press: mode 4 (EU EPAC, 25 km/h / 250 W) - the bike's power-on mode.
// Long press (hold until the blue LED lights, >= 1 s): mode 7 (EU OFF-ROAD, unlimited,
// not road legal in Austria).
//
// LEDs: blue while holding = long press armed. Short blinks while working.
// Green = mode 4 set, blue = mode 7 set, 3x red = failed (bike off/out of range/busy).
// Each press connects, bonds (Just Works, stored in flash), reads SETTINGS, writes
// the mode keeping light/assist, verifies, disconnects. Gives up after TIMEOUT_MS,
// retries once.
//
// REPL helpers (py tools/puck.py "..."): bike.log(), bike.read(), bike.setMode(m, allowUnrestricted)
// Connecting to the bike drops a console connection to the Puck, so everything a
// press does is kept in a RAM log: read it afterwards with bike.log().

var BIKE_ID = "f5:14:76:b9:41:ba random"; // our S2, random static address
var DEFAULT_MODE = 4;   // EU EPAC
var UNLIMITED_MODE = 7; // EU OFF-ROAD
var LONG_PRESS_S = 1.0;
var TIMEOUT_MS = 15000;
var RETRIES = 1;

var SVC = "00001554-1212-efde-1523-785feabcd123";
var CHR_REGISTER_ID = "00001564-1212-efde-1523-785feabcd123";
var CHR_REGISTER = "0000155f-1212-efde-1523-785feabcd123";
var SETTINGS_ID = [0x03, 0x00];

var busy = false;
var working;
var holdTimer;
var LOG = [];
var opStart = 0;

function log(s) {
  var line = new Date().toISOString().substr(11, 8) + " +" + (getTime() - opStart).toFixed(1) + "s " + s;
  print(line);
  LOG.push(line);
  if (LOG.length > 60) LOG.shift();
}

function blink(led, n, ms) {
  var i = 0;
  function step() {
    if (i >= n * 2) return;
    digitalWrite(led, (i & 1) ? 0 : 1);
    i++;
    setTimeout(step, ms);
  }
  step();
}

function startWorking(led) {
  working = setInterval(function () {
    led.set();
    setTimeout(function () { led.reset(); }, 20);
  }, 500);
}

function stopWorking() {
  if (working) clearInterval(working);
  working = undefined;
}

function hex(a) {
  var s = [];
  for (var i = 0; i < a.length; i++) s.push((a[i] < 16 ? "0" : "") + a[i].toString(16));
  return s.join(" ");
}

function delay(ms) {
  return new Promise(function (r) { setTimeout(r, ms); });
}

// Connect, bond, run fn(chrRegisterId, chrRegister) -> Promise, always disconnect.
function session(fn) {
  return new Promise(function (resolve, reject) {
    var gatt, chrId, done = false;
    var timer = setTimeout(function () { finish("timeout"); }, TIMEOUT_MS);
    function finish(err, value) {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (gatt && gatt.connected) gatt.disconnect();
      if (err) reject(err); else resolve(value);
    }
    log("connecting");
    NRF.connect(BIKE_ID).then(function (g) {
      gatt = g;
      if (done) { gatt.disconnect(); throw "timeout"; }
      log("connected");
      return gatt.startBonding(false).catch(function (e) {
        log("bonding failed (" + e + "), re-pairing");
        return gatt.startBonding(true);
      });
    }).then(function () {
      log("secure " + JSON.stringify(gatt.getSecurityStatus()));
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

function setMode(mode) {
  return session(function (chrId, chrReg) {
    return readSettings(chrId, chrReg).then(function (cur) {
      log("bike: " + cur.raw);
      if (cur.mode == mode) return cur;
      return chrReg.writeValue([0x00, 0xD1, cur.light, cur.assist, mode, 0, 0, 0, 0, 0]).then(function () {
        return delay(300);
      }).then(function () {
        return readSettings(chrId, chrReg);
      }).then(function (now) {
        log("now:  " + now.raw);
        if (now.mode != mode) throw "mode not applied";
        return now;
      });
    });
  });
}

function attempt(mode, retries) {
  return setMode(mode).catch(function (e) {
    if (retries <= 0) throw e;
    log("retrying after: " + e);
    return attempt(mode, retries - 1);
  });
}

function onPress(isLong) {
  if (busy) { blink(LED1, 1, 80); return; }
  var mode = isLong ? UNLIMITED_MODE : DEFAULT_MODE;
  var led = isLong ? LED3 : LED2;
  busy = true;
  opStart = getTime();
  log((isLong ? "long" : "short") + " press -> mode " + mode);
  startWorking(led);
  attempt(mode, RETRIES).then(function (s) {
    stopWorking();
    busy = false;
    log("OK mode " + s.mode);
    blink(led, 1, 600);
  }, function (e) {
    stopWorking();
    busy = false;
    log("FAILED: " + e);
    blink(LED1, 3, 150);
  });
}

setWatch(function (e) {
  if (e.state) {
    holdTimer = setTimeout(function () { holdTimer = undefined; LED3.set(); }, LONG_PRESS_S * 1000);
  } else {
    if (holdTimer) clearTimeout(holdTimer);
    holdTimer = undefined;
    LED3.reset();
    onPress(e.time - e.lastTime >= LONG_PRESS_S);
  }
}, BTN, { edge: "both", repeat: true, debounce: 50 });

var bike = {
  log: function () { print(LOG.join("\n")); },
  read: function () {
    opStart = getTime();
    return session(readSettings).then(function (s) {
      print(s.raw + "  mode " + s.mode + ", assist " + s.assist + ", light " + s.light);
    }).catch(function (e) { print("failed: " + e); });
  },
  setMode: function (mode, allowUnrestricted) {
    if (!(mode >= 0 && mode <= 7)) return print("mode must be 0-7");
    if (mode != DEFAULT_MODE && !allowUnrestricted) return print("use bike.setMode(" + mode + ", true)");
    opStart = getTime();
    return setMode(mode).then(function (s) { print("mode " + s.mode + " ok"); },
                              function (e) { print("failed: " + e); });
  }
};
