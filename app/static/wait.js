(function () {
  var id = document.body.dataset.req;
  var el = document.getElementById("status");
  var messages = {
    denied: "Your request was declined.",
    expired: "The request timed out. Tap the band again to retry.",
    claimed: "This request was already used.",
    unknown: "This request isn't linked to this browser."
  };
  function poll() {
    fetch("/api/requests/" + encodeURIComponent(id), { credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.status === "approved") { location.href = j.redirect; return; }
        if (messages[j.status]) { el.textContent = messages[j.status]; document.querySelector(".pulse").remove(); return; }
        setTimeout(poll, 3000);
      })
      .catch(function () { setTimeout(poll, 5000); });
  }
  poll();
})();
