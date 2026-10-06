/* SI Domain Checker — front-end */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const POLL_MS = 700;

  const state = {
    job: null,
    results: [],
    filter: "all",
    query: "",
    timer: null,
    finished: false,
    uploaded: [],
    fileName: "",
  };

  /* ── tabs ─────────────────────────────────────────────── */
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("is-active"));
      document.querySelectorAll(".pane").forEach((p) => p.classList.remove("is-active"));
      tab.classList.add("is-active");
      document.querySelector(`[data-pane="${tab.dataset.tab}"]`).classList.add("is-active");
      hideError();
    });
  });

  const activeTab = () => document.querySelector(".tab.is-active").dataset.tab;

  /* ── file upload ──────────────────────────────────────── */
  const drop = $("drop");
  const fileInput = $("file");

  drop.addEventListener("click", () => fileInput.click());
  ["dragenter", "dragover"].forEach((e) =>
    drop.addEventListener(e, (ev) => {
      ev.preventDefault();
      drop.classList.add("is-over");
    })
  );
  ["dragleave", "drop"].forEach((e) =>
    drop.addEventListener(e, (ev) => {
      ev.preventDefault();
      drop.classList.remove("is-over");
    })
  );
  drop.addEventListener("drop", (ev) => {
    const f = ev.dataTransfer.files && ev.dataTransfer.files[0];
    if (f) readFile(f);
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files[0]) readFile(fileInput.files[0]);
  });

  function readFile(file) {
    const reader = new FileReader();
    reader.onload = () => {
      state.uploaded = String(reader.result)
        .split(/\r?\n/)
        .map((l) => l.trim())
        .filter((l) => l && !l.startsWith("#"));
      state.fileName = file.name;
      drop.classList.add("has-file");
      $("dropTitle").textContent = file.name;
      $("dropSub").textContent =
        state.uploaded.length.toLocaleString() + " lines · click to replace";
      hideError();
    };
    reader.onerror = () => toast("Could not read that file.", true);
    reader.readAsText(file);
  }

  /* ── collect input ────────────────────────────────────── */
  function collect() {
    const pasted = $("input").value
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith("#"));

    if (activeTab() === "upload") {
      if (state.uploaded.length) return state.uploaded;
      if (pasted.length) return pasted;
      showError("Choose a .txt file first (or switch to “Paste list”).");
      return null;
    }
    if (pasted.length) return pasted;
    if (state.uploaded.length) return state.uploaded;
    showError("Paste one domain per line, or upload a file.");
    return null;
  }

  /* ── start ────────────────────────────────────────────── */
  $("startBtn").addEventListener("click", start);
  $("input").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") start();
  });

  async function start() {
    const domains = collect();
    if (!domains) return;

    clearInterval(state.timer);
    state.timer = null;
    state.finished = false;

    const btn = $("startBtn");
    btn.disabled = true;
    btn.querySelector(".btn-text").hidden = true;
    btn.querySelector(".btn-busy").hidden = false;
    hideError();

    try {
      const res = await fetch("/api/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          domains,
          details: $("details").checked,
          delay: parseFloat($("delay").value),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Request failed.");

      resetTable();
      state.job = data.job;
      showPanels();

      if (data.job.dupes)
        toast(data.job.dupes.toLocaleString() + " duplicate lines removed");

      poll();
      state.timer = setInterval(poll, POLL_MS);
    } catch (err) {
      showError(err.message);
    } finally {
      btn.disabled = false;
      btn.querySelector(".btn-text").hidden = false;
      btn.querySelector(".btn-busy").hidden = true;
    }
  }

  /* ── poll ─────────────────────────────────────────────── */
  async function poll() {
    if (!state.job) return;
    try {
      const res = await fetch(`/api/job/${state.job.id}`);
      if (!res.ok) throw new Error("lost job");
      applyJob((await res.json()).job);
    } catch {
      /* transient network hiccup — keep trying */
    }
  }

  function applyJob(job) {
    state.job = job;
    const prev = state.results.length;
    state.results = job.results;

    renderProgress(job);
    renderStats(job.counts);
    toggleDetailCols(job.details);

    if (state.filter === "all" && !state.query) {
      if (state.results.length > prev) appendRows(prev);
    } else if (state.results.length !== prev || job.done === job.total) {
      renderAll();
    }
    updateEmpty();
    updateExport();

    if (job.status !== "running" && !state.finished) {
      state.finished = true;
      clearInterval(state.timer);
      state.timer = null;
      $("liveDot").hidden = true;
      $("barFill").classList.remove("is-running");
      $("stopBtn").hidden = true;
      $("progressTitle").textContent =
        job.status === "stopped" ? "Stopped" : "Finished";
      $("progressSub").textContent =
        `${job.done.toLocaleString()} of ${job.total.toLocaleString()} domains checked`;
      const secs = Math.max(1, job.elapsed);
      toast(
        `${job.status === "stopped" ? "Stopped" : "Done"} — ` +
          `${job.done.toLocaleString()} domains in ${secs.toFixed(0)}s ` +
          `(${(job.done / secs).toFixed(1)}/s)`
      );
    }
  }

  function updateEmpty() {
    const el = $("empty");
    if (!state.results.length) {
      el.hidden = false;
      el.textContent = "No results yet — paste a list or upload a file to begin.";
    } else if (!$("tbody").children.length) {
      el.hidden = false;
      el.textContent = "No domains match this filter.";
    } else {
      el.hidden = true;
    }
  }

  function renderProgress(job) {
    const running = job.status === "running";
    $("liveDot").hidden = !running;
    $("stopBtn").hidden = !running;
    $("barFill").classList.toggle("is-running", running);

    const pct = job.total ? (job.done / job.total) * 100 : 0;
    $("barFill").style.width = pct.toFixed(1) + "%";
    $("progressTitle").textContent = running ? "Checking…" : "Working…";
    $("progressSub").textContent =
      state.fileName && activeTab() === "upload"
        ? state.fileName
        : "rdap.register.si";
    $("progressCount").textContent =
      `${job.done.toLocaleString()} / ${job.total.toLocaleString()}`;
    $("progressRate").textContent = running
      ? `${(job.done / Math.max(0.1, job.elapsed)).toFixed(1)}/s · ${job.elapsed.toFixed(0)}s`
      : "";
  }

  function renderStats(counts) {
    const map = counts || {};
    $("stats").hidden = false;
    ["available", "registered", "reserved", "invalid", "error"].forEach((k) => {
      $(`c-${k}`).textContent = (map[k] || 0).toLocaleString();
    });
  }

  function toggleDetailCols(show) {
    document
      .querySelectorAll(".th-detail, td.detail")
      .forEach((el) => (el.hidden = !show));
  }

  /* ── table ────────────────────────────────────────────── */
  function rowHtml(r, i, isNew) {
    const detail = state.job && state.job.details;
    const cls = isNew ? " class=\"is-in\"" : "";
    return (
      `<tr${cls}>` +
      `<td class="idx">${i + 1}</td>` +
      `<td class="domain">${esc(r.domain)}</td>` +
      `<td><span class="badge badge-${r.status}">${r.status}</span></td>` +
      (detail
        ? `<td class="detail">${esc(r.registered || "—")}</td>` +
          `<td class="detail">${esc(r.expires || "—")}</td>` +
          `<td class="detail">${esc(r.registrar || "—")}</td>`
        : "") +
      `<td class="note">${esc(r.note || "")}</td>` +
      `<td class="time">${esc(r.checked_at || "")}</td>` +
      `</tr>`
    );
  }

  function matches(r) {
    if (state.filter !== "all" && r.status !== state.filter) return false;
    if (state.query && !r.domain.toLowerCase().includes(state.query)) return false;
    return true;
  }

  function appendRows(from) {
    const tbody = $("tbody");
    let html = "";
    for (let i = from; i < state.results.length; i++) {
      const r = state.results[i];
      if (matches(r)) html += rowHtml(r, i, true);
    }
    if (html) tbody.insertAdjacentHTML("beforeend", html);
    updateShowing();
    updateEmpty();
  }

  function renderAll() {
    const tbody = $("tbody");
    let html = "";
    let n = 0;
    for (let i = 0; i < state.results.length; i++) {
      const r = state.results[i];
      if (!matches(r)) continue;
      html += rowHtml(r, i, false);
      n++;
    }
    tbody.innerHTML = html;
    updateShowing(n);
    updateEmpty();
  }

  function updateShowing(shown) {
    const n = shown !== undefined ? shown : $("tbody").children.length;
    $("showing").textContent = state.results.length
      ? `${n.toLocaleString()} / ${state.results.length.toLocaleString()}`
      : "";
    updateExport();
  }

  /* ── CSV export (respects the active filter + search) ── */
  const DETAIL_COLS_JS = ["registered", "expires", "registrar", "nameservers"];

  function visibleRows() {
    return state.results.filter(matches);
  }

  function csvCell(v) {
    const s = v == null ? "" : String(v);
    return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  function buildCsv(rows) {
    const cols = ["domain", "status", "checked_at", "note"].concat(
      state.job && state.job.details ? DETAIL_COLS_JS : []
    );
    const lines = [cols.join(",")];
    for (const r of rows) lines.push(cols.map((c) => csvCell(r[c])).join(","));
    return "\uFEFF" + lines.join("\r\n") + "\r\n"; // BOM + CRLF for Excel
  }

  function updateExport() {
    const btn = $("exportBtn");
    if (!state.results.length) {
      btn.hidden = true;
      return;
    }
    const n = visibleRows().length;
    btn.hidden = false;
    btn.disabled = n === 0;
    btn.textContent =
      n === state.results.length
        ? "Download CSV"
        : `Download CSV (${n.toLocaleString()})`;
  }

  $("exportBtn").addEventListener("click", () => {
    const rows = visibleRows();
    if (!rows.length) return;

    const blob = new Blob([buildCsv(rows)], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download =
      "domains-si" + (state.filter === "all" ? "" : "-" + state.filter) + ".csv";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 4000);

    toast(
      `Downloaded ${rows.length.toLocaleString()} row` +
        (rows.length === 1 ? "" : "s")
    );
  });

  function resetTable() {
    $("tbody").innerHTML = "";
    state.results = [];
    state.query = "";
    $("search").value = "";
    updateEmpty();
    updateExport();
  }

  /* ── filters ──────────────────────────────────────────── */
  $("chips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    document.querySelectorAll(".chip").forEach((c) => c.classList.remove("is-active"));
    chip.classList.add("is-active");
    state.filter = chip.dataset.filter;
    renderAll();
  });

  let searchTimer;
  $("search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.query = e.target.value.trim().toLowerCase();
      renderAll();
    }, 130);
  });

  /* ── actions ──────────────────────────────────────────── */
  $("stopBtn").addEventListener("click", async () => {
    if (!state.job) return;
    await fetch(`/api/job/${state.job.id}/stop`, { method: "POST" }).catch(() => {});
    toast("Stopping after the current lookup…");
  });

  $("clearBtn").addEventListener("click", () => {
    clearInterval(state.timer);
    state.timer = null;
    state.job = null;
    state.uploaded = [];
    state.fileName = "";
    $("input").value = "";
    fileInput.value = "";
    drop.classList.remove("has-file");
    $("dropTitle").textContent = "Drop a .txt file here";
    $("dropSub").textContent = "or click to browse · one domain per line";
    resetTable();
    ["progressCard", "stats", "resultsCard"].forEach((id) => ($(id).hidden = true));
    $("liveDot").hidden = true;
    hideError();
  });

  /* ── helpers ──────────────────────────────────────────── */
  function showPanels() {
    ["progressCard", "stats", "resultsCard"].forEach((id) => ($(id).hidden = false));
  }
  function showError(msg) {
    $("formError").textContent = msg;
    $("formError").hidden = false;
  }
  function hideError() {
    $("formError").hidden = true;
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }
  let toastTimer;
  function toast(msg, isErr) {
    const t = $("toast");
    t.textContent = msg;
    t.classList.toggle("is-error", !!isErr);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), 4200);
  }
})();
