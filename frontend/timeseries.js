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
    const gradBuy = ctx.getContext("2d").createLinearGradient(0, 0, 0, 320);
    gradBuy.addColorStop(0, "rgba(180,197,255,0.22)");
    gradBuy.addColorStop(1, "rgba(180,197,255,0)");
    datasets.push({
      label: "BUY",
      data: buySeries,
      borderColor: "#b4c5ff",
      backgroundColor: gradBuy,
      borderWidth: 2,
      fill: true,
      tension: 0.4,
      pointBackgroundColor: "#b4c5ff",
      pointBorderColor: "#131315",
      pointBorderWidth: 2,
      pointRadius: 4,
      pointHoverRadius: 7,
    });
  }

  if (mode === "sell" || mode === "both") {
    const gradSell = ctx.getContext("2d").createLinearGradient(0, 0, 0, 320);
    gradSell.addColorStop(0, "rgba(255,180,171,0.22)");
    gradSell.addColorStop(1, "rgba(255,180,171,0)");
    datasets.push({
      label: "SELL",
      data: sellSeries,
      borderColor: "#ffb4ab",
      backgroundColor: gradSell,
      borderWidth: 2,
      fill: true,
      tension: 0.4,
      pointBackgroundColor: "#ffb4ab",
      pointBorderColor: "#131315",
      pointBorderWidth: 2,
      pointRadius: 4,
      pointHoverRadius: 7,
    });
  }

  chart = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          display: true,
          labels: {
            color: "#8d90a1",
            font: { family: "'Space Grotesk', sans-serif", size: 11 },
            usePointStyle: true,
            pointStyleWidth: 8,
            padding: 20,
          }
        },
        tooltip: {
          backgroundColor: "#1b1b1d",
          borderColor: "rgba(67,70,85,0.5)",
          borderWidth: 1,
          titleColor: "#e5e1e4",
          bodyColor: "#c3c6d8",
          titleFont: { family: "'Plus Jakarta Sans', sans-serif", weight: "700", size: 12 },
          bodyFont:  { family: "'Space Grotesk', sans-serif", size: 11 },
          padding: 14,
          cornerRadius: 10,
        }
      },
      scales: {
        x: {
          ticks: { color: "#8d90a1", font: { family: "'Space Grotesk', sans-serif", size: 10 } },
          grid:  { color: "rgba(67,70,85,0.15)" },
          border: { color: "rgba(67,70,85,0.25)" },
        },
        y: {
          beginAtZero: true,
          ticks: {
            precision: 0,
            color: "#8d90a1",
            font: { family: "'Space Grotesk', sans-serif", size: 10 },
          },
          grid:  { color: "rgba(67,70,85,0.15)" },
          border: { color: "rgba(67,70,85,0.25)" },
        }
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
      tr.style.cssText = "border-bottom:1px solid rgba(67,70,85,0.08);transition:background 0.15s;";
      tr.onmouseenter = () => tr.style.background = "rgba(42,42,44,0.4)";
      tr.onmouseleave = () => tr.style.background = "";
      const sideHtml = t.side === "BUY"
        ? `<span style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.65rem;padding:2px 8px;border-radius:9999px;background:rgba(83,223,154,0.12);color:#53df9a;letter-spacing:0.05em;">BUY</span>`
        : `<span style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.65rem;padding:2px 8px;border-radius:9999px;background:rgba(255,180,171,0.12);color:#ffb4ab;letter-spacing:0.05em;">SELL</span>`;
      const amtFmt = t.tx_estimate ? "$" + Number(t.tx_estimate).toLocaleString() : "—";
      tr.innerHTML = `
        <td style="padding:14px 20px;font-family:'Inter',sans-serif;font-size:0.8rem;color:#e5e1e4;">${t.full_name ?? ""}</td>
        <td style="padding:14px 20px;"><span style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.7rem;padding:2px 8px;border-radius:6px;background:#1b1b1d;color:#b4c5ff;">${t.ticker ?? ""}</span></td>
        <td style="padding:14px 20px;">${sideHtml}</td>
        <td style="padding:14px 20px;font-family:'Space Grotesk',sans-serif;font-size:0.8rem;color:#c3c6d8;">${t.tx_date ?? ""}</td>
        <td style="padding:14px 20px;text-align:right;font-family:'Space Grotesk',sans-serif;font-size:0.8rem;color:#8d90a1;">${amtFmt}</td>
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
