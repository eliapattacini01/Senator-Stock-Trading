const API_BASE = window.API_BASE;

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
  return (v === null || v === undefined) ? fallback : v;
}


let chart = null;

function setLoading(isLoading) {
  document.getElementById("loading").classList.toggle("d-none", !isLoading);
}

function setStatus(message = "") {
  const el = document.getElementById("status");
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

async function loadTickers() {
  setStatus("");
  setLoading(true);
  try {
    const tickers = await fetchJson(`${API_BASE}/tickers`);

    const sel = document.getElementById("tickerSelect");
    sel.innerHTML = "";
    tickers.forEach(t => {
      const opt = document.createElement("option");
      opt.value = t.ticker;
      opt.textContent = t.ticker;
      sel.appendChild(opt);
    });

    // choose a default ticker if exists
    if (tickers.length > 0) sel.value = tickers[0].ticker;
  } catch (err) {
    console.error(err);
    setStatus(`Error loading tickers: ${err.message}`);
  } finally {
    setLoading(false);
  }
}

function renderChart(labels, buySeries, sellSeries, mode) {
  const ctx = document.getElementById("tsChart");

  if (chart) chart.destroy();

  const datasets = [];

  if (mode === "buy" || mode === "both") {
    datasets.push({
      label: "BUY (unique senators)",
      data: buySeries,
      tension: 0.25
    });
  }

  if (mode === "sell" || mode === "both") {
    datasets.push({
      label: "SELL (unique senators)",
      data: sellSeries,
      tension: 0.25
    });
  }

  chart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      plugins: { legend: { display: true } },
      scales: {
        y: { beginAtZero: true, ticks: { precision: 0 } }
      }
    }
  });
}

async function loadTimeSeries() {
  setStatus("");
  setLoading(true);

  try {
    const ticker = document.getElementById("tickerSelect").value;
    const mode = document.getElementById("modeSelect").value;
    if (!ticker) {
      setStatus("Please select a ticker first.");
      return;
    }

    const data = await fetchJson(
      `${API_BASE}/timeseries/monthly?ticker=${encodeURIComponent(ticker)}&mode=${encodeURIComponent(mode)}`
    );

    if (!data || data.length === 0) {
      setStatus("No data for this ticker.");
      if (chart) chart.destroy();
      chart = null;

      // also clear table
      const tbody = document.querySelector("#transactionsTable tbody");
      if (tbody) tbody.innerHTML = "";
      return;
    }

    const labels = data.map(r => r.month_start);
    const buySeries = data.map(r => r.buy_senators ?? 0);
    const sellSeries = data.map(r => r.sell_senators ?? 0);

    renderChart(labels, buySeries, sellSeries, mode);

    // ✅ refresh table to match selection
    await loadTransactionsForSelectedTicker();
  } catch (err) {
    console.error(err);
    setStatus(`Error loading chart: ${err.message}`);
  } finally {
    setLoading(false);
  }
}


// Wire UI
document.getElementById("loadBtn").addEventListener("click", loadTimeSeries);
document.getElementById("tickerSelect").addEventListener("change", loadTimeSeries);
document.getElementById("modeSelect").addEventListener("change", loadTimeSeries);

// Init
(async () => {
  await loadTickers();
  const sel = document.getElementById("tickerSelect");
  if (sel.options.length === 0) {
    setStatus("No tickers available. Check /tickers endpoint and your database ticker values.");
    return;
  }
  await loadTimeSeries();
})();

async function loadTransactionsForSelectedTicker() {
  const tbody = document.querySelector("#transactionsTable tbody");
  if (!tbody) return;

  const ticker = document.getElementById("tickerSelect").value;
  const mode = document.getElementById("modeSelect").value; // both | buy | sell

  if (!ticker) {
    tbody.innerHTML = "";
    return;
  }

  // Map mode -> API side param (adjust if your backend expects different values)
  let side = "";
  if (mode === "buy") side = "BUY";
  if (mode === "sell") side = "SELL";

  // Pick how many rows you want to show (you can increase)
  const limit = 200;
  let url = `${API_BASE}/transactions?limit=${limit}&offset=0&ticker=${encodeURIComponent(ticker)}`;
  if (side) url += `&side=${encodeURIComponent(side)}`;

  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
    const data = await res.json();

    tbody.innerHTML = "";

    data.forEach(t => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${t.full_name ?? ""}</td>
        <td><span class="badge text-bg-secondary">${t.ticker ?? ""}</span></td>
        <td>${
          t.side === "BUY"
            ? '<span class="badge text-bg-success">BUY</span>'
            : '<span class="badge text-bg-danger">SELL</span>'
        }</td>
        <td>${t.tx_date ?? ""}</td>
        <td class="text-end">${t.tx_estimate ?? ""}</td>
      `;
      tbody.appendChild(tr);
    });

    // Optional: show a small status if no rows
    if (data.length === 0) {
      // keep chart status separate if you prefer
      // setStatus("No transactions found for this selection.");
    }
  } catch (err) {
    console.error(err);
    setStatus(`Error loading transactions table: ${err.message}`);
  }
}

function applyUrlStateToTimeseriesUI() {
  const params = getQS();
  const ticker = qsGet(params, "ticker", "");
  const mode   = qsGet(params, "mode", "both");

  const tickerEl = document.getElementById("tickerSelect");
  const modeEl   = document.getElementById("modeSelect");

  if (tickerEl) tickerEl.value = ticker;
  if (modeEl) modeEl.value = mode;
}
function updateUrlFromTimeseriesUI() {
  const params = getQS();

  const ticker = document.getElementById("tickerSelect")?.value ?? "";
  const mode   = document.getElementById("modeSelect")?.value ?? "both";

  if (ticker) params.set("ticker", ticker); else params.delete("ticker");
  if (mode) params.set("mode", mode); else params.delete("mode");

  setQS(params);
}
function onTimeseriesChanged() {
  updateUrlFromTimeseriesUI();
  loadTimeSeries();
}

document.getElementById("tickerSelect")?.addEventListener("change", onTimeseriesChanged);
document.getElementById("modeSelect")?.addEventListener("change", onTimeseriesChanged);
document.getElementById("loadBtn")?.addEventListener("click", onTimeseriesChanged);
(async () => {
  await loadTickers();                  // fills dropdown options
  applyUrlStateToTimeseriesUI();        // now tickerSelect.value can match
  updateUrlFromTimeseriesUI();          // normalize URL
  loadTimeSeries();
})();
