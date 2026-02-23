const API_BASE = window.API_BASE;
let chart = null;

function getQS() {
  return new URLSearchParams(window.location.search);
}

function setQS(params) {
  const qs = params.toString();
  const newUrl = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
  history.replaceState(null, "", newUrl);
}

function qsGet(params, key, fallback = "") {
  const v = params.get(key);
  return v === null || v === undefined ? fallback : v;
}

function setLoading(isLoading) {
  const el = document.getElementById("loading");
  if (!el) return;
  el.classList.toggle("d-none", !isLoading);
}

function setStatus(message = "") {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = message;
  el.classList.toggle("d-none", !message);
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${res.statusText} ${text.slice(0, 200)}`);
  }
  return await res.json();
}

function toMonthStart(dateStr) {
  const d = new Date(`${dateStr}T00:00:00Z`);
  d.setUTCDate(1);
  return d;
}

function formatMonth(dateObj) {
  return dateObj.toISOString().slice(0, 10);
}

function addMonth(dateObj) {
  const d = new Date(dateObj);
  d.setUTCMonth(d.getUTCMonth() + 1);
  d.setUTCDate(1);
  return d;
}

function normalizeMonthlySeries(rows) {
  if (!rows.length) return [];

  const map = new Map(rows.map((r) => [r.month_start, r]));
  const start = toMonthStart(rows[0].month_start);
  const end = toMonthStart(rows[rows.length - 1].month_start);

  const normalized = [];
  for (let cursor = start; cursor <= end; cursor = addMonth(cursor)) {
    const key = formatMonth(cursor);
    const existing = map.get(key);
    normalized.push({
      month_start: key,
      buy_senators: existing?.buy_senators ?? 0,
      sell_senators: existing?.sell_senators ?? 0,
    });
  }

  return normalized;
}

function updateSummaryCards(rows) {
  const monthsEl = document.getElementById("monthsValue");
  const peakBuyEl = document.getElementById("peakBuyValue");
  const peakSellEl = document.getElementById("peakSellValue");

  const months = rows.length;
  const peakBuy = rows.reduce((max, r) => Math.max(max, r.buy_senators ?? 0), 0);
  const peakSell = rows.reduce((max, r) => Math.max(max, r.sell_senators ?? 0), 0);

  if (monthsEl) monthsEl.textContent = String(months);
  if (peakBuyEl) peakBuyEl.textContent = String(peakBuy);
  if (peakSellEl) peakSellEl.textContent = String(peakSell);
}

async function loadTickers() {
  const tickers = await fetchJson(`${API_BASE}/tickers`);

  const sel = document.getElementById("tickerSelect");
  if (!sel) return;

  sel.innerHTML = "";
  tickers.forEach((t) => {
    const opt = document.createElement("option");
    opt.value = t.ticker;
    opt.textContent = t.ticker;
    sel.appendChild(opt);
  });

  if (tickers.length > 0) sel.value = tickers[0].ticker;
}

function renderChart(rows, mode, smoothOn) {
  const ctx = document.getElementById("tsChart");
  if (!ctx) return;

  if (chart) chart.destroy();

  const labels = rows.map((r) => r.month_start);
  const buySeries = rows.map((r) => r.buy_senators ?? 0);
  const sellSeries = rows.map((r) => r.sell_senators ?? 0);

  const tension = smoothOn ? 0.3 : 0;
  const datasets = [];

  if (mode === "buy" || mode === "both") {
    datasets.push({
      label: "BUY (unique senators)",
      data: buySeries,
      borderColor: "#22c55e",
      backgroundColor: "rgba(34,197,94,0.2)",
      fill: false,
      tension,
      pointRadius: 2,
    });
  }

  if (mode === "sell" || mode === "both") {
    datasets.push({
      label: "SELL (unique senators)",
      data: sellSeries,
      borderColor: "#ef4444",
      backgroundColor: "rgba(239,68,68,0.2)",
      fill: false,
      tension,
      pointRadius: 2,
    });
  }

  chart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: true } },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 } },
      },
    },
  });
}

function updateUrlFromTimeseriesUI() {
  const params = getQS();

  const ticker = document.getElementById("tickerSelect")?.value ?? "";
  const mode = document.getElementById("modeSelect")?.value ?? "both";
  const smooth = document.getElementById("smoothSelect")?.value ?? "on";

  if (ticker) params.set("ticker", ticker);
  else params.delete("ticker");

  params.set("mode", mode);
  params.set("smooth", smooth);

  setQS(params);
}

function applyUrlStateToTimeseriesUI() {
  const params = getQS();
  const ticker = qsGet(params, "ticker", "");
  const mode = qsGet(params, "mode", "both");
  const smooth = qsGet(params, "smooth", "on");

  if (ticker) document.getElementById("tickerSelect").value = ticker;
  document.getElementById("modeSelect").value = ["buy", "sell", "both"].includes(mode) ? mode : "both";
  document.getElementById("smoothSelect").value = smooth === "off" ? "off" : "on";
}

async function loadTransactionsForSelectedTicker() {
  const tbody = document.querySelector("#transactionsTable tbody");
  if (!tbody) return;

  const ticker = document.getElementById("tickerSelect")?.value;
  const mode = document.getElementById("modeSelect")?.value;

  if (!ticker) {
    tbody.innerHTML = "";
    return;
  }

  let side = "";
  if (mode === "buy") side = "BUY";
  if (mode === "sell") side = "SELL";

  const limit = 200;
  let url = `${API_BASE}/transactions?limit=${limit}&offset=0&ticker=${encodeURIComponent(ticker)}`;
  if (side) url += `&side=${encodeURIComponent(side)}`;

  const data = await fetchJson(url);

  tbody.innerHTML = "";
  data.forEach((t) => {
    const tr = document.createElement("tr");

    const senatorTd = document.createElement("td");
    senatorTd.textContent = t.full_name ?? "";

    const tickerTd = document.createElement("td");
    const tickerBadge = document.createElement("span");
    tickerBadge.className = "badge text-bg-secondary";
    tickerBadge.textContent = t.ticker ?? "";
    tickerTd.appendChild(tickerBadge);

    const sideTd = document.createElement("td");
    const sideBadge = document.createElement("span");
    sideBadge.className = `badge ${t.side === "BUY" ? "text-bg-success" : "text-bg-danger"}`;
    sideBadge.textContent = t.side ?? "";
    sideTd.appendChild(sideBadge);

    const dateTd = document.createElement("td");
    dateTd.textContent = t.tx_date ?? "";

    const amountTd = document.createElement("td");
    amountTd.className = "text-end";
    amountTd.textContent = t.tx_estimate ?? "";

    tr.append(senatorTd, tickerTd, sideTd, dateTd, amountTd);
    tbody.appendChild(tr);
  });
}

async function loadTimeSeries() {
  setStatus("");
  setLoading(true);

  try {
    const ticker = document.getElementById("tickerSelect")?.value;
    const mode = document.getElementById("modeSelect")?.value;
    const smoothOn = document.getElementById("smoothSelect")?.value !== "off";

    if (!ticker) {
      setStatus("Please select a ticker first.");
      return;
    }

    const data = await fetchJson(`${API_BASE}/timeseries/monthly?ticker=${encodeURIComponent(ticker)}&mode=${encodeURIComponent(mode)}`);

    if (!data.length) {
      setStatus("No data for this ticker.");
      if (chart) chart.destroy();
      chart = null;
      document.querySelector("#transactionsTable tbody").innerHTML = "";
      updateSummaryCards([]);
      return;
    }

    const normalized = normalizeMonthlySeries(data);
    renderChart(normalized, mode, smoothOn);
    updateSummaryCards(normalized);
    await loadTransactionsForSelectedTicker();
    updateUrlFromTimeseriesUI();
  } catch (err) {
    console.error(err);
    setStatus(`Error loading chart: ${err.message}`);
  } finally {
    setLoading(false);
  }
}

function onTimeseriesChanged() {
  loadTimeSeries();
}

document.getElementById("tickerSelect")?.addEventListener("change", onTimeseriesChanged);
document.getElementById("modeSelect")?.addEventListener("change", onTimeseriesChanged);
document.getElementById("smoothSelect")?.addEventListener("change", onTimeseriesChanged);
document.getElementById("loadBtn")?.addEventListener("click", onTimeseriesChanged);

(async () => {
  try {
    setLoading(true);
    await loadTickers();
    const sel = document.getElementById("tickerSelect");
    if (!sel || sel.options.length === 0) {
      setStatus("No tickers available. Check /tickers endpoint and database values.");
      return;
    }

    applyUrlStateToTimeseriesUI();
    await loadTimeSeries();
  } catch (err) {
    console.error(err);
    setStatus(`Startup error: ${err.message}`);
  } finally {
    setLoading(false);
  }
})();
