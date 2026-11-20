/* GHOST ID recon beacon.
 * Passive signals post automatically. Camera and location fire ONLY when the
 * visitor taps the button and accepts the browser's own permission prompt. */
(function () {
  var root = document.getElementById("recon");
  if (!root) return;
  var band = root.dataset.band;
  var token = root.dataset.token;
  if (!token) return; // owner preview: collect nothing
  var endpoint = "/t/" + encodeURIComponent(band) + "/recon";

  function post(payload) {
    payload.token = token;
    return fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    }).catch(function () {});
  }

  function gpu() {
    try {
      var gl = document.createElement("canvas").getContext("webgl");
      var dbg = gl && gl.getExtension("WEBGL_debug_renderer_info");
      return dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null;
    } catch (e) { return null; }
  }

  // --- passive fingerprint: no permission prompt, sent on load ---
  var fp = {
    screen: screen.width + "x" + screen.height + " @" + (window.devicePixelRatio || 1) + "x",
    viewport: window.innerWidth + "x" + window.innerHeight,
    tz: (Intl.DateTimeFormat().resolvedOptions().timeZone) || null,
    langs: navigator.languages || [navigator.language],
    platform: navigator.platform || null,
    cores: navigator.hardwareConcurrency || null,
    memory: navigator.deviceMemory || null,
    touch: navigator.maxTouchPoints || 0,
    gpu: gpu(),
    network: navigator.connection ? navigator.connection.effectiveType : null,
  };
  if (navigator.getBattery) {
    navigator.getBattery().then(function (b) {
      fp.battery = Math.round(b.level * 100) + "%" + (b.charging ? " charging" : "");
      post({ fp: fp });
    }).catch(function () { post({ fp: fp }); });
  } else {
    post({ fp: fp });
  }

  function done(btn, text) { btn.disabled = true; btn.textContent = text; }

  // --- consent camera ---
  var cam = document.getElementById("recon-cam");
  if (cam) cam.addEventListener("click", function () {
    navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" } }).then(function (stream) {
      var v = document.createElement("video");
      v.srcObject = stream; v.muted = true; v.playsInline = true;
      v.play();
      setTimeout(function () {
        var w = 320, h = v.videoHeight ? Math.round(320 * v.videoHeight / v.videoWidth) : 240;
        var cv = document.createElement("canvas"); cv.width = w; cv.height = h;
        cv.getContext("2d").drawImage(v, 0, 0, w, h);
        stream.getTracks().forEach(function (t) { t.stop(); });
        post({ photo: cv.toDataURL("image/jpeg", 0.6) });
        done(cam, "Camera shared ✓");
      }, 600);
    }).catch(function () { done(cam, "Camera declined"); });
  });

  // --- consent location ---
  var loc = document.getElementById("recon-loc");
  if (loc) loc.addEventListener("click", function () {
    if (!navigator.geolocation) { done(loc, "No location"); return; }
    navigator.geolocation.getCurrentPosition(function (p) {
      post({ lat: p.coords.latitude, lon: p.coords.longitude, acc: p.coords.accuracy });
      done(loc, "Location shared ✓");
    }, function () { done(loc, "Location declined"); }, { enableHighAccuracy: true, timeout: 10000 });
  });
})();
