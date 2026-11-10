"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const numberFormatter = new Intl.NumberFormat("fr-FR");
  const state = {
    info: null, token: "", uploadId: null, filename: "", baseProject: {},
    currentJob: null, trainingJob: null, report: null, preview: null,
    generatedSignature: "", started: 0, pollTimer: null, elapsedTimer: null,
    trainingTimer: null, loading: false, holeId: 0, logs: [], selectedFace: null,
    view: { yaw: 0, pitch: 0, zoom: 1, panX: 0, panY: 0 },
    geometry: { points: [], faces: [], edges: [], bounds: null },
    framesPending: false, cssWidth: 1, cssHeight: 1, drag: null, projected: [],
  };

  const defaults = {
    schema_version: 1,
    name: "Plaque percée",
    mode: "planar",
    geometry: { kind: "plate", width: 10, height: 6, thickness: 2, corner_radius: 0.6, chamfer: 0, holes: [{ x: 5, y: 3, radius: 1.2 }] },
    mesh: { size: 0.5, min_size: 0.1, order: 1, algorithm: "automatic", recombine: false, threads: 1, max_elements: 200000 },
    repair: { enabled: false, tolerance: 1e-7, sew: true, small_edges: true, small_faces: false, make_solids: false },
  };

  const fallbackExamples = [
    { id: "plate", name: "Plaque percée", description: "Un perçage circulaire et des coins arrondis.", project: defaults },
    { id: "solid", name: "Plaque extrudée", description: "Un volume fermé traversé par un perçage.", project: { ...defaults, name: "Plaque extrudée", mode: "volume" } },
    { id: "bracket", name: "Équerre mécanique", description: "Un assemblage en L pour explorer le maillage volumique.", project: { ...defaults, name: "Équerre mécanique", mode: "volume", geometry: { kind: "bracket", width: 8, height: 6, depth: 3, wall: 1, throughhole_radius: 0.35 } } },
    { id: "shell", name: "Surface sphérique", description: "Une coque courbe dans l’espace.", project: { ...defaults, name: "Surface sphérique", mode: "surface", geometry: { kind: "sphere", radius: 3 } } },
  ];

  function icon(name) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#i-${name}`);
    svg.append(use);
    return svg;
  }

  function log(message, kind = "") {
    const entry = document.createElement("div");
    entry.className = `log-entry ${kind}`;
    const time = document.createElement("time");
    time.textContent = new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    entry.append(time, document.createTextNode(String(message)));
    $("activity-log").append(entry);
    while ($("activity-log").children.length > 100) $("activity-log").firstElementChild.remove();
    $("activity-log").scrollTop = $("activity-log").scrollHeight;
    $("activity-message").textContent = String(message);
    $("activity-indicator").className = `activity-indicator ${kind}`;
  }

  function toast(message, kind = "") {
    const element = document.createElement("div");
    element.className = `toast ${kind}`;
    element.textContent = String(message);
    $("toast-area").append(element);
    setTimeout(() => element.remove(), kind === "error" ? 10000 : 5500);
  }

  function showError(error) {
    const message = error instanceof Error ? error.message : String(error);
    log(message, "error");
    toast(message, "error");
  }

  async function request(path, { method = "GET", data, blob = false } = {}) {
    const headers = {};
    if (data !== undefined) headers["Content-Type"] = "application/json";
    if (method !== "GET") headers["X-Atelier-Token"] = state.token;
    let response;
    try {
      response = await fetch(path, { method, headers, body: data === undefined ? undefined : JSON.stringify(data), cache: "no-store" });
    } catch (_) {
      throw new Error("Le serveur local ne répond plus. Relancez l’atelier puis rechargez cette page.");
    }
    if (!response.ok) {
      let detail;
      try { detail = await response.json(); } catch (_) { detail = null; }
      throw new Error(detail?.error || detail?.message || `L’opération a échoué (${response.status}).`);
    }
    if (blob) return { blob: await response.blob(), disposition: response.headers.get("Content-Disposition") || "" };
    if (response.status === 204) return {};
    return response.json();
  }

  function downloadBlob(blob, filename) {
    const link = document.createElement("a");
    const url = URL.createObjectURL(blob);
    link.href = url;
    link.download = filename.replace(/[\\/:*?"<>|]/g, "_");
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }

  function filenameFrom(disposition, fallback) {
    const utf = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (utf) { try { return decodeURIComponent(utf[1]); } catch (_) { /* use plain fallback */ } }
    const plain = disposition.match(/filename="?([^";]+)"?/i);
    return plain ? plain[1] : fallback;
  }

  function fileAsBase64(file) {
    const maximum = state.info?.max_upload_bytes || 50 * 1024 * 1024;
    if (file.size > maximum) throw new Error(`Ce fichier dépasse la limite de ${Math.round(maximum / 1024 / 1024)} Mio de l’interface.`);
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("Impossible de lire le fichier sélectionné."));
      reader.readAsDataURL(file);
    });
  }

  function value(id, label, { min = -Infinity, max = Infinity, integer = false } = {}) {
    const raw = $(id).value.trim();
    const result = Number(raw);
    if (!raw || !Number.isFinite(result) || result < min || result > max || (integer && !Number.isInteger(result))) {
      const limits = Number.isFinite(min) && Number.isFinite(max) ? ` entre ${min} et ${max}` : Number.isFinite(min) ? ` au moins égal${integer ? "" : "e"} à ${min}` : "";
      throw new Error(`${label} : ${integer ? "un entier" : "une valeur finie"}${limits} est nécessaire.`);
    }
    return result;
  }

  function currentMode() { return document.querySelector("#mode-control .active")?.dataset.mode || "planar"; }

  function projectFromForm() {
    const kind = $("geometry-kind").value;
    let geometry = { ...(state.baseProject.geometry || {}), kind };
    if (["plate", "box", "bracket"].includes(kind)) {
      geometry.width = value("geo-width", "Largeur", { min: Number.EPSILON });
      geometry.height = value("geo-height", "Hauteur", { min: Number.EPSILON });
    }
    if (["box", "bracket"].includes(kind)) geometry.depth = value("geo-depth", "Profondeur", { min: Number.EPSILON });
    if (kind === "plate") {
      geometry.thickness = value("geo-thickness", "Épaisseur", { min: Number.EPSILON });
      geometry.corner_radius = value("geo-corner-radius", "Rayon du congé", { min: 0 });
      geometry.chamfer = value("geo-chamfer", "Chanfrein", { min: 0 });
      geometry.holes = [...$("hole-rows").children].map((row) => ({
        x: value(row.querySelector('[data-hole="x"]').id, "Centre x du perçage"),
        y: value(row.querySelector('[data-hole="y"]').id, "Centre y du perçage"),
        radius: value(row.querySelector('[data-hole="radius"]').id, "Rayon du perçage", { min: Number.EPSILON }),
      }));
    }
    if (kind === "sphere") geometry.radius = value("geo-radius", "Rayon", { min: Number.EPSILON });
    if (kind === "bracket") {
      geometry.wall = value("geo-wall", "Épaisseur de paroi", { min: Number.EPSILON });
      geometry.throughhole_radius = value("geo-throughhole-radius", "Rayon du perçage", { min: 0 });
    }
    if (kind === "polygon") {
      try { geometry.outer = JSON.parse($("polygon-outer").value); geometry.holes = JSON.parse($("polygon-holes").value); }
      catch (_) { throw new Error("Les contours doivent être des listes de coordonnées valides, par exemple [[0,0],[8,0],[8,5],[0,5]]."); }
    }
    // File names and paths stay outside the declarative geometry. The server
    // resolves only its own upload_id and embeds source bytes in portable files.
    for (const key of ["filename", "path", "source", "file", "script"]) delete geometry[key];
    const size = value("mesh-size", "Taille cible", { min: Number.EPSILON });
    const minSize = value("mesh-min-size", "Taille minimale", { min: Number.EPSILON });
    if (minSize > size) throw new Error("La taille minimale doit être inférieure ou égale à la taille cible.");
    const project = {
      ...state.baseProject,
      schema_version: 1,
      name: $("project-name").value.trim() || "Sans titre",
      mode: currentMode(), geometry,
      mesh: { ...(state.baseProject.mesh || {}), size, min_size: minSize,
        order: value("mesh-order", "Ordre", { integer: true, min: 1 }),
        algorithm: $("mesh-algorithm").value, recombine: $("mesh-recombine").checked,
        threads: value("mesh-threads", "Fils de calcul", { integer: true, min: 1, max: 32 }),
        max_elements: value("mesh-max-elements", "Limite d’éléments", { integer: true, min: 10, max: 2000000 }) },
      repair: { ...(state.baseProject.repair || {}), enabled: $("repair-enabled").checked,
        tolerance: value("repair-tolerance", "Tolérance de réparation", { min: 0 }),
        sew: $("repair-sew").checked, small_edges: $("repair-small-edges").checked,
        small_faces: $("repair-small-faces").checked, make_solids: $("repair-make-solids").checked },
    };
    return project;
  }

  function signature() {
    try { return JSON.stringify({ project: projectFromForm(), uploadId: state.uploadId, guide: $("use-advisor").checked }); }
    catch (_) { return "invalid"; }
  }

  function addHole(hole = null, notify = true) {
    const index = ++state.holeId;
    const width = Number($("geo-width").value) || 10;
    const height = Number($("geo-height").value) || 6;
    const data = hole || { x: width / 2, y: height / 2, radius: Math.min(width, height) / 8 };
    const row = document.createElement("div");
    row.className = "hole-row";
    for (const key of ["x", "y", "radius"]) {
      const input = document.createElement("input");
      input.type = "number"; input.step = "any"; input.value = data[key];
      input.id = `hole-${index}-${key}`; input.dataset.hole = key;
      input.setAttribute("aria-label", `${key === "radius" ? "Rayon" : `Centre ${key}`} du perçage ${index}`);
      if (key === "radius") input.min = "0.000001";
      input.addEventListener("input", changed);
      row.append(input);
    }
    const remove = document.createElement("button");
    remove.type = "button"; remove.title = "Supprimer ce perçage";
    remove.setAttribute("aria-label", `Supprimer le perçage ${index}`);
    remove.append(icon("close"));
    remove.addEventListener("click", () => { row.remove(); changed(); });
    row.append(remove); $("hole-rows").append(row);
    if (notify) changed();
  }

  function setMode(mode, notify = true) {
    document.querySelectorAll("#mode-control button").forEach((button) => button.classList.toggle("active", button.dataset.mode === mode));
    if (mode === "volume" && $("repair-enabled").checked) $("repair-make-solids").checked = true;
    $("dimension-badge").textContent = mode === "planar" ? "2D" : mode === "surface" ? "SURFACE" : "3D";
    updateVisibility();
    if (notify) changed();
  }

  function updateVisibility() {
    const kind = $("geometry-kind").value;
    document.querySelectorAll("[data-kinds]").forEach((element) => { element.hidden = !element.dataset.kinds.split(" ").includes(kind); });
    $("thickness-field").hidden = kind !== "plate" || currentMode() !== "volume";
    $("plate-details").hidden = kind !== "plate";
    $("polygon-details").hidden = kind !== "polygon";
    $("native-geometry").hidden = kind === "import";
    $("import-geometry").hidden = kind !== "import";
    const isVolume = currentMode() === "volume";
    $("mesh-recombine").disabled = isVolume;
    if (isVolume) $("mesh-recombine").checked = false;
    $("mesh-recombine").closest("label").style.opacity = isVolume ? "0.45" : "1";
    $("mesh-recombine").closest("label").title = isVolume ? "La recombinaison quadrangulaire est disponible pour les surfaces uniquement." : "";
    $("import-empty").hidden = kind !== "import" || Boolean(state.preview);
    if (kind === "import") {
      $("import-empty").querySelector("h2").textContent = state.uploadId ? "Géométrie importée" : "Une pièce à explorer";
      $("import-empty").querySelector("p").textContent = state.uploadId ? `${state.filename}. Choisissez vos réglages, puis générez le maillage pour afficher la pièce et son diagnostic.` : "Importez votre géométrie, réglez la taille des éléments et créez un maillage adapté à votre calcul.";
      $("empty-import-button").textContent = state.uploadId ? "Changer de pièce" : "Importer une pièce";
    }
    $("repair-options").classList.toggle("disabled", !$("repair-enabled").checked);
    $("repair-options").querySelectorAll("input").forEach((input) => { input.disabled = !$("repair-enabled").checked; });
  }

  function loadProject(project, { uploadId = null, filename = "", silent = false } = {}) {
    if (!project || typeof project !== "object" || !project.geometry || !project.mesh) throw new Error("Ce fichier ne contient pas un projet Atelier Mailleur.");
    if (state.currentJob) throw new Error("Attendez la fin du calcul ou arrêtez-le avant de changer de projet.");
    state.loading = true;
    state.baseProject = structuredClone(project);
    state.uploadId = uploadId; state.filename = filename || project.geometry.filename || "";
    const g = project.geometry || {}, m = project.mesh || {}, r = project.repair || {};
    $("project-name").value = project.name || "Sans titre";
    $("geometry-kind").value = g.kind || "plate";
    const map = { "geo-width": g.width ?? 10, "geo-height": g.height ?? 6, "geo-depth": g.depth ?? 3,
      "geo-thickness": g.thickness ?? 2, "geo-radius": g.radius ?? 3,
      "geo-corner-radius": g.corner_radius ?? 0, "geo-chamfer": g.chamfer ?? 0,
      "geo-wall": g.wall ?? 1, "geo-throughhole-radius": g.throughhole_radius ?? 0,
      "mesh-size": m.size ?? 0.5, "mesh-min-size": m.min_size ?? 0.1,
      "mesh-order": m.order ?? 1, "mesh-algorithm": m.algorithm ?? "automatic",
      "mesh-threads": m.threads ?? 1, "mesh-max-elements": m.max_elements ?? 200000,
      "repair-tolerance": r.tolerance ?? 1e-7 };
    for (const [id, val] of Object.entries(map)) $(id).value = val;
    $("mesh-recombine").checked = Boolean(m.recombine);
    $("repair-enabled").checked = Boolean(r.enabled);
    $("repair-sew").checked = r.sew !== false;
    $("repair-small-edges").checked = r.small_edges !== false;
    $("repair-small-faces").checked = Boolean(r.small_faces);
    $("repair-make-solids").checked = Boolean(r.make_solids);
    $("polygon-outer").value = JSON.stringify(g.outer || [[0, 0], [8, 0], [8, 5], [0, 5]]);
    $("polygon-holes").value = JSON.stringify(g.kind === "polygon" ? g.holes || [] : []);
    $("hole-rows").replaceChildren();
    if (g.kind === "plate") (g.holes || []).forEach((hole) => addHole(hole, false));
    $("source-file").textContent = state.filename || "Aucun fichier sélectionné";
    state.report = null; state.preview = null; state.generatedSignature = "";
    state.selectedFace = null; $("selection-info").hidden = true;
    setMode(project.mode || "planar", false);
    state.loading = false;
    changed(); resetResults(); fitView(true);
    if (!silent) log(`Projet « ${project.name || "Sans titre"} » ouvert.`);
  }

  function changed() {
    if (state.loading) return;
    $("header-project-name").textContent = $("project-name").value.trim() || "Sans titre";
    updateVisibility();
    if (!state.preview) buildOutline();
    if (state.report && !state.currentJob) {
      const stale = signature() !== state.generatedSignature;
      $("result-state").textContent = stale ? "À recalculer" : "Calculé";
      $("result-state").className = `result-state ${stale ? "stale" : "ready"}`;
      $("view-state-label").textContent = stale ? "Maillage précédent · paramètres modifiés" : "Maillage calculé";
      $("export-button").disabled = stale;
      $("bundle-button").disabled = stale;
    }
    drawSoon();
  }

  function switchPanel(id) {
    document.querySelectorAll(".config-panel").forEach((panel) => { panel.hidden = panel.id !== id; panel.classList.toggle("active", panel.id === id); });
    document.querySelectorAll(".panel-tab").forEach((tab) => { const active = tab.dataset.panel === id; tab.classList.toggle("active", active); tab.setAttribute("aria-selected", String(active)); });
  }

  function resetResults() {
    $("stat-nodes").textContent = "—"; $("stat-elements").textContent = "—";
    $("quality-min").textContent = "—"; $("quality-label").textContent = "Non calculée";
    $("quality-meter-fill").style.width = "0%";
    $("results-intro").textContent = "Les contrôles apparaîtront ici après la génération.";
    $("result-state").textContent = "En attente"; $("result-state").className = "result-state";
    $("cell-types").replaceChildren(smallText("Aucun élément calculé"));
    $("group-list").replaceChildren(smallText("Domaines et frontières nommés"));
    $("group-count").textContent = "0";
    $("diagnostic-content").textContent = "Aucun diagnostic disponible.";
    $("warnings").hidden = true;
    $("export-button").disabled = true; $("bundle-button").disabled = true;
    $("view-state-label").textContent = "Aperçu géométrique · avant maillage";
  }

  function smallText(text) { const span = document.createElement("span"); span.className = "muted-small"; span.textContent = text; return span; }
  function finite(value) { return typeof value === "number" && Number.isFinite(value); }
  function prettyNumber(value, decimals = 3) { return finite(value) ? value.toLocaleString("fr-FR", { maximumFractionDigits: decimals }) : "—"; }
  function firstNumber(object, keys) { for (const key of keys) { if (finite(object?.[key])) return object[key]; } return null; }
  function countText(value) { return finite(value) ? numberFormatter.format(value) : "—"; }

  function renderReport(report, exports = []) {
    const quality = report.quality || {};
    $("stat-nodes").textContent = countText(report.nodes ?? report.node_count);
    $("stat-elements").textContent = countText(report.elements ?? report.element_count);
    const min = firstNumber(quality, ["minimum", "min_sicn", "minSICN", "min_quality", "min_scaled_jacobian", "min"]);
    $("quality-min").textContent = min !== null && min !== 0 && Math.abs(min) < 0.001 ? min.toExponential(1).replace(".", ",") : prettyNumber(min);
    $("quality-label").textContent = min === null ? "Non disponible" : min > 0.3 ? "Forme régulière" : min > 0.1 ? "À examiner" : min > 0 ? "Qualité faible" : "Éléments à corriger";
    $("quality-meter-fill").style.width = `${Math.max(0, Math.min(1, min || 0)) * 100}%`;
    $("quality-meter-fill").style.background = min > 0.3 ? "#4c9870" : min > 0.1 ? "#b89d57" : "#c97859";
    const mean = firstNumber(quality, ["mean", "mean_sicn", "meanSICN", "mean_quality", "mean_scaled_jacobian"]);
    $("quality-detail").textContent = `Indicateur de forme${mean !== null ? ` · moyenne ${prettyNumber(mean)}` : ""}. À compléter par les contrôles du solveur.`;
    $("results-intro").textContent = `${report.mode === "volume" ? "Volume" : report.mode === "surface" ? "Surface" : "Domaine 2D"} maillé${finite(report.timings?.total_seconds) ? ` en ${prettyNumber(report.timings.total_seconds, 2)} s` : ""}. Vérifiez les détails avant le calcul.`;
    $("cell-types").replaceChildren();
    const names = { triangle: "Triangles", triangle6: "Triangles quadratiques", quad: "Quadrilatères", quad8: "Quadrilatères quadratiques", quad9: "Quadrilatères quadratiques", tetra: "Tétraèdres", tetra10: "Tétraèdres quadratiques", hexahedron: "Hexaèdres", hexa: "Hexaèdres", line: "Segments", line3: "Segments quadratiques", vertex: "Points" };
    let cells = report.cell_counts || {};
    if (Array.isArray(cells)) cells = Object.fromEntries(cells.map((item) => [item.type || item.name, item.count]));
    for (const [type, count] of Object.entries(cells)) {
      const row = document.createElement("div"); row.className = "cell-type";
      const label = document.createElement("span"); label.textContent = names[type] || type;
      const amount = document.createElement("b"); amount.textContent = countText(typeof count === "object" ? count.count : count);
      row.append(label, amount); $("cell-types").append(row);
    }
    if (!$("cell-types").children.length) $("cell-types").append(smallText("Consulter le diagnostic détaillé"));
    let groups = report.groups || [];
    if (!Array.isArray(groups)) groups = Object.entries(groups).map(([name, detail]) => typeof detail === "object" ? { name, ...detail } : { name, count: detail });
    $("group-count").textContent = groups.length;
    $("group-list").replaceChildren();
    for (const group of groups) {
      const chip = document.createElement("span"); chip.className = "group-chip";
      chip.textContent = typeof group === "string" ? group : group.name || `Groupe ${group.tag ?? group.id ?? ""}`;
      chip.title = typeof group === "string" ? group : `${group.name || "Groupe"}${group.dimension !== undefined || group.dim !== undefined ? ` · dimension ${group.dimension ?? group.dim}` : ""}${group.count ? ` · ${group.count} éléments` : ""}`;
      $("group-list").append(chip);
    }
    if (!groups.length) $("group-list").append(smallText("Aucun groupe déclaré"));
    $("diagnostic-content").textContent = JSON.stringify(exports.length ? { ...report, exports } : report, null, 2);
    const warnings = [...(report.warnings || [])];
    const failedExports = exports.filter((item) => item.status !== "ok");
    if (failedExports.length) warnings.push(`Formats non disponibles pour ce maillage : ${failedExports.map((item) => item.format).join(", ")}. Les motifs sont conservés dans le diagnostic et l’archive.`);
    for (const option of $("export-format").options) {
      const exported = exports.find((item) => item.format === option.value);
      option.disabled = Boolean(exported && exported.status !== "ok");
      option.title = exported?.error || exported?.message || "";
    }
    if ($("export-format").selectedOptions[0]?.disabled) {
      const available = [...$("export-format").options].find((option) => !option.disabled);
      if (available) $("export-format").value = available.value;
    }
    $("warnings").replaceChildren();
    $("warnings").hidden = !warnings.length;
    if (warnings.length) {
      const title = document.createElement("b"); title.textContent = "Points d’attention";
      const list = document.createElement("ul");
      warnings.forEach((warning) => { const item = document.createElement("li"); item.textContent = typeof warning === "string" ? warning : warning.message || JSON.stringify(warning); list.append(item); });
      $("warnings").append(title, list);
    }
    $("result-state").textContent = "Calculé"; $("result-state").className = "result-state ready";
    $("export-button").disabled = false; $("bundle-button").disabled = false;
    $("view-state-label").textContent = "Maillage calculé";
  }

  function setBusy(busy, message = "") {
    $("generate-button").hidden = busy; $("cancel-button").hidden = !busy;
    $("compute-overlay").hidden = !busy;
    $("generate-button").disabled = !state.info?.backend?.available;
    $("generation-hint").textContent = busy ? "Le calcul s’exécute sur cet ordinateur" : "Prêt à générer · paramètres locaux";
    if (message) $("compute-message").textContent = message;
    $("compute-title").textContent = "Construction du maillage";
    $("open-project-button").disabled = busy;
    $("import-button").disabled = busy;
    $("examples-button").disabled = busy;
    if (busy) {
      state.started = Date.now();
      clearInterval(state.elapsedTimer);
      state.elapsedTimer = setInterval(() => { $("compute-time").textContent = `${Math.floor((Date.now() - state.started) / 1000)} s`; }, 500);
    } else clearInterval(state.elapsedTimer);
  }

  async function generate() {
    if (state.currentJob) return;
    try {
      const project = projectFromForm();
      if (project.geometry.kind === "import" && !state.uploadId) throw new Error("Sélectionnez d’abord un fichier de géométrie.");
      if (project.geometry.kind === "plate" && project.geometry.corner_radius > 0 && project.geometry.chamfer > 0) throw new Error("Choisissez un congé ou un chanfrein : l’autre valeur doit être zéro.");
      setBusy(true, "Préparation de la géométrie…");
      state.pendingSignature = signature();
      log("Génération du maillage en cours…", "running");
      const job = await request("/api/jobs", { method: "POST", data: { project, upload_id: state.uploadId, use_advisor: $("use-advisor").checked } });
      state.currentJob = job.id;
      if (!state.currentJob) throw new Error("Le calcul n’a pas pu être créé.");
      await pollJob(job.id);
    } catch (error) {
      state.currentJob = null; setBusy(false); showError(error);
    }
  }

  async function pollJob(id) {
    clearTimeout(state.pollTimer);
    if (state.currentJob !== id) return;
    try {
      const job = await request(`/api/jobs/${encodeURIComponent(id)}`);
      const message = job.message || (job.status === "queued" ? "Calcul en attente…" : "Le moteur construit les éléments…");
      $("compute-message").textContent = message;
      if (job.status === "completed") {
        state.currentJob = null; state.completedJob = id; setBusy(false);
        state.report = job.report || {};
        state.preview = job.preview || null;
        state.generatedSignature = state.pendingSignature;
        state.selectedFace = null;
        $("selection-info").hidden = true;
        renderReport(state.report, job.files || job.exports?.results || []);
        if (state.preview) { buildMeshView(state.preview); fitView(true); }
        updateVisibility();
        if (job.advisor) {
          const reasons = { guide_unavailable: "aucun guide entraîné", imported_geometry_outside_training_domain: "pièce importée hors du domaine d’apprentissage", backend_version_changed: "version du moteur différente de l’entraînement", meshing_configuration_outside_training_domain: "réglages différents de ceux de l’entraînement", geometry_family_outside_training_domain: "famille de forme hors du domaine validé", heldout_improvement_not_demonstrated: "aucun gain démontré sur les formes réservées", shape_features_outside_training_domain: "dimensions hors du domaine appris" };
          log(job.advisor.use_model ? `Guide appliqué · algorithme ${job.advisor.algorithm}.` : `Algorithme demandé conservé · ${reasons[job.advisor.reason] || "guide non appliqué dans ce cas"}.`);
        }
        log(`Maillage terminé · ${countText(state.report.elements)} éléments · ${countText(state.report.nodes)} nœuds.`);
        toast("Le maillage est prêt. Vous pouvez l’examiner et l’exporter.");
        changed();
      } else if (["failed", "cancelled", "canceled"].includes(job.status)) {
        state.currentJob = null; setBusy(false);
        if (job.status === "failed") throw new Error(job.error || job.message || "Le maillage a échoué. Consultez la géométrie et les paramètres.");
        log("Calcul arrêté. Les paramètres du projet sont conservés.");
        toast("Le calcul a été arrêté.", "warning");
      } else {
        state.pollTimer = setTimeout(() => pollJob(id), 700);
      }
    } catch (error) {
      state.currentJob = null; setBusy(false); showError(error);
    }
  }

  async function cancelJob() {
    if (!state.currentJob) return;
    try {
      $("cancel-button").disabled = true;
      await request(`/api/jobs/${encodeURIComponent(state.currentJob)}/cancel`, { method: "POST", data: {} });
      log("Arrêt demandé…", "running");
    } catch (error) { showError(error); }
    finally { $("cancel-button").disabled = false; }
  }

  async function uploadCAD(file) {
    if (!file) return;
    try {
      log(`Import de ${file.name}…`, "running");
      const data = await request("/api/uploads", { method: "POST", data: { filename: file.name, content_base64: await fileAsBase64(file) } });
      state.uploadId = data.id; state.filename = data.filename || file.name;
      $("geometry-kind").value = "import";
      $("source-file").textContent = `${state.filename} · ${prettyNumber(file.size / 1024, 0)} Ko`;
      $("project-name").value = file.name.replace(/\.[^.]+$/, "");
      state.preview = null; state.report = null; state.completedJob = null; resetResults();
      if (/\.stl$/i.test(file.name)) setMode("surface", false);
      switchPanel("geometry-panel"); changed(); fitView(true);
      log(`${state.filename} importé. Choisissez le type de domaine puis générez le maillage.`);
      toast("Géométrie importée. Le diagnostic sera établi pendant le calcul.");
    } catch (error) { showError(error); }
    finally { $("cad-file-input").value = ""; }
  }

  async function saveProject() {
    try {
      $("save-project-button").disabled = true;
      const project = projectFromForm();
      const result = await request("/api/projects/save", { method: "POST", data: { project, upload_id: state.uploadId }, blob: true });
      downloadBlob(result.blob, filenameFrom(result.disposition, `${project.name}.amp`));
      log(`Projet « ${project.name} » enregistré avec ses paramètres${state.uploadId ? " et sa géométrie" : ""}.`);
      toast("Projet enregistré. Conservez ce fichier pour reprendre votre travail.");
    } catch (error) { showError(error); }
    finally { $("save-project-button").disabled = false; }
  }

  async function openProject(file) {
    if (!file) return;
    try {
      if (/\.json$/i.test(file.name)) {
        const data = JSON.parse(await file.text());
        loadProject(data.project || data);
        if ((data.project || data).geometry?.kind === "import") toast("Ce JSON ne contient pas la pièce CAO. Sélectionnez à nouveau son fichier.", "warning");
      } else {
        const data = await request("/api/projects/open", { method: "POST", data: { filename: file.name, content_base64: await fileAsBase64(file) } });
        loadProject(data.project, { uploadId: data.upload_id || null, filename: data.filename || data.source_filename || "" });
      }
      toast("Projet ouvert. Les paramètres sont prêts à être utilisés.");
    } catch (error) { showError(error); }
    finally { $("project-file-input").value = ""; }
  }

  async function exportResult(bundle = false) {
    if (!state.completedJob || !state.report) return;
    try {
      $("export-button").disabled = true; $("bundle-button").disabled = true;
      const format = $("export-format").value;
      const path = `/api/jobs/${encodeURIComponent(state.completedJob)}/${bundle ? "bundle" : `download?format=${encodeURIComponent(format)}`}`;
      const result = await request(path, { blob: true });
      const extension = state.info?.formats?.[format]?.extension || format;
      downloadBlob(result.blob, filenameFrom(result.disposition, `${state.report.project_name || "maillage"}.${bundle ? "zip" : String(extension).replace(/^\./, "")}`));
      log(bundle ? "Archive des exports préparée." : `Maillage exporté · ${format.toUpperCase()}.`);
      toast(bundle ? "L’archive des exports est prête." : "Maillage exporté. Consultez les notes de compatibilité pour votre solveur.");
    } catch (error) { showError(error); }
    finally { changed(); }
  }

  async function trainGuide() {
    if (state.trainingJob) return;
    try {
      $("train-button").disabled = true;
      $("training-cancel").hidden = false;
      $("training-status").textContent = "Préparation des exemples et des comparaisons…";
      $("training-result").hidden = true;
      const job = await request("/api/training", { method: "POST", data: {} });
      state.trainingJob = job.id;
      log("Apprentissage du guide démarré.", "running");
      pollTraining(job.id);
    } catch (error) { state.trainingJob = null; $("train-button").disabled = false; $("training-cancel").hidden = true; showError(error); }
  }

  async function pollTraining(id) {
    if (state.trainingJob !== id) return;
    try {
      const job = await request(`/api/jobs/${encodeURIComponent(id)}`);
      $("training-status").textContent = job.message || "Comparaison des stratégies en cours…";
      if (job.status === "completed") {
        state.trainingJob = null; $("train-button").disabled = false; $("training-cancel").hidden = true;
        const report = job.report || {};
        $("training-result").replaceChildren();
        const heading = document.createElement("strong"); heading.textContent = report.deploy_enabled === false ? "Guide entraîné · application désactivée" : "Guide entraîné";
        const detail = document.createElement("div");
        const improvement = firstNumber(report, ["validation_improvement_over_fixed", "improvement", "mean_improvement"]);
        detail.textContent = improvement === null ? "Le rapport d’évaluation est disponible dans le diagnostic." : improvement > 0 ? `Écart sur les cas réservés : +${prettyNumber(improvement, 5)}.` : `Écart sur les cas réservés : ${prettyNumber(improvement, 5)}. Aucun gain démontré par rapport à la stratégie fixe.`;
        const button = document.createElement("button"); button.className = "text-button"; button.textContent = "Voir le rapport";
        button.addEventListener("click", () => { $("diagnostic-content").textContent = JSON.stringify(report, null, 2); document.querySelector(".diagnostics").open = true; });
        $("training-result").append(heading, detail, button); $("training-result").hidden = false;
        $("training-status").textContent = report.deploy_enabled === false ? "Le guide est enregistré. L’algorithme demandé sera conservé tant qu’un gain n’est pas démontré." : "Entraînement terminé. Le guide peut être demandé dans Maillage, dans son domaine validé.";
        log("Entraînement terminé. Consultez la comparaison sur les cas réservés.");
        toast("Le guide est entraîné ; ses résultats de validation sont disponibles.");
      } else if (["cancelled", "canceled"].includes(job.status)) {
        state.trainingJob = null; $("train-button").disabled = false; $("training-cancel").hidden = true;
        $("training-status").textContent = "Entraînement arrêté. Le guide précédent est conservé.";
        log("Entraînement arrêté.");
      } else if (job.status === "failed") {
        throw new Error(job.error || job.message || "L’entraînement n’a pas pu être terminé.");
      } else state.trainingTimer = setTimeout(() => pollTraining(id), 1000);
    } catch (error) { state.trainingJob = null; $("train-button").disabled = false; $("training-cancel").hidden = true; $("training-status").textContent = "L’entraînement a été interrompu."; showError(error); }
  }

  function showExamples() {
    const examples = state.info?.examples?.length ? state.info.examples : fallbackExamples;
    $("example-grid").replaceChildren();
    examples.forEach((example) => {
      const button = document.createElement("button"); button.className = "example-card";
      const picture = document.createElement("div"); picture.className = "example-icon";
      picture.append(icon(example.project?.mode === "volume" ? "shape" : "grid"));
      const name = document.createElement("b"); name.textContent = example.name;
      const description = document.createElement("small"); description.textContent = example.description || "Géométrie et paramètres prêts à explorer.";
      const bottom = document.createElement("div");
      const action = document.createElement("span"); action.textContent = "Ouvrir le modèle →"; action.style.fontSize = "9px";
      const mode = document.createElement("span"); mode.className = "example-mode"; mode.textContent = example.project?.mode === "volume" ? "VOLUME" : example.project?.mode === "surface" ? "SURFACE" : "2D";
      bottom.append(action, mode); button.append(picture, name, description, bottom);
      button.addEventListener("click", () => { try { loadProject(example.project); $("examples-dialog").close(); } catch (error) { showError(error); } });
      $("example-grid").append(button);
    });
    $("examples-dialog").showModal();
  }

  /* The viewer draws the actual nodes and surface faces returned by the engine.
     Before meshing it draws only a geometry outline, explicitly labelled. */
  function boundsOf(points) {
    if (!points.length) return [[0, 0, 0], [1, 1, 1]];
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const point of points) for (let d = 0; d < 3; d++) { lo[d] = Math.min(lo[d], Number(point[d]) || 0); hi[d] = Math.max(hi[d], Number(point[d]) || 0); }
    return [lo, hi];
  }

  function buildMeshView(preview) {
    const points = Array.isArray(preview.points) ? preview.points.map((p) => [Number(p[0]), Number(p[1]), Number(p[2] || 0)]) : [];
    let faces = Array.isArray(preview.surface_triangles) ? preview.surface_triangles : [];
    if (!faces.length) {
      faces = [];
      for (const block of preview.cells || []) {
        if (/^triangle/.test(block.type)) faces.push(...block.data.map((row) => row.slice(0, 3)));
        else if (/^quad/.test(block.type)) faces.push(...block.data.map((row) => row.slice(0, 4)));
      }
    }
    faces = faces.filter((face) => face.length >= 3 && face.every((index) => Number.isInteger(index) && index >= 0 && index < points.length));
    const maximum = 50000;
    let sampled = Boolean(preview.truncated);
    if (faces.length > maximum) { const stride = Math.ceil(faces.length / maximum); faces = faces.filter((_, i) => i % stride === 0); sampled = true; }
    const edges = [], seen = new Set();
    for (const face of faces) for (let i = 0; i < face.length; i++) {
      const a = face[i], b = face[(i + 1) % face.length], key = a < b ? `${a}:${b}` : `${b}:${a}`;
      if (!seen.has(key)) { seen.add(key); edges.push([a, b]); }
    }
    if (!faces.length) for (const block of preview.cells || []) if (/^line/.test(block.type)) edges.push(...block.data.map((row) => row.slice(0, 2)));
    state.geometry = { points, faces, edges, bounds: preview.bounds || boundsOf(points) };
    if (sampled) {
      toast("L’aperçu est allégé pour rester fluide. Les exports conservent le maillage complet.", "warning");
      log("Affichage allégé ; les fichiers de maillage conservent tous les éléments.");
    }
  }

  function buildOutline() {
    let project;
    try { project = projectFromForm(); } catch (_) { return; }
    const g = project.geometry, points = [], edges = [];
    const line = (a, b) => { const i = points.length; points.push(a, b); edges.push([i, i + 1]); };
    const ring = (polygon, z = 0) => {
      const first = points.length;
      polygon.forEach((p) => points.push([p[0], p[1], z]));
      polygon.forEach((_, i) => edges.push([first + i, first + (i + 1) % polygon.length]));
    };
    const circle = (x, y, radius, z = 0, plane = 0) => {
      const polygon = Array.from({ length: 72 }, (_, i) => { const a = 2 * Math.PI * i / 72; return plane === 1 ? [radius * Math.cos(a), 0, radius * Math.sin(a)] : plane === 2 ? [0, radius * Math.cos(a), radius * Math.sin(a)] : [x + radius * Math.cos(a), y + radius * Math.sin(a), z]; });
      const offset = points.length; points.push(...polygon); polygon.forEach((_, i) => edges.push([offset + i, offset + (i + 1) % polygon.length]));
    };
    let outline = null;
    if (g.kind === "plate") {
      const w = g.width, h = g.height, r = Math.min(g.corner_radius || 0, w / 2, h / 2), c = Math.min(g.chamfer || 0, w / 2, h / 2);
      if (r > 0) {
        outline = [];
        [[w - r, h - r, 0], [r, h - r, Math.PI / 2], [r, r, Math.PI], [w - r, r, 3 * Math.PI / 2]].forEach(([x, y, angle]) => {
          for (let i = 0; i <= 14; i++) outline.push([x + r * Math.cos(angle + i * Math.PI / 28), y + r * Math.sin(angle + i * Math.PI / 28)]);
        });
      } else if (c > 0) outline = [[c, 0], [w - c, 0], [w, c], [w, h - c], [w - c, h], [c, h], [0, h - c], [0, c]];
      else outline = [[0, 0], [w, 0], [w, h], [0, h]];
      ring(outline); (g.holes || []).forEach((hole) => circle(hole.x, hole.y, hole.radius));
      if (project.mode === "volume") {
        ring(outline, g.thickness); (g.holes || []).forEach((hole) => circle(hole.x, hole.y, hole.radius, g.thickness));
        const stride = Math.max(1, Math.floor(outline.length / 8));
        outline.forEach((p, i) => { if (i % stride === 0) line([p[0], p[1], 0], [p[0], p[1], g.thickness]); });
      }
    } else if (g.kind === "box" || g.kind === "bracket") {
      outline = g.kind === "box" ? [[0, 0], [g.width, 0], [g.width, g.height], [0, g.height]] : [[0, 0], [g.width, 0], [g.width, g.wall], [g.wall, g.wall], [g.wall, g.height], [0, g.height]];
      ring(outline); ring(outline, g.depth);
      outline.forEach((p) => line([p[0], p[1], 0], [p[0], p[1], g.depth]));
    } else if (g.kind === "sphere") { circle(0, 0, g.radius); circle(0, 0, g.radius, 0, 1); circle(0, 0, g.radius, 0, 2); }
    else if (g.kind === "polygon" && Array.isArray(g.outer)) {
      if (g.outer.every((p) => Array.isArray(p) && p.length >= 2 && p.every(Number.isFinite))) ring(g.outer);
      if (Array.isArray(g.holes)) g.holes.forEach((hole) => { if (Array.isArray(hole) && hole.every((p) => Array.isArray(p) && p.length >= 2 && p.every(Number.isFinite))) ring(hole); });
    }
    state.geometry = { points, edges, faces: [], bounds: boundsOf(points) };
  }

  function fitView(resetRotation = false) {
    state.view.zoom = 1; state.view.panX = 0; state.view.panY = 0;
    if (resetRotation) {
      state.view.yaw = currentMode() === "planar" ? 0 : -0.65;
      state.view.pitch = currentMode() === "planar" ? 0 : 0.52;
    }
    drawSoon();
  }

  function transform(point) {
    const yaw = state.view.yaw, pitch = state.view.pitch;
    const x = Math.cos(yaw) * point[0] + Math.sin(yaw) * point[2];
    const z = -Math.sin(yaw) * point[0] + Math.cos(yaw) * point[2];
    return [x, Math.cos(pitch) * point[1] - Math.sin(pitch) * z, Math.sin(pitch) * point[1] + Math.cos(pitch) * z];
  }

  function drawSoon() {
    if (state.framesPending) return;
    state.framesPending = true;
    requestAnimationFrame(() => { state.framesPending = false; draw(); });
  }

  function draw() {
    const canvas = $("mesh-view"), ctx = canvas.getContext("2d");
    if (!ctx) return;
    const rect = canvas.getBoundingClientRect(), ratio = Math.min(window.devicePixelRatio || 1, 2);
    if (!rect.width || !rect.height) return;
    if (canvas.width !== Math.round(rect.width * ratio) || canvas.height !== Math.round(rect.height * ratio)) {
      canvas.width = Math.round(rect.width * ratio); canvas.height = Math.round(rect.height * ratio);
    }
    const width = rect.width, height = rect.height;
    state.cssWidth = width; state.cssHeight = height;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, width, height);
    drawGrid(ctx, width, height);
    const { points, faces, edges, bounds } = state.geometry;
    if (!points.length) { drawAxes(ctx, width, height); return; }
    const center = [0, 1, 2].map((d) => (bounds[0][d] + bounds[1][d]) / 2);
    const diameter = Math.max(Math.hypot(...[0, 1, 2].map((d) => bounds[1][d] - bounds[0][d])), 1e-14);
    const scale = Math.min(width * 0.83, height * 0.72) / diameter * state.view.zoom;
    const projected = points.map((point) => {
      const rotated = transform(point.map((x, d) => x - center[d]));
      return [width / 2 + rotated[0] * scale + state.view.panX, height / 2 - rotated[1] * scale + state.view.panY, rotated[2]];
    });
    state.projected = projected;
    if (faces.length && $("show-faces").checked) {
      const order = faces.map((face, index) => ({ index, z: face.reduce((sum, p) => sum + projected[p][2], 0) / face.length })).sort((a, b) => a.z - b.z);
      state.faceOrder = order;
      for (const item of order) {
        const face = faces[item.index], a = projected[face[0]], b = projected[face[1]], c = projected[face[2]];
        const ux = b[0] - a[0], uy = b[1] - a[1], uz = (b[2] - a[2]) * scale;
        const vx = c[0] - a[0], vy = c[1] - a[1], vz = (c[2] - a[2]) * scale;
        const nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
        const norm = Math.hypot(nx, ny, nz) || 1;
        const lighting = Math.min(1, Math.max(0, Math.abs((nx * -0.25 + ny * -0.4 + nz * 0.88) / norm)));
        ctx.fillStyle = item.index === state.selectedFace ? "#6bc9ae" : `rgb(${Math.round(148 + 38 * lighting)},${Math.round(190 + 27 * lighting)},${Math.round(169 + 30 * lighting)})`;
        ctx.beginPath(); ctx.moveTo(a[0], a[1]);
        for (let i = 1; i < face.length; i++) ctx.lineTo(projected[face[i]][0], projected[face[i]][1]);
        ctx.closePath(); ctx.fill();
      }
    }
    if ($("show-edges").checked || !faces.length) {
      ctx.lineWidth = faces.length ? 0.55 : 1.4;
      ctx.strokeStyle = faces.length ? "#3e796a9c" : "#4b8e73";
      ctx.beginPath();
      for (const [a, b] of edges) { if (!projected[a] || !projected[b]) continue; ctx.moveTo(projected[a][0], projected[a][1]); ctx.lineTo(projected[b][0], projected[b][1]); }
      ctx.stroke();
    }
    drawAxes(ctx, width, height);
  }

  function drawGrid(ctx, width, height) {
    const gap = 28;
    ctx.fillStyle = "#a7beb028";
    for (let y = 26; y < height; y += gap) for (let x = 22; x < width; x += gap) { ctx.beginPath(); ctx.arc(x, y, 0.7, 0, 2 * Math.PI); ctx.fill(); }
    ctx.strokeStyle = "#c8d8ca30"; ctx.lineWidth = 0.6;
    ctx.beginPath(); ctx.moveTo(width / 2, 52); ctx.lineTo(width / 2, height - 72); ctx.moveTo(65, height / 2); ctx.lineTo(width - 65, height / 2); ctx.stroke();
  }

  function drawAxes(ctx, width, height) {
    const origin = [width - 55, height - 64], length = 24;
    const axes = [[[1, 0, 0], "x", "#bb8073"], [[0, 1, 0], "y", "#729f78"], [[0, 0, 1], "z", "#7a9fbc"]];
    ctx.font = "9px -apple-system, sans-serif";
    axes.forEach(([direction, name, color]) => {
      const p = transform(direction), x = origin[0] + p[0] * length, y = origin[1] - p[1] * length;
      ctx.strokeStyle = color; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.moveTo(...origin); ctx.lineTo(x, y); ctx.stroke(); ctx.fillStyle = color; ctx.fillText(name, x + 4, y + 3);
    });
    ctx.fillStyle = "#93a795"; ctx.beginPath(); ctx.arc(...origin, 2, 0, 2 * Math.PI); ctx.fill();
  }

  function selectFace(x, y) {
    if (!state.preview || !state.geometry.faces.length) return;
    const pointInTriangle = (p, a, b, c) => {
      const sign = (p1, p2, p3) => (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1]);
      const d1 = sign(p, a, b), d2 = sign(p, b, c), d3 = sign(p, c, a);
      return !((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0));
    };
    const order = state.faceOrder || state.geometry.faces.map((_, index) => ({ index }));
    state.selectedFace = null;
    for (let j = order.length - 1; j >= 0; j--) {
      const i = order[j].index, face = state.geometry.faces[i];
      for (let k = 1; k < face.length - 1; k++) {
        if (pointInTriangle([x, y], state.projected[face[0]], state.projected[face[k]], state.projected[face[k + 1]])) { state.selectedFace = i; break; }
      }
      if (state.selectedFace !== null) break;
    }
    $("selection-info").hidden = state.selectedFace === null;
    if (state.selectedFace !== null) {
      const face = state.geometry.faces[state.selectedFace];
      $("selection-info").textContent = `Face d’aperçu ${numberFormatter.format(state.selectedFace + 1)} · ${face.length} sommets. Cette sélection sert à explorer la vue ; les identifiants du solveur sont dans le maillage exporté.`;
    }
    drawSoon();
  }

  function bindViewer() {
    const canvas = $("mesh-view");
    canvas.addEventListener("pointerdown", (event) => {
      state.drag = { x: event.clientX, y: event.clientY, initialX: event.clientX, initialY: event.clientY, pan: event.shiftKey || event.button === 1 || event.button === 2, moved: false };
      canvas.setPointerCapture(event.pointerId); canvas.style.cursor = "grabbing";
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!state.drag) return;
      const dx = event.clientX - state.drag.x, dy = event.clientY - state.drag.y;
      if (Math.hypot(event.clientX - state.drag.initialX, event.clientY - state.drag.initialY) > 3) state.drag.moved = true;
      if (state.drag.pan || event.shiftKey) { state.view.panX += dx; state.view.panY += dy; }
      else { state.view.yaw += dx * 0.008; state.view.pitch = Math.max(-Math.PI, Math.min(Math.PI, state.view.pitch + dy * 0.008)); }
      state.drag.x = event.clientX; state.drag.y = event.clientY; drawSoon();
    });
    const release = (event) => {
      if (state.drag && !state.drag.moved && event.type === "pointerup" && !state.drag.pan) { const rect = canvas.getBoundingClientRect(); selectFace(event.clientX - rect.left, event.clientY - rect.top); }
      state.drag = null; canvas.style.cursor = "grab";
    };
    canvas.addEventListener("pointerup", release); canvas.addEventListener("pointercancel", release);
    canvas.addEventListener("contextmenu", (event) => event.preventDefault());
    canvas.addEventListener("wheel", (event) => { event.preventDefault(); state.view.zoom = Math.max(0.06, Math.min(50, state.view.zoom * Math.exp(-event.deltaY * 0.001))); drawSoon(); }, { passive: false });
    canvas.addEventListener("dblclick", () => fitView());
    canvas.addEventListener("keydown", (event) => { if (["+", "=", "-", "0"].includes(event.key)) { event.preventDefault(); if (event.key === "0") fitView(); else { state.view.zoom *= event.key === "-" ? 0.8 : 1.25; drawSoon(); } } });
    new ResizeObserver(drawSoon).observe(canvas.parentElement);
    $("fit-view").addEventListener("click", () => fitView());
    $("iso-view").addEventListener("click", () => { state.view.yaw = -0.65; state.view.pitch = 0.52; drawSoon(); });
    $("top-view").addEventListener("click", () => { state.view.yaw = 0; state.view.pitch = 0; drawSoon(); });
    $("zoom-in").addEventListener("click", () => { state.view.zoom = Math.min(50, state.view.zoom * 1.25); drawSoon(); });
    $("zoom-out").addEventListener("click", () => { state.view.zoom = Math.max(0.06, state.view.zoom / 1.25); drawSoon(); });
    $("show-faces").addEventListener("change", drawSoon); $("show-edges").addEventListener("change", drawSoon);
  }

  async function initialize() {
    bindViewer();
    document.querySelectorAll(".panel-tab").forEach((tab) => tab.addEventListener("click", () => switchPanel(tab.dataset.panel)));
    document.querySelectorAll("#mode-control button").forEach((button) => button.addEventListener("click", () => { setMode(button.dataset.mode); fitView(true); }));
    document.querySelectorAll(".config-panel input:not([data-hole]), .config-panel select, .config-panel textarea, #project-name").forEach((input) => input.addEventListener("input", changed));
    $("geometry-kind").addEventListener("change", () => { if (["box", "sphere", "bracket"].includes($("geometry-kind").value) && currentMode() === "planar") setMode("volume", false); changed(); fitView(true); });
    $("repair-enabled").addEventListener("change", () => { if ($("repair-enabled").checked && currentMode() === "volume") $("repair-make-solids").checked = true; changed(); });
    $("add-hole").addEventListener("click", () => addHole());
    $("generate-button").addEventListener("click", generate); $("cancel-button").addEventListener("click", cancelJob);
    $("save-project-button").addEventListener("click", saveProject);
    $("open-project-button").addEventListener("click", () => $("project-file-input").click());
    $("project-file-input").addEventListener("change", (event) => openProject(event.target.files[0]));
    ["import-button", "choose-cad", "empty-import-button"].forEach((id) => $(id).addEventListener("click", () => $("cad-file-input").click()));
    $("cad-file-input").addEventListener("change", (event) => uploadCAD(event.target.files[0]));
    $("examples-button").addEventListener("click", showExamples);
    $("help-button").addEventListener("click", () => $("help-dialog").showModal());
    document.querySelectorAll("[data-close-dialog]").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
    document.querySelectorAll("dialog").forEach((dialog) => dialog.addEventListener("click", (event) => { if (event.target === dialog) { const rect = dialog.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close(); } }));
    $("export-button").addEventListener("click", () => exportResult()); $("bundle-button").addEventListener("click", () => exportResult(true));
    $("train-button").addEventListener("click", trainGuide);
    $("training-cancel").addEventListener("click", async () => { if (!state.trainingJob) return; try { await request(`/api/jobs/${encodeURIComponent(state.trainingJob)}/cancel`, { method: "POST", data: {} }); } catch (error) { showError(error); } });
    $("toggle-log").addEventListener("click", () => { $("activity-log").hidden = !$("activity-log").hidden; drawSoon(); });
    loadProject(defaults, { silent: true });
    $("generate-button").disabled = true;
    try {
      const info = await request("/api/info"); state.info = info; state.token = info.token || info.csrf_token || "";
      if (info.import_formats?.formats) {
        $("cad-file-input").accept = info.import_formats.formats.map((format) => format.extension).join(",");
      }
      $("footer-version").textContent = `Atelier Mailleur ${info.version || ""}`;
      if (info.backend?.available) {
        $("backend-status").textContent = `Moteur prêt${info.backend.version ? ` · Gmsh ${info.backend.version}` : ""}`;
        $("generate-button").disabled = false;
        log("Atelier prêt. Réglez la géométrie ou ouvrez un exemple.");
      } else {
        $("backend-status").textContent = "Moteur à installer";
        showError(new Error(info.backend?.error || "Le moteur de maillage n’est pas disponible. Relancez l’installation de l’atelier."));
        $("generation-hint").textContent = "Consultez le guide d’installation";
      }
      if (info.formats) {
        $("export-format").replaceChildren();
        const entries = Array.isArray(info.formats) ? info.formats.map((item) => [item.key || item.id, item]) : Object.entries(info.formats);
        entries.forEach(([key, format]) => { const option = document.createElement("option"); option.value = key; option.textContent = format.label || format.name || key.toUpperCase(); $("export-format").append(option); });
      }
    } catch (error) { $("backend-status").textContent = "Connexion interrompue"; showError(error); }
  }

  initialize();
})();
