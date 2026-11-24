/* Clean, animated chatbot client for SEC Financial Analyst */
(() => {
  let lastPayload = null;
  const stepOrder = ["planner", "workers", "synthesizer", "auditor"];
  const RECENT_KEY = "sec_analyst_recent_queries_v1";
  let activeStepIndex = 0;
  let stepStartTime = null;
  let stepTimerId = null;

  const $ = (id) => document.getElementById(id);

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatInlineMarkdown(escapedLine) {
    return escapedLine
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*]+)\*/g, "<em>$1</em>");
  }

  function renderMarkdown(rawText) {
    if (!rawText) return "<p>No answer returned.</p>";
    const lines = escapeHtml(rawText).split(/\r?\n/);
    const html = [];
    let inUl = false;
    let inOl = false;
    let tableRows = [];

    const closeLists = () => {
      if (inUl) {
        html.push("</ul>");
        inUl = false;
      }
      if (inOl) {
        html.push("</ol>");
        inOl = false;
      }
    };

    const flushTable = () => {
      if (!tableRows.length) return;
      const cleanRows = tableRows.filter(
        (r) => !/^\|[\s\-:|]+\|$/.test(r.trim())
      );
      if (cleanRows.length) {
        html.push('<div class="overflow-x-auto"><table>');
        cleanRows.forEach((row, idx) => {
          const cells = row
            .trim()
            .replace(/^\|/, "")
            .replace(/\|$/, "")
            .split("|")
            .map((c) => formatInlineMarkdown(c.trim()));
          const tag = idx === 0 ? "th" : "td";
          html.push(
            `<tr>${cells.map((c) => `<${tag}>${c}</${tag}>`).join("")}</tr>`
          );
        });
        html.push("</table></div>");
      }
      tableRows = [];
    };

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
        closeLists();
        tableRows.push(trimmed);
        continue;
      }
      flushTable();

      if (!trimmed) {
        closeLists();
        continue;
      }
      if (/^###\s+/.test(trimmed)) {
        closeLists();
        html.push(`<h3>${formatInlineMarkdown(trimmed.replace(/^###\s+/, ""))}</h3>`);
      } else if (/^##\s+/.test(trimmed)) {
        closeLists();
        html.push(`<h2>${formatInlineMarkdown(trimmed.replace(/^##\s+/, ""))}</h2>`);
      } else if (/^#\s+/.test(trimmed)) {
        closeLists();
        html.push(`<h1>${formatInlineMarkdown(trimmed.replace(/^#\s+/, ""))}</h1>`);
      } else if (/^[-*•]\s+/.test(trimmed)) {
        if (inOl) {
          html.push("</ol>");
          inOl = false;
        }
        if (!inUl) {
          html.push("<ul>");
          inUl = true;
        }
        html.push(`<li>${formatInlineMarkdown(trimmed.replace(/^[-*•]\s+/, ""))}</li>`);
      } else if (/^\d+[.)]\s+/.test(trimmed)) {
        if (inUl) {
          html.push("</ul>");
          inUl = false;
        }
        if (!inOl) {
          html.push("<ol>");
          inOl = true;
        }
        html.push(`<li>${formatInlineMarkdown(trimmed.replace(/^\d+[.)]\s+/, ""))}</li>`);
      } else {
        closeLists();
        html.push(`<p>${formatInlineMarkdown(trimmed)}</p>`);
      }
    }
    closeLists();
    flushTable();
    return html.join("");
  }

  function formatCellValue(colName, val) {
    if (val === null || val === undefined) return "—";
    const lowerCol = String(colName).toLowerCase();
    const num = typeof val === "number" ? val : Number(val);
    if (
      !Number.isNaN(num) &&
      typeof val !== "boolean" &&
      String(val).trim() !== "" &&
      lowerCol !== "fiscal_year" &&
      lowerCol !== "id" &&
      Math.abs(num) >= 1_000_000
    ) {
      const abs = Math.abs(num);
      const sign = num < 0 ? "-" : "";
      let compact = "";
      if (abs >= 1e12) compact = `${sign}$${(abs / 1e12).toFixed(2)}T`;
      else if (abs >= 1e9) compact = `${sign}$${(abs / 1e9).toFixed(2)}B`;
      else compact = `${sign}$${(abs / 1e6).toFixed(2)}M`;
      return `<span title="${escapeHtml(String(val))}" class="text-verified font-semibold">${compact}</span>`;
    }
    return escapeHtml(String(val));
  }

  async function refreshHealth() {
    const dot = $("health-dot");
    const text = $("health-text");
    try {
      const res = await fetch("/health");
      if (!res.ok) throw new Error("API error");
      const data = await res.json();
      dot.className = "w-2 h-2 rounded-full bg-verified";
      text.textContent = `${data.model} · API Ready`;
    } catch {
      dot.className = "w-2 h-2 rounded-full bg-warn";
      text.textContent = "API Offline";
    }
  }

  async function refreshCorpus() {
    const strip = $("corpus-strip");
    if (!strip) return;
    try {
      const res = await fetch("/corpus");
      if (!res.ok) throw new Error("Corpus unavailable");
      const data = await res.json();
      const tickers = data.tickers || [];
      const years = data.fiscal_years || [];
      if (!tickers.length) {
        strip.innerHTML = `<span class="text-slate-500">No filings indexed yet — use <span class="text-accent">+ Ingest Ticker</span>.</span>`;
        return;
      }
      const yearLabel =
        years.length > 1 ? `FY${years[0]}–FY${years[years.length - 1]}` : `FY${years[0]}`;
      strip.innerHTML =
        `<span class="text-slate-500 mr-1">Indexed (${escapeHtml(yearLabel)}):</span>` +
        tickers
          .map(
            (t) =>
              `<button type="button" data-ticker="${escapeHtml(t)}" class="corpus-pill px-2 py-0.5 rounded-md bg-panel/80 border border-border text-slate-300 hover:border-accent hover:text-accent transition cursor-pointer">${escapeHtml(t)}</button>`
          )
          .join("");

      strip.querySelectorAll(".corpus-pill").forEach((pill) => {
        pill.addEventListener("click", () => {
          const input = $("question-input");
          const t = pill.dataset.ticker;
          if (!input.value.trim()) {
            input.value = `What was ${t} revenue and net income in FY2024 vs FY2023?`;
          } else if (!input.value.toUpperCase().includes(t)) {
            input.value = `${input.value.trim()} ${t}`;
          }
          input.focus();
          autoResizeTextarea();
        });
      });
    } catch {
      strip.innerHTML = "";
    }
  }

  function loadRecentQueries() {
    try {
      return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    } catch {
      return [];
    }
  }

  function saveRecentQuery(q) {
    const existing = loadRecentQueries().filter((item) => item !== q);
    const updated = [q, ...existing].slice(0, 4);
    try {
      localStorage.setItem(RECENT_KEY, JSON.stringify(updated));
    } catch {
      // ignore storage quota errors
    }
    renderRecentQueries();
  }

  function renderRecentQueries() {
    const bar = $("recent-queries-bar");
    if (!bar) return;
    const recent = loadRecentQueries();
    if (!recent.length) {
      bar.classList.add("hidden");
      bar.classList.remove("flex");
      return;
    }
    bar.classList.remove("hidden");
    bar.classList.add("flex");
    bar.innerHTML =
      `<span class="text-slate-500">Recent:</span>` +
      recent
        .map(
          (q) =>
            `<button type="button" class="recent-chip max-w-xs truncate px-2.5 py-1 rounded-full border border-border/70 bg-bg/60 text-slate-400 hover:border-accent hover:text-accent transition cursor-pointer" title="${escapeHtml(q)}">${escapeHtml(q)}</button>`
        )
        .join("");

    bar.querySelectorAll(".recent-chip").forEach((btn) => {
      btn.addEventListener("click", () => {
        const q = btn.getAttribute("title");
        $("question-input").value = q;
        runAnalyze(q);
      });
    });
  }

  function enterActiveMode(question) {
    $("hero-section").classList.add("hidden");
    $("suggestions-bar").classList.add("hidden");
    $("new-chat-btn").classList.remove("hidden");

    const main = $("workspace-main");
    main.classList.remove("justify-center");
    main.classList.add("justify-start");

    const results = $("results-section");
    results.classList.remove("hidden");
    results.classList.add("flex");
    $("active-question-pill").textContent = question;

    const composer = $("composer-wrapper");
    composer.classList.add(
      "fixed",
      "bottom-4",
      "left-1/2",
      "-translate-x-1/2",
      "px-4",
      "z-30"
    );
  }

  function resetToHero() {
    closeDrawer();
    stopStepTimer();
    $("hero-section").classList.remove("hidden");
    $("suggestions-bar").classList.remove("hidden");
    $("new-chat-btn").classList.add("hidden");

    const main = $("workspace-main");
    main.classList.add("justify-center");
    main.classList.remove("justify-start");

    const results = $("results-section");
    results.classList.add("hidden");
    results.classList.remove("flex");

    const composer = $("composer-wrapper");
    composer.classList.remove(
      "fixed",
      "bottom-4",
      "left-1/2",
      "-translate-x-1/2",
      "px-4",
      "z-30"
    );
    $("question-input").value = "";
    autoResizeTextarea();
    $("question-input").focus();
  }

  function stopStepTimer() {
    if (stepTimerId !== null) {
      clearInterval(stepTimerId);
      stepTimerId = null;
    }
  }

  function startActiveStep(stepIdx) {
    stopStepTimer();
    activeStepIndex = stepIdx;
    if (stepIdx >= stepOrder.length) return;
    const card = $(`step-${stepOrder[stepIdx]}`);
    if (!card) return;
    stepStartTime = performance.now();
    card.classList.add("step-active");
    card.querySelector(".step-dot").className = "step-dot w-2.5 h-2.5 rounded-full bg-accent animate-pulse";
    const detail = card.querySelector(".step-detail");
    detail.textContent = "Running · 0.0s";

    stepTimerId = setInterval(() => {
      const elapsed = ((performance.now() - stepStartTime) / 1000).toFixed(1);
      detail.textContent = `Running · ${elapsed}s`;
    }, 100);
  }

  function resetPipeline() {
    stopStepTimer();
    stepOrder.forEach((name) => {
      const card = $(`step-${name}`);
      if (!card) return;
      card.querySelector(".step-detail").textContent = "Waiting";
      card.querySelector(".step-dot").className = "step-dot w-2.5 h-2.5 rounded-full bg-slate-600";
      card.classList.remove("border-accent", "border-verified", "border-warn", "step-active");
    });
    startActiveStep(0);
  }

  function updateStep(nodeName, status) {
    const target = nodeName === "abstain" ? "auditor" : nodeName;
    const card = $(`step-${target}`);
    if (!card) return;
    const detail = card.querySelector(".step-detail");
    const dot = card.querySelector(".step-dot");
    const elapsed = stepStartTime ? ((performance.now() - stepStartTime) / 1000).toFixed(1) : null;

    stopStepTimer();
    card.classList.remove("step-active");

    if (status === "insufficient_evidence" || nodeName === "abstain") {
      detail.textContent = elapsed ? `Abstained · ${elapsed}s` : "Abstained";
      dot.className = "step-dot w-2.5 h-2.5 rounded-full bg-warn";
      card.classList.remove("border-verified");
      card.classList.add("border-warn");
    } else if (status === "retrying") {
      detail.textContent = "Retrying...";
      dot.className = "step-dot w-2.5 h-2.5 rounded-full bg-warn animate-pulse";
      card.classList.remove("border-verified");
      card.classList.add("border-warn");
      // Synthesizer is about to re-run
      startActiveStep(2);
      return;
    } else {
      detail.textContent = elapsed ? `Done · ${elapsed}s` : "Completed";
      dot.className = "step-dot w-2.5 h-2.5 rounded-full bg-verified";
      card.classList.remove("border-warn");
      card.classList.add("border-verified");
      const idx = stepOrder.indexOf(target);
      if (idx !== -1 && idx + 1 < stepOrder.length) {
        startActiveStep(idx + 1);
      }
    }
  }

  function selectTab(target) {
    document.querySelectorAll(".drawer-tab").forEach((b) => {
      b.className =
        "drawer-tab px-3 py-1.5 rounded-lg text-xs font-mono text-slate-400 hover:text-slate-200 cursor-pointer transition";
    });
    const activeBtn = document.querySelector(`.drawer-tab[data-tab="${target}"]`);
    if (activeBtn) {
      activeBtn.className =
        "drawer-tab px-3 py-1.5 rounded-lg text-xs font-mono bg-slate-800 text-accent cursor-pointer transition";
    }
    document.querySelectorAll(".drawer-panel").forEach((p) => p.classList.add("hidden"));
    const panel = $(`tab-${target}`);
    if (panel) panel.classList.remove("hidden");
  }

  function openDrawer() {
    const backdrop = $("drawer-backdrop");
    const drawer = $("verified-drawer");
    backdrop.classList.remove("hidden");
    requestAnimationFrame(() => backdrop.classList.remove("opacity-0"));
    drawer.classList.remove("translate-x-full");
    drawer.setAttribute("aria-hidden", "false");
  }

  function closeDrawer() {
    const backdrop = $("drawer-backdrop");
    const drawer = $("verified-drawer");
    drawer.classList.add("translate-x-full");
    drawer.setAttribute("aria-hidden", "true");
    backdrop.classList.add("opacity-0");
    setTimeout(() => backdrop.classList.add("hidden"), 280);
  }

  function renderSqlTab(sqlResults) {
    if (!sqlResults.length) {
      return `<p class="text-slate-500 font-sans">No SQL queries executed.</p>`;
    }
    return sqlResults
      .map((r) => {
        const rows = r.rows || [];
        let tableHtml = `<p class="text-slate-500 font-sans">0 rows returned.</p>`;
        if (rows.length > 0) {
          const cols = Object.keys(rows[0]).filter((c) => c !== "filing_url");
          tableHtml = `
            <div class="overflow-x-auto rounded-lg border border-border">
              <table class="w-full text-left border-collapse text-[11px] font-mono tabular-nums">
                <thead>
                  <tr class="bg-bg/90 border-b border-border text-slate-400">
                    ${cols.map((c) => `<th class="px-2.5 py-2 font-semibold">${escapeHtml(c)}</th>`).join("")}
                  </tr>
                </thead>
                <tbody>
                  ${rows
                    .map(
                      (row) => `<tr class="border-b border-border/60 hover:bg-accent/5">
                        ${cols.map((c) => `<td class="px-2.5 py-2 text-slate-200">${formatCellValue(c, row[c])}</td>`).join("")}
                      </tr>`
                    )
                    .join("")}
                </tbody>
              </table>
            </div>`;
        }
        return `
          <div class="bg-bg/70 border border-border rounded-xl p-3.5 space-y-2.5">
            ${tableHtml}
            <details class="text-[11px]">
              <summary class="text-slate-400 hover:text-accent cursor-pointer select-none">View executed SQL</summary>
              <pre class="mt-2 p-2.5 rounded-lg bg-bg border border-border text-accent overflow-x-auto whitespace-pre-wrap break-all">${escapeHtml(r.sql)}</pre>
            </details>
          </div>`;
      })
      .join("");
  }

  function renderDiffTab(diffResults) {
    if (!diffResults.length) {
      return `<p class="text-slate-500">No YoY filing diffs computed.</p>`;
    }
    return diffResults
      .map((d) => {
        const added = (d.added_paragraphs || [])
          .map(
            (p) =>
              `<div class="p-2.5 rounded-lg bg-emerald-950/40 border border-emerald-500/30 text-emerald-200"><span class="font-mono text-[10px] uppercase text-verified block mb-1">+ Added</span>${escapeHtml(p)}</div>`
          )
          .join("");
        const removed = (d.removed_paragraphs || [])
          .map(
            (p) =>
              `<div class="p-2.5 rounded-lg bg-rose-950/40 border border-rose-500/30 text-rose-200"><span class="font-mono text-[10px] uppercase text-rose-400 block mb-1">- Removed</span>${escapeHtml(p)}</div>`
          )
          .join("");
        const modified = (d.modified_paragraphs || [])
          .map(
            (m) =>
              `<div class="p-2.5 rounded-lg bg-amber-950/30 border border-amber-500/30 space-y-1.5">
                <span class="font-mono text-[10px] uppercase text-warn">~ Modified (${Math.round((m.similarity || 0) * 100)}% match)</span>
                <div class="text-slate-400 line-through">${escapeHtml(m.previous_text)}</div>
                <div class="text-slate-200">${escapeHtml(m.current_text)}</div>
              </div>`
          )
          .join("");

        return `
          <div class="bg-bg/70 border border-border rounded-xl p-3.5 space-y-3">
            <div class="flex items-center justify-between font-mono text-[11px]">
              <span class="text-accent font-semibold">${escapeHtml(d.ticker)} · ${escapeHtml(d.item_section)} (${d.previous_year} → ${d.current_year})</span>
              <span class="text-slate-400">${d.unchanged_count} unchanged</span>
            </div>
            <div class="space-y-2">${added}${modified}${removed || (!added && !modified ? '<p class="text-slate-500">No material paragraph differences detected.</p>' : "")}</div>
          </div>`;
      })
      .join("");
  }

  function renderFinal(payload) {
    stopStepTimer();
    lastPayload = payload;
    $("copy-md-btn").disabled = false;
    $("export-md-btn").disabled = false;
    $("export-json-btn").disabled = false;
    $("open-drawer-btn").disabled = false;

    const badge = $("status-badge");
    badge.classList.remove("hidden");
    if (payload.status === "ok") {
      badge.textContent = "VERIFIED";
      badge.className =
        "px-2 py-0.5 rounded-md text-[11px] font-mono font-semibold bg-emerald-950 text-verified border border-verified/40";
    } else {
      badge.textContent = "INSUFFICIENT EVIDENCE";
      badge.className =
        "px-2 py-0.5 rounded-md text-[11px] font-mono font-semibold bg-amber-950 text-warn border border-warn/40";
    }

    $("answer-body").innerHTML = renderMarkdown(payload.answer || "No answer returned.");

    const citesBar = $("citations-bar");
    const citations = payload.citations || [];
    if (citations.length > 0) {
      citesBar.classList.remove("hidden");
      citesBar.innerHTML = citations
        .map(
          (c) =>
            `<a href="${escapeHtml(c.filing_url)}" target="_blank" rel="noopener"
              class="px-2.5 py-1 rounded-lg bg-bg/80 border border-border text-xs font-mono text-accent hover:border-accent transition">
              ${escapeHtml(c.ticker)} FY${c.fiscal_year} · ${escapeHtml(c.item_section)} ↗
            </a>`
        )
        .join("");
    } else {
      citesBar.classList.add("hidden");
      citesBar.innerHTML = "";
    }

    const sqlResults = payload.sql_results || [];
    const metrics = payload.computed_metrics || [];
    const diffs = payload.diff_results || [];
    const excerpts = payload.retrieved_contexts || [];

    $("count-sql").textContent = sqlResults.length;
    $("count-math").textContent = metrics.length;
    $("count-diff").textContent = diffs.length;
    $("count-excerpts").textContent = excerpts.length;

    const totalEvidence = sqlResults.length + metrics.length + diffs.length + excerpts.length;
    $("drawer-summary-pill").textContent = totalEvidence;

    $("tab-sql").innerHTML = renderSqlTab(sqlResults);
    $("tab-math").innerHTML = metrics.length
      ? metrics
          .map(
            (m) => `<div class="bg-bg/70 border border-border rounded-xl p-3.5 space-y-1">
              <div class="flex justify-between">
                <span class="text-slate-200 font-semibold">${escapeHtml(m.name)}</span>
                <span class="text-verified font-semibold">${escapeHtml(m.formatted)}</span>
              </div>
              <div class="text-slate-400">Formula: ${escapeHtml(m.formula)}</div>
              <div class="text-slate-500">Inputs: ${escapeHtml(JSON.stringify(m.inputs))}</div>
            </div>`
          )
          .join("")
      : `<p class="text-slate-500 font-sans">No Python math operations executed.</p>`;

    $("tab-diff").innerHTML = renderDiffTab(diffs);

    $("tab-excerpts").innerHTML = excerpts.length
      ? excerpts
          .map(
            (ex) => `<div class="bg-bg/70 border border-border rounded-xl p-3.5 space-y-1.5">
              <div class="flex items-center justify-between font-mono text-[11px]">
                <span class="text-accent">${escapeHtml(ex.ticker)} FY${ex.fiscal_year} · ${escapeHtml(ex.item_section)}</span>
                <a href="${escapeHtml(ex.filing_url)}" target="_blank" rel="noopener" class="text-verified hover:underline">SEC.gov ↗</a>
              </div>
              <p class="text-slate-300 leading-relaxed">${escapeHtml(ex.content)}</p>
            </div>`
          )
          .join("")
      : `<p class="text-slate-500 font-sans">No SEC 10-K excerpts retrieved.</p>`;

    // Auto-select first non-empty drawer tab
    if (sqlResults.length) selectTab("sql");
    else if (metrics.length) selectTab("math");
    else if (diffs.length) selectTab("diff");
    else if (excerpts.length) selectTab("excerpts");
    else selectTab("sql");
  }

  async function runAnalyze(question) {
    const btn = $("analyze-btn");
    btn.disabled = true;
    btn.innerHTML = `<span class="animate-pulse">Running...</span>`;
    enterActiveMode(question);
    saveRecentQuery(question);
    resetPipeline();
    $("status-badge").classList.add("hidden");
    $("citations-bar").classList.add("hidden");
    $("answer-body").innerHTML =
      `<p class="text-slate-400 animate-pulse">Running deterministic XBRL &amp; 10-K verification pipeline...</p>`;

    try {
      const res = await fetch("/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";

        for (const block of blocks) {
          const eventLine = block.split("\n").find((l) => l.startsWith("event:"));
          const dataLine = block.split("\n").find((l) => l.startsWith("data:"));
          if (!eventLine || !dataLine) continue;
          const eventType = eventLine.replace("event:", "").trim();
          const data = JSON.parse(dataLine.replace("data:", "").trim());

          if (eventType === "node_update") {
            updateStep(data.node, data.status);
          } else if (eventType === "error") {
            stopStepTimer();
            $("answer-body").textContent = `Pipeline error: ${data.detail}`;
          } else if (eventType === "final") {
            renderFinal(data);
          }
        }
      }
    } catch (err) {
      stopStepTimer();
      $("answer-body").textContent = `Request failed: ${err.message}. Check that Postgres and llama-server are running.`;
    } finally {
      btn.disabled = false;
      btn.innerHTML = `<span>Analyze</span><span aria-hidden="true">↑</span>`;
    }
  }

  function autoResizeTextarea() {
    const ta = $("question-input");
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = `${Math.min(ta.scrollHeight, 144)}px`;
  }

  function downloadFile(filename, content, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  document.addEventListener("DOMContentLoaded", () => {
    refreshHealth();
    refreshCorpus();
    renderRecentQueries();

    const qInput = $("question-input");
    qInput.addEventListener("input", autoResizeTextarea);
    qInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const q = qInput.value.trim();
        if (q && !$("analyze-btn").disabled) {
          qInput.value = "";
          autoResizeTextarea();
          runAnalyze(q);
        }
      }
    });

    $("analyze-form").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = qInput.value.trim();
      if (q) {
        qInput.value = "";
        autoResizeTextarea();
        runAnalyze(q);
      }
    });

    $("new-chat-btn").addEventListener("click", resetToHero);

    document.querySelectorAll(".example-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const q = chip.textContent.trim();
        runAnalyze(q);
      });
    });

    // Slide-over drawer controls (closed by default)
    $("open-drawer-btn").addEventListener("click", openDrawer);
    $("close-drawer-btn").addEventListener("click", closeDrawer);
    $("drawer-backdrop").addEventListener("click", closeDrawer);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeDrawer();
    });

    document.querySelectorAll(".drawer-tab").forEach((tabBtn) => {
      tabBtn.addEventListener("click", () => selectTab(tabBtn.dataset.tab));
    });

    $("copy-md-btn").addEventListener("click", async () => {
      if (!lastPayload?.answer) return;
      try {
        await navigator.clipboard.writeText(lastPayload.answer);
        const btn = $("copy-md-btn");
        btn.textContent = "Copied!";
        setTimeout(() => (btn.textContent = "Copy"), 1500);
      } catch {
        // clipboard blocked
      }
    });

    $("export-md-btn").addEventListener("click", () => {
      if (!lastPayload) return;
      const md = [
        "# SEC Financial Analyst — Audit Memo",
        `**Status:** ${lastPayload.status}`,
        "",
        "## Analysis",
        lastPayload.answer || "",
        "",
        "## Citations",
        ...(lastPayload.citations || []).map(
          (c) => `- **${c.ticker} FY${c.fiscal_year} (${c.item_section})** — [${c.accession_number}](${c.filing_url})`
        ),
      ].join("\n");
      downloadFile("audit_memo.md", md, "text/markdown");
    });

    $("export-json-btn").addEventListener("click", () => {
      if (lastPayload) {
        downloadFile("audit_memo.json", JSON.stringify(lastPayload, null, 2), "application/json");
      }
    });

    // Ingest modal wiring
    $("open-ingest-btn").addEventListener("click", () => $("ingest-modal").classList.remove("hidden"));
    $("close-ingest-btn").addEventListener("click", () => $("ingest-modal").classList.add("hidden"));
    $("ingest-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const ticker = $("ingest-ticker").value.trim().toUpperCase();
      const years = $("ingest-years")
        .value.split(",")
        .map((y) => parseInt(y.trim(), 10))
        .filter((y) => !Number.isNaN(y));
      const include10q = Boolean($("ingest-10q")?.checked);
      const statusEl = $("ingest-status");
      const submitBtn = $("ingest-submit-btn");
      submitBtn.disabled = true;
      statusEl.textContent = `Ingesting ${ticker} ${include10q ? "10-K + 10-Q" : "10-K"} from SEC EDGAR...`;

      try {
        const res = await fetch("/ingest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticker, fiscal_years: years, include_10q: include10q }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
        statusEl.textContent = `Done: ${data.filings_processed} filings, ${data.xbrl_facts_written} XBRL facts, ${data.children_written} chunks.`;
        refreshCorpus();
      } catch (err) {
        statusEl.textContent = `Error: ${err.message}`;
      } finally {
        submitBtn.disabled = false;
      }
    });
  });
})();
