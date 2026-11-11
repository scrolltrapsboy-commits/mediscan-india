/* MediScan India - main UI logic (upload, camera flow, identify, rendering). */
(function () {
  "use strict";

  var MAX_BYTES = 10 * 1024 * 1024;
  var ALLOWED = ["image/jpeg", "image/png", "image/webp"];
  var REQUEST_TIMEOUT_MS = 90000;
  var UNAVAILABLE = "Information unavailable";

  var $ = function (id) { return document.getElementById(id); };
  var els = {
    upload: $("uploadTrigger"), cameraBtn: $("cameraTrigger"), cameraBtnText: $("cameraTriggerText"),
    file: $("fileInput"), placeholder: $("previewPlaceholder"), img: $("stripPreview"), video: $("cameraVideo"),
    bar: $("previewBar"), fileName: $("fileName"), fileSize: $("fileSize"), actions: $("previewActions"),
    qIcon: $("qualityIcon"), qText: $("qualityText"), ocrConf: $("ocrConfidence"),
    statusBox: $("statusBox"), statusIcon: $("statusIcon"), statusText: $("statusText"),
    scan: $("scanActionButton"), scanText: $("scanBtnText"),
    resEmpty: $("resultEmpty"), resContent: $("resultContent"), badges: $("resultBadges"),
    resName: $("resName"), resGeneric: $("resGeneric"), resImage: $("resImage"),
    facts: $("resFacts"), sections: $("resSections"),
    priceBody: $("priceBody"), priceEmpty: $("priceEmpty"), standard: $("formulationStandard")
  };

  var state = { blob: null, name: "", source: null, objectUrl: null, mode: "idle", busy: false, controller: null, possible: [] };

  /* ---------- helpers ---------- */
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmtSize(b) { return b >= 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB"; }
  function rupee(v) { return v == null ? UNAVAILABLE : "₹" + Number(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }

  function setStatus(kind, text) {
    if (!text) { els.statusBox.classList.add("hidden"); return; }
    var styles = {
      error: ["bg-error-container", "text-on-error-container", "border-[#FDA4AF]", "error"],
      warn: ["bg-[#FFFBEB]", "text-[#92400E]", "border-[#FCD34D]", "warning"],
      info: ["bg-surface-container-lowest", "text-on-surface-variant", "border-surface-container-high", "info"]
    };
    els.statusBox.className = "rounded-lg px-space-md py-space-sm font-body-sm text-body-sm flex gap-2 items-start border " +
      styles[kind].slice(0, 3).join(" ");
    els.statusIcon.textContent = styles[kind][3];
    els.statusText.textContent = text;
  }

  function pvButton(label, icon, handler, primary) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "pv-btn" + (primary ? " primary" : "");
    b.innerHTML = '<span class="material-symbols-outlined">' + icon + "</span>" + esc(label);
    b.addEventListener("click", handler);
    return b;
  }

  function setBusy(busy) {
    state.busy = busy;
    els.upload.disabled = busy; els.cameraBtn.disabled = busy;
    [els.upload, els.cameraBtn].forEach(function (b) { b.classList.toggle("opacity-50", busy); b.classList.toggle("pointer-events-none", busy); });
    els.scan.disabled = busy || !state.blob || state.mode !== "image";
    els.scanText.textContent = busy ? "Analysing medicine..." : (state.hasResult ? "Re-Identify Medicine" : "Identify Medicine");
    els.actions.querySelectorAll("button").forEach(function (b) { b.disabled = busy; });
  }

  /* ---------- preview area states ---------- */
  function showPlaceholder() {
    els.placeholder.classList.remove("hidden");
    els.img.classList.add("hidden"); els.video.classList.add("hidden"); els.bar.classList.add("hidden");
  }

  function revokeUrl() { if (state.objectUrl) { URL.revokeObjectURL(state.objectUrl); state.objectUrl = null; } }

  function clearImage() {
    revokeUrl();
    state.blob = null; state.name = ""; state.source = null; state.mode = "idle"; state.hasResult = false;
    els.img.removeAttribute("src");
    els.actions.innerHTML = "";
    showPlaceholder();
    els.qIcon.classList.add("hidden"); els.qText.textContent = ""; els.ocrConf.textContent = "";
    setStatus(null);
    resetResults();
    els.scan.disabled = true; els.scanText.textContent = "Identify Medicine";
  }

  function setImage(blob, name, source, dims) {
    revokeUrl();
    MediCamera.stop();
    els.cameraBtnText.textContent = "Open Camera";
    state.blob = blob; state.name = name; state.source = source; state.mode = "image"; state.hasResult = false;
    state.objectUrl = URL.createObjectURL(blob);
    els.img.src = state.objectUrl;
    els.img.classList.remove("hidden"); els.placeholder.classList.add("hidden"); els.video.classList.add("hidden");
    els.bar.classList.remove("hidden");
    els.fileName.textContent = name; els.fileSize.textContent = fmtSize(blob.size);
    els.actions.innerHTML = "";
    if (source === "camera") els.actions.appendChild(pvButton("Retake", "replay", openCamera));
    els.actions.appendChild(pvButton("Remove", "delete", clearImage));
    els.qIcon.classList.add("hidden"); els.ocrConf.textContent = "";
    els.qText.textContent = dims ? dims.width + " × " + dims.height + " px" : "";
    if (!dims) {
      var probe = new Image();
      probe.onload = function () { if (state.blob === blob) els.qText.textContent = probe.naturalWidth + " × " + probe.naturalHeight + " px"; };
      probe.src = state.objectUrl;
    }
    setStatus(null);
    resetResults();
    setBusy(false);
  }

  /* ---------- upload ---------- */
  els.upload.addEventListener("click", function () { if (!state.busy) els.file.click(); });
  els.file.addEventListener("change", function () {
    var f = els.file.files && els.file.files[0];
    els.file.value = "";
    if (!f) return;
    if (ALLOWED.indexOf(f.type) === -1) { setStatus("error", "Unsupported file type. Please choose a JPG, PNG or WEBP image."); return; }
    if (f.size > MAX_BYTES) { setStatus("error", "That image is " + fmtSize(f.size) + ". Please choose one under 10 MB."); return; }
    if (f.size === 0) { setStatus("error", "That file is empty."); return; }
    if (MediCamera.isActive()) closeCamera();
    setImage(f, f.name, "upload");
  });

  /* ---------- camera ---------- */
  function cameraControls(info) {
    els.actions.innerHTML = "";
    els.actions.appendChild(pvButton("Capture", "photo_camera", takePhoto, true));
    if (info && info.canSwitch) els.actions.appendChild(pvButton("Switch", "cameraswitch", switchCam));
    els.actions.appendChild(pvButton("Close", "close", closeCamera));
  }

  function showCameraInfo(info) {
    els.fileName.textContent = "Live camera";
    els.fileSize.textContent = info && info.width ? info.width + " × " + info.height : "";
  }

  async function openCamera() {
    if (state.busy) return;
    setStatus(null);
    try {
      var info = await MediCamera.start();
      state.mode = "camera";
      revokeUrl();
      els.img.classList.add("hidden"); els.placeholder.classList.add("hidden");
      els.video.classList.remove("hidden"); els.bar.classList.remove("hidden");
      showCameraInfo(info); cameraControls(info);
      els.cameraBtnText.textContent = "Close Camera";
      els.qIcon.classList.add("hidden"); els.qText.textContent = "Frame the printed medicine name and hold steady"; els.ocrConf.textContent = "";
      els.scan.disabled = true;
    } catch (e) {
      MediCamera.stop();
      setStatus("error", e.message || "The camera could not be started.");
    }
  }

  function closeCamera() {
    MediCamera.stop();
    els.video.classList.add("hidden");
    els.cameraBtnText.textContent = "Open Camera";
    if (state.blob) { // go back to the previous photo
      state.mode = "image";
      els.img.classList.remove("hidden"); els.bar.classList.remove("hidden");
      els.fileName.textContent = state.name; els.fileSize.textContent = fmtSize(state.blob.size);
      els.actions.innerHTML = "";
      if (state.source === "camera") els.actions.appendChild(pvButton("Retake", "replay", openCamera));
      els.actions.appendChild(pvButton("Remove", "delete", clearImage));
      els.qText.textContent = "";
    } else {
      state.mode = "idle"; showPlaceholder(); els.qText.textContent = "";
    }
    setBusy(false);
  }

  async function switchCam() {
    try { var info = await MediCamera.switchCamera(); showCameraInfo(info); cameraControls(info); }
    catch (e) { setStatus("error", e.message); }
  }

  async function takePhoto() {
    try {
      var shot = await MediCamera.capture(0.95);
      var stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
      setImage(shot.blob, "camera_" + stamp + ".jpg", "camera", { width: shot.width, height: shot.height });
    } catch (e) { setStatus("error", e.message); }
  }

  els.cameraBtn.addEventListener("click", function () { MediCamera.isActive() ? closeCamera() : openCamera(); });

  /* ---------- identify ---------- */
  function cancelRequest() { if (state.controller) state.controller.abort(); }

  async function identify() {
    if (state.busy || !state.blob || state.mode !== "image") return;
    setStatus(null);
    setBusy(true);
    var timedOut = false, cancelled = false;
    var controller = new AbortController();
    state.controller = controller;
    var timer = setTimeout(function () { timedOut = true; controller.abort(); }, REQUEST_TIMEOUT_MS);
    CapsuleLoader.show("Analysing medicine...", function () { cancelled = true; controller.abort(); });
    try {
      var fd = new FormData();
      fd.append("image", state.blob, state.name);
      var res = await fetch("/api/identify", { method: "POST", body: fd, signal: controller.signal });
      var data = await res.json().catch(function () { return null; });
      if (!data) throw new Error("The server sent an unreadable response.");
      handleResponse(res.ok, data);
    } catch (e) {
      if (cancelled) setStatus("info", "Identification cancelled.");
      else if (timedOut) setStatus("error", "This is taking too long. Please try again with a smaller or clearer photo.");
      else setStatus("error", e && e.message && e.message !== "Failed to fetch" ? e.message : "Could not reach the server. Check your connection and try again.");
    } finally {
      clearTimeout(timer);
      state.controller = null;
      CapsuleLoader.hide();
      setBusy(false);
    }
  }

  function showVision(data) {
    var v = data.vision;
    if (v) {
      els.qIcon.classList.remove("hidden");
      els.qIcon.textContent = v.is_blurry ? "warning" : "check_circle";
      els.qIcon.className = "material-symbols-outlined text-[16px] " + (v.is_blurry ? "text-[#D97706]" : "text-secondary");
      els.qText.textContent = (v.is_blurry ? "Low focus detected" : "Focus check passed") + " · " + v.width + " × " + v.height + " px";
    }
    if (data.ocr) els.ocrConf.textContent = "OCR Confidence: " + data.ocr.confidence + "%";
  }

  function handleResponse(ok, data) {
    showVision(data);
    var status = data.status;
    if (!ok || status === "error") { setStatus("error", data.message || "Something went wrong."); return; }
    state.hasResult = true;
    if (status === "confirmed" && data.match) {
      renderMedicine(data.match, true);
      renderComparison(data.comparison, data.match);
      if (data.warnings && data.warnings.length) setStatus("warn", data.warnings.join(" "));
      return;
    }
    resetResults();
    if (status === "possible") {
      state.possible = data.possible || [];
      renderPossible(state.possible, data.ocr);
      setStatus("warn", "We could not confirm this medicine from the photo. Check the possible matches against your pack.");
      return;
    }
    if (status === "google_fallback") {
      renderGoogleFallback(data.google || {}, data.ocr);
      setStatus("warn", data.message || "DrugSetu had no match. Check these Google Search results before relying on any medicine information.");
      return;
    }
    var kind = (status === "api_error") ? "error" : "warn";
    var msg = data.message || "The medicine could not be identified.";
    if (data.warnings && data.warnings.length) msg += " " + data.warnings.join(" ");
    setStatus(kind, msg);
    if (status === "api_not_configured" && data.ocr) renderOcrOnly(data.ocr);
  }

  /* ---------- rendering ---------- */
  function resetResults() {
    els.resContent.classList.add("hidden"); els.resEmpty.classList.remove("hidden");
    els.resEmpty.innerHTML = '<span class="material-symbols-outlined text-[36px] text-outline">pill</span>' +
      '<h3 class="font-headline-md text-headline-md text-on-surface tracking-tight">Medicine details will appear here</h3>' +
      '<p class="font-body-md text-body-md">Add a clear photo of the printed name on a strip, box or bottle, then choose Identify Medicine. Only information returned by the DrugSetu medicine database is shown.</p>';
    els.priceBody.innerHTML = ""; els.priceEmpty.classList.remove("hidden"); els.standard.textContent = "";
    els.priceEmpty.textContent = "Identify a medicine to compare equivalent products. A comparison is shown only when the database returns matching salts, strength and dosage form with known prices.";
  }

  function renderOcrOnly(ocr) {
    var lines = (ocr.candidate_names || []).map(esc).join(", ") || "none";
    els.resEmpty.innerHTML = '<span class="material-symbols-outlined text-[36px] text-outline">text_snippet</span>' +
      '<h3 class="font-headline-md text-headline-md text-on-surface tracking-tight">Text read from your photo</h3>' +
      '<p class="font-body-md text-body-md">Possible names: <strong>' + lines + "</strong>" +
      (ocr.strengths && ocr.strengths.length ? "<br/>Strengths seen: " + ocr.strengths.map(esc).join(", ") : "") + "</p>";
  }

  function renderGoogleFallback(google, ocr) {
    els.resEmpty.classList.remove("hidden");
    els.resContent.classList.add("hidden");
    var names = (ocr && ocr.candidate_names) || [];
    var strengths = (ocr && ocr.strengths) || [];
    var query = names.concat(strengths).join(" ") || "Indian medicine";
    var html = '<span class="material-symbols-outlined text-[36px] text-primary">travel_explore</span>' +
      '<h3 class="font-headline-md text-headline-md text-on-surface tracking-tight">Google Search fallback</h3>' +
      '<p class="font-body-md text-body-md text-on-surface-variant">DrugSetu returned no matching record. The links below are web-search references, not verified medical records.</p>';
    if (google.items && google.items.length) {
      html += '<div class="flex flex-col gap-3 mt-3">';
      google.items.forEach(function (item) {
        html += '<article class="rounded-lg border border-surface-container-high p-3">' +
          '<a class="font-label-md text-label-md text-primary underline" href="' + esc(item.link) + '" target="_blank" rel="noopener noreferrer">' + esc(item.title || item.link) + '</a>' +
          '<p class="font-body-sm text-body-sm text-on-surface-variant mt-1">' + esc(item.snippet || "") + '</p>' +
          '<span class="font-label-sm text-label-sm text-outline">' + esc(item.displayLink || "Google Search result") + '</span></article>';
      });
      html += '</div>';
    }
    var url = google.search_url || ("https://www.google.com/search?q=" + encodeURIComponent(query + " medicine India"));
    html += '<a class="inline-flex items-center gap-2 mt-3 px-4 py-2 rounded-lg bg-primary text-on-primary font-label-md text-label-md" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">Open Google Search <span aria-hidden="true">↗</span></a>';
    html += '<p class="font-body-sm text-body-sm text-outline mt-3">Check the product name, composition, strength and manufacturer against the package. Do not use search snippets alone to make treatment decisions.</p>';
    els.resEmpty.innerHTML = html;
    els.priceBody.innerHTML = "";
    els.priceEmpty.classList.remove("hidden");
    els.priceEmpty.textContent = "No verified price comparison is available from DrugSetu for this medicine.";
  }

  function fact(label, value) {
    return '<div class="flex flex-col gap-0.5"><span class="font-label-sm text-label-sm text-outline uppercase">' + esc(label) +
      '</span><span class="font-body-sm text-body-sm font-semibold text-on-surface">' + esc(value || UNAVAILABLE) + "</span></div>";
  }

  function section(icon, color, title, text) {
    return '<div class="flex flex-col gap-1"><span class="font-label-md text-label-md text-on-surface uppercase tracking-wider flex items-center gap-1.5">' +
      '<span class="material-symbols-outlined ' + color + ' text-[18px]">' + icon + "</span>" + esc(title) + "</span>" +
      '<p class="font-body-md text-body-md text-on-surface-variant leading-relaxed pl-6">' + esc(text || UNAVAILABLE) + "</p></div>";
  }

  function strengthText(p) {
    var c = p.composition || [];
    var withVal = c.filter(function (x) { return x.value; });
    return withVal.length ? withVal.map(function (x) { return x.value + " " + x.unit; }).join(" + ") : null;
  }

  function renderMedicine(p, confirmed) {
    els.resEmpty.classList.add("hidden"); els.resContent.classList.remove("hidden");
    var badges = "";
    if (p.schedule) badges += '<span class="font-label-sm text-label-sm px-2 py-0.5 rounded bg-surface-container-high text-on-surface-variant">' + esc(p.schedule) + "</span>";
    if (p.dosage_form) badges += '<span class="font-label-sm text-label-sm px-2 py-0.5 rounded bg-surface-container-high text-on-surface-variant">' + esc(p.dosage_form) + "</span>";
    badges += confirmed
      ? '<span class="font-label-sm text-label-sm px-2 py-0.5 rounded bg-secondary-fixed text-on-secondary-fixed flex items-center gap-1"><span class="material-symbols-outlined text-[13px]">verified</span> Validated</span>'
      : '<span class="font-label-sm text-label-sm px-2 py-0.5 rounded bg-[#FFFBEB] text-[#92400E] border border-[#FCD34D]">Possible match - not confirmed</span>';
    els.badges.innerHTML = badges;
    els.resName.textContent = p.name || UNAVAILABLE;
    els.resGeneric.textContent = p.generic_name || p.composition_text || "";
    if (p.image_url) { els.resImage.src = p.image_url; els.resImage.classList.remove("hidden"); }
    else { els.resImage.classList.add("hidden"); els.resImage.removeAttribute("src"); }

    var pack = (p.packagings || []).find(function (x) { return x.label; });
    var mrp = (p.packagings || []).find(function (x) { return x.mrp != null; });
    els.facts.innerHTML =
      fact("Active Ingredients", p.composition_text) + fact("Strength", strengthText(p)) + fact("Manufacturer", p.manufacturer) +
      fact("Dosage Form", p.dosage_form) + fact("Packaging", pack && pack.label) + fact("MRP", mrp ? rupee(mrp.mrp) : null);

    var html = section("healing", "text-primary", "Primary Uses", p.uses) +
      section("info", "text-tertiary", "Common Side Effects", p.side_effects) +
      section("shield", "text-secondary", "Clinical Precautions", p.precautions);
    if (p.interactions) html += section("swap_horiz", "text-tertiary", "Drug Interactions", p.interactions);
    els.sections.innerHTML = html;
  }

  function renderPossible(list, ocr) {
    els.resEmpty.classList.remove("hidden");
    var read = ocr && ocr.candidate_names && ocr.candidate_names.length ? "Read from your photo: <strong>" + ocr.candidate_names.map(esc).join(", ") + "</strong>. " : "";
    els.resEmpty.innerHTML = '<span class="material-symbols-outlined text-[36px] text-[#D97706]">help</span>' +
      '<h3 class="font-headline-md text-headline-md text-on-surface tracking-tight">Medicine not confirmed</h3>' +
      '<p class="font-body-md text-body-md">' + read + "Pick the closest match to view its details, or retake a clearer photo.</p>" +
      '<div class="flex flex-wrap gap-space-sm" id="possibleList"></div>';
    var box = $("possibleList");
    list.forEach(function (p, i) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "px-3 py-2 rounded-lg bg-surface-container-low hover:bg-surface-container text-on-surface font-label-md text-label-md";
      b.textContent = p.name || "Unnamed product";
      b.addEventListener("click", function () { renderMedicine(state.possible[i], false); setStatus("warn", "This is a possible match only. Verify it against your medicine pack."); });
      box.appendChild(b);
    });
  }

  function renderComparison(cmp, base) {
    els.priceBody.innerHTML = "";
    var rows = (cmp && cmp.rows) || [];
    if (!rows.length) {
      els.priceEmpty.classList.remove("hidden");
      els.priceEmpty.textContent = "No reliable comparable products were returned for this medicine, so no price comparison is shown.";
      return;
    }
    els.priceEmpty.classList.add("hidden");
    var form = base.dosage_form ? " " + base.dosage_form : "";
    els.standard.textContent = base.composition_text ? "Formulation standard: " + base.composition_text + form : "";
    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      var cls = r.is_lowest ? "bg-secondary-fixed/20 hover:bg-secondary-fixed/30" : (r.is_scanned ? "bg-surface-container/50 hover:bg-surface-container" : "hover:bg-surface-container-low/60");
      tr.className = cls + " transition-colors";
      var badge = r.is_lowest ? '<span class="font-label-sm text-label-sm px-2 py-0.5 rounded-full bg-secondary text-on-secondary whitespace-nowrap">Lowest Price</span>'
        : (r.is_scanned ? '<span class="font-label-sm text-label-sm px-2 py-0.5 rounded bg-surface-container-high text-on-surface-variant">Scanned</span>' : "");
      var nameCls = r.is_scanned ? "font-semibold text-primary" : (r.is_lowest ? "font-semibold text-on-surface" : "font-medium text-on-surface");
      var priceCls = r.is_lowest ? "font-semibold text-secondary" : (r.is_scanned ? "font-semibold text-on-surface" : "font-medium text-on-surface");
      tr.innerHTML =
        '<td class="py-4 px-space-md ' + nameCls + '"><div class="flex items-center gap-2 flex-wrap"><span>' + esc(r.name || UNAVAILABLE) + "</span>" + badge + "</div></td>" +
        '<td class="py-4 px-space-md text-on-surface-variant">' + esc(r.manufacturer || UNAVAILABLE) + "</td>" +
        '<td class="py-4 px-space-md text-on-surface-variant">' + esc(r.pack_label || (r.quantity ? r.quantity + " units" : UNAVAILABLE)) + "</td>" +
        '<td class="py-4 px-space-md text-right ' + priceCls + '">' + esc(r.mrp != null ? rupee(r.mrp) : UNAVAILABLE) + "</td>" +
        '<td class="py-4 px-space-md text-right font-data-mono text-data-mono ' + (r.is_lowest ? "font-semibold text-secondary" : "text-on-surface-variant") + '">' + esc(r.per_unit != null ? rupee(r.per_unit) : "-") + "</td>";
      els.priceBody.appendChild(tr);
    });
  }

  els.scan.addEventListener("click", identify);
  window.addEventListener("pagehide", function () { MediCamera.stop(); cancelRequest(); });

  // test hook (read-only state for browser tests)
  window.__mediscan = { state: state };
})();
