// One copy-to-clipboard helper for every "Copy" button in the app.
//
// navigator.clipboard only exists on HTTPS pages and on localhost. A gateway reached
// over the network at http://<ip>:8010 or http://<host>.local:8010 is neither, so the
// modern API is missing there and the old select-and-copy method is used instead.
function copyText(text, btn) {
  function flash(label) {
    if (!btn) return;
    if (btn.dataset.copyLabel === undefined) btn.dataset.copyLabel = btn.textContent;
    btn.textContent = label;
    clearTimeout(btn._copyTimer);
    btn._copyTimer = setTimeout(function () { btn.textContent = btn.dataset.copyLabel; }, 1400);
  }
  function legacy() {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0";
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    ta.setSelectionRange(0, text.length);
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(function () { flash("Copied"); }, function () { flash(legacy() ? "Copied" : "Select and copy it"); });
  } else {
    flash(legacy() ? "Copied" : "Select and copy it");
  }
}
