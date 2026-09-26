/* Message Gateway: small shared helpers (JSON posts, messages, test-send popup). */
(function () {
  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
      .then(function (r) {
        if (r.status === 401) { window.location = "/gateway/login"; throw new Error("Please log in again."); }
        if (!r.ok) return r.json().catch(function () { return {}; }).then(function (j) {
          var d = j.detail; throw new Error((d && d.error) || (typeof d === "string" ? d : "") || "Request failed.");
        });
        return r.json();
      });
  }
  function say(el, text, kind) {
    el.innerHTML = ""; var d = document.createElement("div"); d.className = "msg " + kind; d.textContent = text; el.appendChild(d);
  }

  // Send-a-test popup. providers: [{name,label}] of connected providers for the channel.
  function openTest(channel, label, providers, defaultName, preset, apps) {
    var dlg = document.getElementById("testDlg"); if (!dlg) return;
    var needsTo = channel !== "push";
    var multi = providers.length > 1;
    dlg.querySelector("[data-title]").textContent = "Send a test " + label.toLowerCase();
    var sel = dlg.querySelector("[data-provider]");
    sel.innerHTML = providers.map(function (p) { return '<option value="' + p.name + '">' + p.label + (p.name === defaultName ? " (default)" : "") + "</option>"; }).join("");
    sel.value = preset || defaultName || (providers[0] && providers[0].name);
    dlg.querySelector("[data-provider-row]").classList.toggle("hidden", !multi);
    // Pushover apps: let the tester pick which application sends the test.
    var appSel = dlg.querySelector("[data-app]"), appRow = dlg.querySelector("[data-app-row]");
    var usable = (apps || []).filter(function (a) { return a.set; });
    appSel.innerHTML = usable.map(function (a) { return '<option value="' + a.name + '">' + a.name + (a.is_default ? " (default)" : "") + "</option>"; }).join("");
    var def = usable.filter(function (a) { return a.is_default; })[0];
    if (def) appSel.value = def.name;
    function syncApp() { appRow.classList.toggle("hidden", !(channel === "push" && sel.value === "pushover" && usable.length > 1)); }
    sel.onchange = syncApp; syncApp();
    var to = dlg.querySelector("[data-to]");
    dlg.querySelector("[data-to-row]").classList.toggle("hidden", !needsTo);
    to.placeholder = channel === "email" ? "you@example.com" : "+15551234567";
    to.value = "";
    dlg.querySelector("[data-hint]").textContent = needsTo ? "" : "Sends a short test notification to your configured device.";
    var out = dlg.querySelector("[data-out]"); out.innerHTML = "";
    var go = dlg.querySelector("[data-send]"); go.disabled = false; go.textContent = "Send test";
    go.onclick = function () {
      go.disabled = true; go.textContent = "Sending…";
      post("/gateway/channels/" + channel + "/test", { provider: sel.value, to: needsTo ? to.value : null, app: (channel === "push" && sel.value === "pushover" && usable.length > 1) ? appSel.value : null })
        .then(function (r) { say(out, r.ok ? "Sent. Check your device or inbox." : (r.error || "Send failed."), r.ok ? "ok" : "err"); })
        .catch(function (e) { say(out, e.message, "err"); })
        .then(function () { go.disabled = false; go.textContent = "Send test"; });
    };
    dlg.showModal();
    if (needsTo) to.focus();
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-test-channel]"); if (!b) return;
    openTest(b.dataset.testChannel, b.dataset.testLabel, JSON.parse(b.dataset.providers || "[]"), b.dataset.default || "", b.dataset.provider || "");
  });
  window.MG = { post: post, say: say, openTest: openTest };
})();
