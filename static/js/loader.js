/* Capsule loader: transparent, lives only inside the preview area. */
(function () {
  "use strict";
  var overlay = document.getElementById("loaderOverlay");
  var textEl = document.getElementById("loaderText");
  var cancelBtn = document.getElementById("loaderCancel");
  var onCancel = null;
  var safetyTimer = null;
  var MAX_VISIBLE_MS = 100000; // the loader can never run forever

  function hide() {
    clearTimeout(safetyTimer);
    safetyTimer = null;
    onCancel = null;
    overlay.classList.add("hidden");
  }

  function show(message, cancelCallback) {
    textEl.textContent = message || "Analysing medicine...";
    onCancel = typeof cancelCallback === "function" ? cancelCallback : null;
    cancelBtn.classList.toggle("hidden", !onCancel);
    overlay.classList.remove("hidden");
    clearTimeout(safetyTimer);
    safetyTimer = setTimeout(hide, MAX_VISIBLE_MS);
  }

  cancelBtn.addEventListener("click", function () {
    var cb = onCancel;
    hide();
    if (cb) cb();
  });

  window.CapsuleLoader = {
    show: show,
    hide: hide,
    isVisible: function () { return !overlay.classList.contains("hidden"); }
  };
})();
