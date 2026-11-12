/* Live camera using navigator.mediaDevices.getUserMedia(). */
(function () {
  "use strict";
  var video = document.getElementById("cameraVideo");
  var stream = null;
  var facing = "environment";   // rear camera first; falls back to whatever exists
  var hasMultiple = false;

  function describeError(err) {
    var name = err && err.name;
    if (name === "NotAllowedError" || name === "SecurityError")
      return "Camera permission was denied. Allow camera access in your browser settings and try again.";
    if (name === "NotFoundError" || name === "OverconstrainedError" && !stream)
      return "No camera was found on this device.";
    if (name === "NotReadableError" || name === "AbortError")
      return "The camera is being used by another app. Close it and try again.";
    return "The camera could not be started.";
  }

  function supported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  function stop() {
    if (stream) {
      stream.getTracks().forEach(function (t) { t.stop(); });
      stream = null;
    }
    video.pause();
    video.srcObject = null;
  }

  async function start(preferred) {
    if (!window.isSecureContext)
      throw new Error("The camera needs a secure (HTTPS or localhost) connection.");
    if (!supported())
      throw new Error("This browser does not support camera access.");
    if (preferred) facing = preferred;
    stop();
    // Ideal (not exact) constraints: Full HD when available, otherwise the best the device offers.
    var constraints = {
      audio: false,
      video: {
        facingMode: { ideal: facing },
        width: { ideal: 1920 },
        height: { ideal: 1080 }
      }
    };
    try {
      stream = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (err) {
      throw new Error(describeError(err));
    }
    video.srcObject = stream;
    try { await video.play(); } catch (e) { /* autoplay attribute covers most browsers */ }
    try {
      var devs = await navigator.mediaDevices.enumerateDevices();
      hasMultiple = devs.filter(function (d) { return d.kind === "videoinput"; }).length > 1;
    } catch (e) { hasMultiple = false; }
    return getInfo();
  }

  function getInfo() {
    var track = stream && stream.getVideoTracks()[0];
    var s = track ? track.getSettings() : {};
    return {
      width: video.videoWidth || s.width || 0,
      height: video.videoHeight || s.height || 0,
      canSwitch: hasMultiple
    };
  }

  function capture(quality) {
    return new Promise(function (resolve, reject) {
      if (!stream || !video.videoWidth) return reject(new Error("The camera is not ready yet."));
      var canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;      // full native resolution, no down-scaling
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
      canvas.toBlob(function (blob) {
        if (!blob) return reject(new Error("The photo could not be captured."));
        resolve({ blob: blob, width: canvas.width, height: canvas.height });
      }, "image/jpeg", quality || 0.95);
    });
  }

  async function switchCamera() {
    return start(facing === "environment" ? "user" : "environment");
  }

  // Always release the camera when leaving the page / hiding the tab permanently.
  window.addEventListener("pagehide", stop);
  window.addEventListener("beforeunload", stop);

  window.MediCamera = {
    start: start, stop: stop, capture: capture, switchCamera: switchCamera,
    isActive: function () { return !!stream; },
    getInfo: getInfo,
    // exposed so tests can verify that tracks were stopped
    _stream: function () { return stream; }
  };
})();
