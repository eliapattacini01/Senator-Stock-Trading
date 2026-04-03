const API_BASE = window.API_BASE;

/* ── helpers ─────────────────────────────────────────────────────────────── */
function setLoading(on) {
  document.getElementById("loading")?.classList.toggle("d-none", !on);
}

function setStatus(msg = "") {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle("d-none", !msg);
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} — ${txt.slice(0, 200)}`);
  }
  return res.json();
}

function fmtMoney(n) {
  if (n == null || n === 0) return "—";
  if (n >= 1_000_000) return "$" + (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000)     return "$" + Math.round(n / 1_000) + "K";
  return "$" + Math.round(n).toLocaleString();
}

function fmtPrice(n) {
  if (n == null) return "—";
  return "$" + Number(n).toFixed(2);
}

/* ── chart instances ────────────────────────────────────────────────────── */
let donutInstance    = null;
let timelineInstance = null;
let perfInstance     = null;

/* Chart.js palette */
const PALETTE = [
  "#3b82f6","#22c55e","#f59e0b","#ef4444","#8b5cf6",
  "#06b6d4","#ec4899","#14b8a6","#f97316","#64748b",
  "#a78bfa","#34d399","#fbbf24","#f87171","#60a5fa",
];

/* ── member select ──────────────────────────────────────────────────────── */
let memberTom = null;

async function loadMembers(chamber = "") {
  try {
    let url = `${API_BASE}/senators?limit=1000`;
    if (chamber) url += `&chamber=${encodeURIComponent(chamber)}`;
    const data = await fetchJson(url);

    const select = document.getElementById("memberSelect");
    if (!select) return;

    select.innerHTML = `<option value="">— choose a member —</option>`;
    data.forEach(m => {
      const opt = document.createElement("option");
      opt.value = opt.textContent = m.full_name;
      select.appendChild(opt);
    });

    if (memberTom) memberTom.destroy();
    memberTom = new TomSelect(select, {
      maxItems: 1,
      allowEmptyOption: true,
      placeholder: "Search member…",
      create: false,
      sortField: { field: "text", direction: "asc" },
    });

    // Pre-select from URL ?person=
    const qs = new URLSearchParams(window.location.search);
    const personParam = qs.get("person");
    if (personParam) {
      memberTom.setValue(personParam, true);
    }
  } catch (err) {
    console.error(err);
    setStatus(`Error loading members: ${err.message}`);
  }
}

/* ── portfolio load ─────────────────────────────────────────────────────── */
async function loadPortfolio() {
  const person = memberTom?.getValue()?.trim();
  if (!person) {
    setStatus("Please select a member first.");
    return;
  }
  setStatus("");
  setLoading(true);
  document.getElementById("summarySection").classList.add("d-none");

  try {
    const data = await fetchJson(
      `${API_BASE}/portfolio?person=${encodeURIComponent(person)}&fetch_prices=true`
    );

    if (!data.positions?.length) {
      setStatus("No trading data found for this member.");
      return;
    }

    renderSummary(data);
    renderDonut(data.positions);
    renderTimeline(person);
    renderComposition(data.positions);
    renderPositionsTable(data.positions);
    renderPerformanceChart(person);   // async – runs in background, shows spinner

    document.getElementById("summarySection").classList.remove("d-none");

    // Update URL
    const p = new URLSearchParams(window.location.search);
    p.set("person", person);
    history.replaceState(null, "", `${location.pathname}?${p}`);

  } catch (err) {
    console.error(err);
    setStatus(`Error loading portfolio: ${err.message}`);
  } finally {
    setLoading(false);
  }
}

/* ── summary cards ──────────────────────────────────────────────────────── */
function renderSummary(data) {
  document.getElementById("memberTitle").textContent = data.person;
  document.getElementById("sumTickers").textContent  = data.positions.length;
  document.getElementById("sumBought").textContent   = fmtMoney(data.total_bought);
  document.getElementById("sumSold").textContent     = fmtMoney(data.total_sold);

  // Chamber badge – infer from members list (we can't easily get it from portfolio endpoint)
  // Just leave it blank for now
  document.getElementById("memberChamberBadge").innerHTML = "";
}

/* ── donut chart ────────────────────────────────────────────────────────── */
function renderDonut(positions) {
  const ctx = document.getElementById("donutChart");
  if (!ctx) return;

  // Show top 10 by total_bought, group rest as "Other"
  const sorted  = [...positions].sort((a, b) => b.total_bought - a.total_bought);
  const top10   = sorted.slice(0, 10);
  const rest    = sorted.slice(10);
  const otherAmt = rest.reduce((s, p) => s + p.total_bought, 0);

  const labels = top10.map(p => p.ticker);
  const values = top10.map(p => p.total_bought);
  const colors = top10.map((_, i) => PALETTE[i % PALETTE.length]);

  if (otherAmt > 0) {
    labels.push("Other");
    values.push(otherAmt);
    colors.push("#374151");
  }

  if (donutInstance) donutInstance.destroy();
  donutInstance = new Chart(ctx, {
    type: "doughnut",
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderColor: "#13171f",
        borderWidth: 2,
        hoverOffset: 6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: {
          position: "right",
          labels: {
            color: "#94a3b8",
            font: { size: 11 },
            boxWidth: 12,
            padding: 10,
          },
        },
        tooltip: {
          callbacks: {
            label: ctx => ` ${ctx.label}: ${fmtMoney(ctx.parsed)}`,
          },
        },
      },
      cutout: "65%",
    },
  });
}

/* ── timeline chart ─────────────────────────────────────────────────────── */
async function renderTimeline(person) {
  const ctx = document.getElementById("timelineChart");
  if (!ctx) return;

  try {
    const data = await fetchJson(
      `${API_BASE}/transactions?senator=${encodeURIComponent(person)}&limit=200&sort=tx_date&order=asc`
    );

    // Group by month
    const monthly = {};
    data.forEach(t => {
      const month = t.tx_date?.slice(0, 7);
      if (!month) return;
      if (!monthly[month]) monthly[month] = { buys: 0, sells: 0 };
      if (t.side === "BUY") monthly[month].buys++;
      else monthly[month].sells++;
    });

    const labels    = Object.keys(monthly).sort();
    const buyData   = labels.map(m => monthly[m].buys);
    const sellData  = labels.map(m => monthly[m].sells);

    if (timelineInstance) timelineInstance.destroy();
    timelineInstance = new Chart(ctx, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Buys",
            data: buyData,
            backgroundColor: "rgba(34, 197, 94, 0.6)",
            borderColor: "#22c55e",
            borderWidth: 1,
          },
          {
            label: "Sells",
            data: sellData,
            backgroundColor: "rgba(239, 68, 68, 0.6)",
            borderColor: "#ef4444",
            borderWidth: 1,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: {
          legend: {
            labels: { color: "#94a3b8", font: { size: 11 } },
          },
        },
        scales: {
          x: {
            stacked: true,
            ticks: { color: "#64748b", maxTicksLimit: 12 },
            grid:  { color: "rgba(37,43,56,0.6)" },
          },
          y: {
            stacked: true,
            beginAtZero: true,
            ticks: { color: "#64748b", precision: 0 },
            grid: { color: "rgba(37,43,56,0.6)" },
          },
        },
      },
    });
  } catch (err) {
    console.warn("Timeline chart error:", err);
  }
}

/* ── performance chart ──────────────────────────────────────────────────── */
function fmtPct(pctValue, decimals = 1) {
  // pctValue is a plain % number (e.g. 12.3 = +12.3%)
  const val = Number(pctValue).toFixed(decimals);
  return (pctValue >= 0 ? "+" : "") + val + "%";
}

// Full data kept in memory — period switching is client-side (instant)
let _perfData   = null;
let _perfPerson = "";

// Build an interpolated disclosure series aligned to a given date array.
// disclosure_dates / disclosure_growth may be sparser — forward-fill to match.
function _alignDisclosure(dates, discDates, discGrowth) {
  if (!discDates?.length || !discGrowth?.length) return dates.map(() => null);
  const map = {};
  discDates.forEach((d, i) => { map[d] = discGrowth[i]; });
  let last = null;
  return dates.map(d => {
    if (map[d] != null) last = map[d];
    return last;
  });
}

function _setPeriod(period) {
  if (!_perfData) return;

  // Highlight active button
  document.querySelectorAll(".period-btn").forEach(b =>
    b.classList.toggle("active", b.dataset.period === period)
  );

  const allDates = _perfData.dates;
  const allPort  = _perfData.portfolio_growth.map(v => (v - 1) * 100);
  const allSpy   = _perfData.spy_growth.map(v => (v - 1) * 100);

  // Align disclosure series to the same date spine
  const rawDisc  = _alignDisclosure(allDates, _perfData.disclosure_dates, _perfData.disclosure_growth);
  const allDisc  = rawDisc.map(v => v != null ? (v - 1) * 100 : null);

  // Compute start index for the chosen window
  let startIdx = 0;
  if (period !== "MAX") {
    const now    = new Date();
    const cutoff = new Date(now);
    if      (period === "1D") cutoff.setDate(now.getDate() - 1);
    else if (period === "7D") cutoff.setDate(now.getDate() - 7);
    else if (period === "1M") cutoff.setMonth(now.getMonth() - 1);
    else if (period === "1Y") cutoff.setFullYear(now.getFullYear() - 1);
    else if (period === "5Y") cutoff.setFullYear(now.getFullYear() - 5);
    const cutoffStr = cutoff.toISOString().slice(0, 10);
    const idx = allDates.findIndex(d => d >= cutoffStr);
    startIdx = idx >= 0 ? idx : 0;
  }

  const dates    = allDates.slice(startIdx);
  let portSlice  = allPort.slice(startIdx);
  let spySlice   = allSpy.slice(startIdx);
  let discSlice  = allDisc.slice(startIdx);

  // Re-normalise so the window always starts at 0%
  if (startIdx > 0 && portSlice.length) {
    const pBase = portSlice[0];
    const sBase = spySlice[0];
    const dBase = discSlice.find(v => v != null) ?? 0;
    portSlice = portSlice.map(v => v - pBase);
    spySlice  = spySlice.map(v => v - sBase);
    discSlice = discSlice.map(v => v != null ? v - dBase : null);
  }

  const windowReturn    = portSlice.length ? portSlice[portSlice.length - 1] : 0;
  const windowSpyReturn = spySlice.length  ? spySlice[spySlice.length - 1]   : 0;
  const isUp    = windowReturn >= 0;
  const line    = isUp ? "#3b82f6" : "#ef4444";
  const fill    = isUp ? "rgba(59,130,246,0.08)" : "rgba(239,68,68,0.08)";
  const periods = { "1D":"today","7D":"past week","1M":"past month",
                    "1Y":"past year","5Y":"past 5 years","MAX":"all time" };
  const periodLabel = periods[period] || "";

  // Update big return number
  const retEl = document.getElementById("perfTotalReturn");
  if (retEl) {
    retEl.textContent = fmtPct(windowReturn);
    retEl.className   = `perf-return ${isUp ? "price-up" : "price-down"}`;
  }
  const wlEl = document.getElementById("perfWindowLabel");
  if (wlEl) wlEl.textContent = periodLabel;

  // Update SPY return for the current window
  const spyEl = document.getElementById("perfSpyReturn");
  if (spyEl) spyEl.textContent = fmtPct(windowSpyReturn);

  // Update portfolio return card
  const cardEl = document.getElementById("sumPortReturn");
  if (cardEl) {
    cardEl.textContent = windowReturn !== 0 ? fmtPct(windowReturn) : "—";
    cardEl.className   = `font-headline text-3xl font-extrabold ${isUp ? "price-up" : "price-down"}`;
  }
  const cardSubEl = document.getElementById("sumPortReturnSub");
  if (cardSubEl) cardSubEl.textContent = periodLabel;

  // Update chart without animation (feels instant like a real trading app)
  if (perfInstance) {
    perfInstance.data.labels = dates;
    perfInstance.data.datasets[0].data            = portSlice.map(v => +v.toFixed(2));
    perfInstance.data.datasets[0].borderColor     = line;
    perfInstance.data.datasets[0].backgroundColor = fill;
    if (perfInstance.data.datasets[1])
      perfInstance.data.datasets[1].data = spySlice.map(v => +v.toFixed(2));
    if (perfInstance.data.datasets[2])
      perfInstance.data.datasets[2].data = discSlice.map(v => v != null ? +v.toFixed(2) : null);
    perfInstance.update("none");
  }
}

async function renderPerformanceChart(person) {
  const canvas   = document.getElementById("perfChart");
  const loadEl   = document.getElementById("perfLoading");
  const noDataEl = document.getElementById("perfNoData");
  if (!canvas) return;

  // Reset
  _perfData   = null;
  _perfPerson = person;
  loadEl?.classList.remove("d-none");
  canvas.classList.add("d-none");
  noDataEl?.classList.add("d-none");
  ["perfTotalReturn","perfCagr","perfSpyReturn","perfSpyCagr",
   "perfDateRange","perfNTx","perfPeriodRange","perfWindowLabel",
   "perfDiscReturn","perfDiscCagr"]
    .forEach(id => { const el = document.getElementById(id); if (el) el.textContent = ""; });

  try {
    const data = await fetchJson(
      `${API_BASE}/portfolio/performance?person=${encodeURIComponent(person)}`
    );

    loadEl?.classList.add("d-none");

    if (!data.dates?.length) {
      noDataEl?.classList.remove("d-none");
      return;
    }

    _perfData = data;

    // All-time stats for the bottom strip (these never change with period)
    const cagrPct    = (data.cagr             - 1) * 100;
    const spyCagrPct = (data.spy_cagr         - 1) * 100;
    const spyRetPct  = (data.spy_total_return  - 1) * 100;

    const cagrEl = document.getElementById("perfCagr");
    if (cagrEl) {
      cagrEl.textContent = fmtPct(cagrPct) + "/yr";
      cagrEl.className   = `perf-stat-val ${cagrPct >= 0 ? "price-up" : "price-down"}`;
    }
    const spyCagrEl = document.getElementById("perfSpyCagr");
    if (spyCagrEl) spyCagrEl.textContent = fmtPct(spyCagrPct) + "/yr";

    const spyEl = document.getElementById("perfSpyReturn");
    if (spyEl) spyEl.textContent = fmtPct(spyRetPct);

    const nTxEl = document.getElementById("perfNTx");
    if (nTxEl) nTxEl.textContent = data.n_transactions;

    const rangeEl = document.getElementById("perfPeriodRange");
    if (rangeEl) rangeEl.textContent = `${data.start_date} → ${data.end_date}`;

    const dateRangeEl = document.getElementById("perfDateRange");
    if (dateRangeEl) dateRangeEl.textContent = `${data.start_date} → ${data.end_date}`;

    // Disclosure stats
    const discRetPct  = (_perfData.disclosure_total_return - 1) * 100;
    const discCagrPct = (_perfData.disclosure_cagr         - 1) * 100;
    const discRetEl   = document.getElementById("perfDiscReturn");
    if (discRetEl) {
      discRetEl.textContent = fmtPct(discRetPct);
      discRetEl.className   = `perf-stat-val ${discRetPct >= 0 ? "price-up" : "price-down"}`;
    }
    const discCagrEl = document.getElementById("perfDiscCagr");
    if (discCagrEl) discCagrEl.textContent = data.disclosure_dates?.length ? fmtPct(discCagrPct) + "/yr" : "—";

    // Build Chart.js instance (full/MAX data; _setPeriod will slice it)
    canvas.classList.remove("d-none");
    if (perfInstance) perfInstance.destroy();

    const allPort = data.portfolio_growth.map(v => +((v - 1) * 100).toFixed(2));
    const allSpy  = data.spy_growth.map(v => +((v - 1) * 100).toFixed(2));
    const rawDisc = _alignDisclosure(data.dates, data.disclosure_dates, data.disclosure_growth);
    const allDisc = rawDisc.map(v => v != null ? +((v - 1) * 100).toFixed(2) : null);
    const totalReturn = allPort[allPort.length - 1] ?? 0;
    const isUp = totalReturn >= 0;

    perfInstance = new Chart(canvas, {
      type: "line",
      data: {
        labels: data.dates,
        datasets: [
          {
            label: person,
            data: allPort,
            borderColor: isUp ? "#3b82f6" : "#ef4444",
            backgroundColor: isUp ? "rgba(59,130,246,0.08)" : "rgba(239,68,68,0.08)",
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.2,
            fill: true,
            order: 1,
          },
          {
            label: "S&P 500 (SPY)",
            data: allSpy,
            borderColor: "#475569",
            backgroundColor: "transparent",
            borderWidth: 1.5,
            borderDash: [4, 4],
            pointRadius: 0,
            tension: 0.2,
            order: 2,
          },
          {
            label: "Following Disclosures",
            data: allDisc,
            borderColor: "#ddb8ff",
            backgroundColor: "transparent",
            borderWidth: 1.5,
            borderDash: [2, 3],
            pointRadius: 0,
            tension: 0.2,
            spanGaps: true,
            order: 3,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: {
            labels: {
              color: "#64748b",
              font: { size: 11 },
              usePointStyle: true,
              pointStyleWidth: 10,
              boxHeight: 2,
            },
          },
          tooltip: {
            backgroundColor: "#1a1f2e",
            borderColor: "#252b38",
            borderWidth: 1,
            titleColor: "#94a3b8",
            bodyColor: "#e2e8f0",
            callbacks: {
              label: ctx =>
                ` ${ctx.dataset.label}: ${ctx.parsed.y >= 0 ? "+" : ""}${ctx.parsed.y.toFixed(1)}%`,
            },
          },
        },
        scales: {
          x: {
            ticks: { color: "#475569", maxTicksLimit: 8, maxRotation: 0, font: { size: 10 } },
            grid:  { color: "rgba(37,43,56,0.4)" },
          },
          y: {
            position: "right",
            ticks: {
              color: "#475569",
              font: { size: 10 },
              callback: v => (v >= 0 ? "+" : "") + v.toFixed(0) + "%",
            },
            grid: { color: "rgba(37,43,56,0.4)" },
          },
        },
      },
    });

    // Apply MAX period (updates header return + chart)
    _setPeriod("MAX");

    // period buttons and stats strip are always visible

  } catch (err) {
    loadEl?.classList.add("d-none");
    if (noDataEl) {
      noDataEl.textContent = `Could not compute performance: ${err.message}`;
      noDataEl.classList.remove("d-none");
    }
    console.warn("Performance chart error:", err);
  }
}

// Wire period buttons via event delegation
document.addEventListener("click", e => {
  if (e.target.classList.contains("period-btn")) {
    _setPeriod(e.target.dataset.period);
  }
});

/* ── portfolio composition ──────────────────────────────────────────────── */
function renderComposition(positions) {
  console.log("[composition] called, positions:", positions?.length, "list el:", document.getElementById("compositionList"));
  const list    = document.getElementById("compositionList");
  const empty   = document.getElementById("compositionEmpty");
  if (!list) return;

  console.log("[composition] positions sample:", positions.slice(0, 3));

  // Held positions: more buys than sells (count-based, works even when tx_estimate = 0)
  const held = positions
    .filter(p => (p.n_buys || 0) > (p.n_sells || 0))
    .sort((a, b) => (b.total_bought || 0) - (a.total_bought || 0));

  console.log("[composition] held count:", held.length, "from total:", positions.length);

  if (held.length === 0) {
    list.innerHTML = "";
    empty?.classList.remove("d-none");
    return;
  }
  empty?.classList.add("d-none");

  const sizeOf = p => p.total_bought || 0;
  const total  = held.reduce((s, p) => s + sizeOf(p), 0);

  // Top 15 + group remainder as "Other"
  const top   = held.slice(0, 15);
  const rest  = held.slice(15);
  const items = [...top];
  if (rest.length > 0) {
    const otherAmt = rest.reduce((s, p) => s + sizeOf(p), 0);
    items.push({ ticker: `+${rest.length} more`, net_invested: otherAmt, total_bought: otherAmt, current_price: null, _isOther: true });
  }

  list.innerHTML = items.map((p, i) => {
    const size     = p._isOther ? p.net_invested : sizeOf(p);
    const pct      = total > 0 ? ((size / total) * 100).toFixed(1) : (100 / items.length).toFixed(1);
    const color    = p._isOther ? "#4b5563" : PALETTE[i % PALETTE.length];
    const priceStr = p.current_price != null ? `<span style="color:#8d90a1;font-size:0.7rem;margin-left:0.5rem;">${fmtPrice(p.current_price)}</span>` : "";
    const tickerHtml = p._isOther
      ? `<span class="font-label font-bold text-sm" style="color:#8d90a1;">${p.ticker}</span>`
      : `<a href="timeseries.html?ticker=${encodeURIComponent(p.ticker)}" style="color:${color};font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.85rem;text-decoration:none;letter-spacing:0.06em;">${p.ticker}</a>`;

    return `
      <div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">
          <div style="display:flex;align-items:center;gap:8px;">
            ${tickerHtml}${priceStr}
          </div>
          <div style="display:flex;align-items:center;gap:12px;">
            <span class="font-label text-xs" style="color:#8d90a1;">${total > 0 ? fmtMoney(size) : ""}</span>
            <span class="font-label font-bold text-sm" style="color:${color};min-width:3.5rem;text-align:right;">${pct}%</span>
          </div>
        </div>
        <div style="height:6px;background:rgba(67,70,85,0.2);border-radius:9999px;overflow:hidden;">
          <div style="height:100%;width:${pct}%;background:${color};border-radius:9999px;transition:width 0.6s ease;"></div>
        </div>
      </div>`;
  }).join("");
}

/* ── positions table ────────────────────────────────────────────────────── */
function renderPositionsTable(positions) {
  const tbody = document.querySelector("#portfolioTable tbody");
  if (!tbody) return;
  tbody.innerHTML = "";

  positions.forEach(p => {
    const dirClass = p.direction === "LONG" ? "direction-long" : "direction-short";
    const dirLabel = p.direction === "LONG"
      ? '<span class="badge-buy">LONG</span>'
      : '<span class="badge-sell">SHORT</span>';

    const priceHtml = p.current_price != null
      ? `<span class="text-secondary small">${fmtPrice(p.current_price)}</span>`
      : `<span class="text-secondary small">—</span>`;

    const lastSideBadge = p.last_side === "BUY"
      ? `<span class="badge-buy" style="font-size:0.65rem;">BUY</span>`
      : `<span class="badge-sell" style="font-size:0.65rem;">SELL</span>`;

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>
        <a href="timeseries.html?ticker=${encodeURIComponent(p.ticker)}"
           class="text-decoration-none fw-semibold badge-ticker">${p.ticker}</a>
      </td>
      <td class="text-center text-secondary small">${p.n_buys}</td>
      <td class="text-center text-secondary small">${p.n_sells}</td>
      <td>${dirLabel}</td>
      <td class="text-end num">${fmtMoney(p.total_bought)}</td>
      <td class="text-end num">${fmtMoney(p.total_sold)}</td>
      <td class="text-end">${priceHtml}</td>
      <td class="text-secondary small">${p.last_tx_date ?? "—"} ${lastSideBadge}</td>
    `;
    tbody.appendChild(tr);
  });
}

/* ── event wiring ───────────────────────────────────────────────────────── */
document.getElementById("loadPortfolioBtn")?.addEventListener("click", loadPortfolio);

document.getElementById("chamberFilter")?.addEventListener("change", async () => {
  const chamber = document.getElementById("chamberFilter").value;
  await loadMembers(chamber);
});

/* ── init ───────────────────────────────────────────────────────────────── */
(async () => {
  await loadMembers();

  // Auto-load if ?person= is in the URL
  const qs = new URLSearchParams(window.location.search);
  if (qs.get("person")) {
    await loadPortfolio();
  }
})();
