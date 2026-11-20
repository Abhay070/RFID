(function () {
  var expires = parseInt(document.body.dataset.expires, 10) * 1000;
  var el = document.getElementById("countdown");
  function tick() {
    var left = Math.max(0, Math.floor((expires - Date.now()) / 1000));
    var h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
    el.textContent = (h ? h + ":" : "") + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
    if (left === 0) { location.reload(); return; }
    setTimeout(tick, 1000);
  }
  tick();
  // Re-check with the server every 20s so a revoke takes effect quickly.
  setInterval(function () {
    fetch("/private", { credentials: "same-origin" }).then(function (r) { if (r.status === 403) location.reload(); });
  }, 20000);
})();
