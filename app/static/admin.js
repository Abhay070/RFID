/* Command center: tabs, live visitor feed, copy buttons. CSP-safe (served from /static). */
(function () {
  // --- tabs ---
  var tabs = document.querySelectorAll(".tab");
  var panels = document.querySelectorAll(".panel");
  function show(name) {
    tabs.forEach(function (t) { t.classList.toggle("active", t.dataset.tab === name); });
    panels.forEach(function (p) { p.classList.toggle("active", p.dataset.panel === name); });
    try { localStorage.setItem("ghost_tab", name); } catch (e) {}
  }
  tabs.forEach(function (t) { t.addEventListener("click", function () { show(t.dataset.tab); }); });
  try { var saved = localStorage.getItem("ghost_tab"); if (saved) show(saved); } catch (e) {}

  // --- copy buttons ---
  document.querySelectorAll("[data-copy]").forEach(function (b) {
    b.addEventListener("click", function () {
      var txt = b.dataset.copy;
      (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject())
        .then(function () { var o = b.textContent; b.textContent = "Copied ✓";
          setTimeout(function () { b.textContent = o; }, 1500); })
        .catch(function () {});
    });
  });

  // --- live feed ---
  var feed = document.getElementById("feed");
  if (!feed) return;
  var lastTop = 0;
  function render(items) {
    if (!items.length) { feed.innerHTML = '<p class="muted fine">No taps yet.</p>'; return; }
    feed.innerHTML = items.map(function (it) {
      var badges = (it.photo ? '<span class="dot-badge cam">📷</span>' : "")
                 + (it.loc ? '<span class="dot-badge loc">📍</span>' : "");
      var place = it.place ? ' · ' + it.place : "";
      return '<a class="feed-row" href="/admin/tap/' + it.id + '">'
           + '<span class="feed-main"><strong>' + it.device + '</strong>'
           + '<span class="muted fine"> · ' + it.ip + place + '</span></span>'
           + '<span class="feed-side">' + badges + '<span class="muted fine">' + it.time + '</span></span></a>';
    }).join("");
  }
  function poll() {
    fetch("/admin/feed.json", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        render(d.items);
        var top = d.items[0] ? d.items[0].id : 0;
        if (lastTop && top > lastTop) { feed.classList.add("flash");
          setTimeout(function () { feed.classList.remove("flash"); }, 900); }
        lastTop = top;
      }).catch(function () {});
  }
  poll();
  setInterval(poll, 5000);
})();
