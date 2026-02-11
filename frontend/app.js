
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

let offset = 0;
const limit = 50;
let topChart = null;
let tickerTom = null;

async function fetchJson(url, options = {}) {
  const res = await fetch(url, options);

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} ${res.statusText} — ${text.slice(0, 200)}`);
  }

  try {
    return await res.json();
  } catch {
    throw new Error("Response was not valid JSON");
  }
}

function setLoading(isLoading) {
  const el = document.getElementById("loading");
  if (!el) return;
  el.classList.toggle("d-none", !isLoading);
}

function setStatus(message = "") {
  const el = document.getElementById("status");
  const hasMsg = Boolean(message);
  el.textContent = message;
  el.classList.toggle("d-none", !hasMsg);
}

function setPageInfo() {
    const el = document.getElementById("pageInfo");
        if (!el) return;
  el.textContent = `Showing ${limit} rows per page. Offset: ${offset}`;
}

async function loadSenators() {
  const select = document.getElementById("senatorSelect");
  if (!select) return;
  try {
    setStatus("");
    setLoading(true);

    const res = await fetch(`${API_BASE}/senators`);
    if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
    const data = await res.json();

    const select = document.getElementById("senatorSelect");
    select.innerHTML = `<option value="">All Senators</option>`;

    data.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s.full_name;
      opt.textContent = s.full_name;
      select.appendChild(opt);
    });
  } catch (err) {
    console.error(err);
    setStatus(`Error loading senators: ${err.message}`);
  } finally {
    setLoading(false);
  }
}

async function loadTransactions(resetOffset = false) {
  const table = document.querySelector("#transactionsTable tbody");
  if (!table) return;
  if (resetOffset) offset = 0;

  try {
    setStatus("");
    setLoading(true);
    setPageInfo();

    const senator = document.getElementById("senatorSelect").value;
    const side = document.getElementById("sideSelect").value;
    const ticker =
    tickerTom?.getValue()?.trim().toUpperCase() || "";


    let url = `${API_BASE}/transactions?limit=${limit}&offset=${offset}`;
    if (senator) url += `&senator=${encodeURIComponent(senator)}`;
    if (side) url += `&side=${encodeURIComponent(side)}`;
    if (ticker) url += `&ticker=${encodeURIComponent(ticker)}`;

    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
    const data = await res.json();

    const tbody = document.querySelector("#transactionsTable tbody");
    tbody.innerHTML = "";

    data.forEach(t => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${t.full_name}</td>
        <td><span class="badge text-bg-secondary">${t.ticker}</span></td>
        <td>${t.side === "BUY"
            ? '<span class="badge text-bg-success">BUY</span>'
            : '<span class="badge text-bg-danger">SELL</span>'}</td>
        <td>${t.tx_date}</td>
        <td class="text-end">${t.tx_estimate ?? ""}</td>
      `;
      tbody.appendChild(tr);
    });

    if (data.length === 0) {
      setStatus("No results for the selected filters.");
    }
  } catch (err) {
    console.error(err);
    setStatus(`Error loading transactions: ${err.message}`);
  } finally {
    setLoading(false);
  }
}
let cachedActivity = [];

async function loadActivityTop(period, side, topN = 10) {
  const url = `${API_BASE}/activity/top?period=${encodeURIComponent(period)}&side=${encodeURIComponent(side)}&top_n=${topN}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status} ${res.statusText}`);
  return await res.json();
}

function fillBucketSelect(activityRows) {
  const bucketSelect = document.getElementById("bucketSelect");
  const buckets = [...new Set(activityRows.map(r => r.bucket_start))];

  // Remember what the user currently selected
  const prev = bucketSelect.value;

  bucketSelect.innerHTML = "";
  buckets.forEach(b => {
    const opt = document.createElement("option");
    opt.value = b;
    opt.textContent = b;
    bucketSelect.appendChild(opt);
  });

  // Keep previous selection if it still exists; otherwise pick the latest
  if (prev && buckets.includes(prev)) {
    bucketSelect.value = prev;
  } else if (buckets.length > 0) {
    bucketSelect.value = buckets[0];
  }
}

function renderTopTickersChart(activityRows, bucketStart) {
  const rows = activityRows.filter(r => r.bucket_start === bucketStart);

  const labels = rows.map(r => r.ticker);
  const values = rows.map(r => r.n_senators);

  const ctx = document.getElementById("topTickersChart");

  if (topChart) topChart.destroy();

  topChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "# Senators",
        data: values
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: true } },
      scales: {
        y: { beginAtZero: true, precision: 0 }
      }
    }
  });
}

function renderSelectedBucket() {
  const bucket = document.getElementById("bucketSelect").value;
  if (!bucket) {
    setStatus("No bucket selected.");
    return;
  }
  renderTopTickersChart(cachedActivity, bucket);
}

async function refreshActivity() {
  if (
    !document.getElementById("periodSelect") ||
    !document.getElementById("sideAggSelect") ||
    !document.getElementById("bucketSelect")
  ) return;
  try {
    setStatus("");
    setLoading(true);

    const period = document.getElementById("periodSelect").value;
    const side = document.getElementById("sideAggSelect").value;

    cachedActivity = await loadActivityTop(period, side, 10);

    if (cachedActivity.length === 0) {
      setStatus("No data for this selection.");
      return;
    }

    fillBucketSelect(cachedActivity);
    renderSelectedBucket();
  } catch (err) {
    console.error(err);
    setStatus(`Error loading chart: ${err.message}`);
  } finally {
    setLoading(false);
  }
}

// Event listeners (ONLY these)
const periodSelect = document.getElementById("periodSelect");
if (periodSelect) {
  periodSelect.addEventListener("change", refreshActivity);
}

const sideAggSelect = document.getElementById("sideAggSelect");
if (sideAggSelect) {
  sideAggSelect.addEventListener("change", refreshActivity);
}

const bucketSelect = document.getElementById("bucketSelect");
if (bucketSelect) {
  bucketSelect.addEventListener("change", renderSelectedBucket);
}

const loadChartBtn = document.getElementById("loadChartBtn");
if (loadChartBtn) {
  loadChartBtn.addEventListener("click", refreshActivity);
}

const tickerSelectIndex = document.getElementById("tickerSelectIndex");
if (tickerSelectIndex) {
  tickerSelectIndex.addEventListener("change", () => loadTransactions(true));
}




async function loadTickersIndex() {
  setStatus("");
  setLoading(true);

  try {
    const tickers = await fetchJson(`${API_BASE}/tickers`);

    const select = document.getElementById("tickerSelectIndex");
    if (!select) return;

    // Clear existing options
    select.innerHTML = "";

    // Add empty option = "All tickers"
    const emptyOpt = document.createElement("option");
    emptyOpt.value = "";
    emptyOpt.textContent = "All tickers";
    select.appendChild(emptyOpt);

    tickers.forEach(t => {
      const opt = document.createElement("option");
      opt.value = t.ticker;
      opt.textContent = t.ticker;
      select.appendChild(opt);
    });

    // Destroy previous instance if exists
    if (tickerTom) {
      tickerTom.destroy();
    }

    // Create Tom Select
    tickerTom = new TomSelect(select, {
      maxItems: 1,
      allowEmptyOption: true,
      placeholder: "Search ticker…",
      create: false,
      sortField: {
        field: "text",
        direction: "asc"
      },
      onChange(value) {
        // reload data when ticker changes
        offset = 0;
        updateUrlFromIndexUI();
        loadTransactions(false);
      }
    });

  } catch (err) {
    console.error(err);
    setStatus(`Error loading tickers: ${err.message}`);
  } finally {
    setLoading(false);
  }
}



function applyUrlStateToIndexUI() {
  const params = getQS();

  const senator = qsGet(params, "senator", "");
  const side    = qsGet(params, "side", "");
  const ticker = qsGet(params, "ticker", "");
  
  if (tickerTom && ticker) {
  tickerTom.setValue(ticker, true); // silent = true
  }
  // Update UI controls (only if they exist)
  const senatorEl = document.getElementById("senatorSelect");
  const sideEl    = document.getElementById("sideSelect");
  const tickerEl  = document.getElementById("tickerSelectIndex") || document.getElementById("tickerInput");

  if (senatorEl) senatorEl.value = senator;
  if (sideEl) sideEl.value = side;

  if (tickerEl) {
    // if tickerSelectIndex: set dropdown; if tickerInput: set text
    if (tickerEl.tagName.toLowerCase() === "select") tickerEl.value = ticker;
    else tickerEl.value = ticker;
  }

  // Pagination state
  const limitFromUrl = parseInt(qsGet(params, "limit", ""), 10);
  const offsetFromUrl = parseInt(qsGet(params, "offset", ""), 10);

  if (!Number.isNaN(limitFromUrl) && limitFromUrl > 0) {
    // only if you use a global limit variable; otherwise ignore
    // limit = limitFromUrl; // (if limit is const, skip)
  }
  if (!Number.isNaN(offsetFromUrl) && offsetFromUrl >= 0) {
    offset = offsetFromUrl;
  }
}

function updateUrlFromIndexUI() {
  const params = getQS();

  const senator = document.getElementById("senatorSelect")?.value ?? "";
  const side    = document.getElementById("sideSelect")?.value ?? "";

  // support either dropdown or input
  const tickerSelect = document.getElementById("tickerSelectIndex");
  const tickerInput  = document.getElementById("tickerInput");

  const ticker = tickerTom?.getValue() || "";

  // store filters
  if (senator) params.set("senator", senator); else params.delete("senator");
  if (side) params.set("side", side); else params.delete("side");
  if (ticker) params.set("ticker", ticker); else params.delete("ticker");

  // store paging
  params.set("limit", String(limit));
  params.set("offset", String(offset));

  setQS(params);
}
function onIndexFiltersChanged() {
  offset = 0;                 // reset to page 1
  updateUrlFromIndexUI();
  loadTransactions(false);    // (false because we already set offset)
}

document.getElementById("senatorSelect")?.addEventListener("change", onIndexFiltersChanged);
document.getElementById("sideSelect")?.addEventListener("change", onIndexFiltersChanged);

document.getElementById("tickerSelectIndex")?.addEventListener("change", onIndexFiltersChanged);
document.getElementById("tickerInput")?.addEventListener("keydown", (e) => {
  if (e.key === "Enter") onIndexFiltersChanged();
});

// Pagination buttons should also update URL
document.getElementById("nextBtn")?.addEventListener("click", () => {
  offset += limit;
  updateUrlFromIndexUI();
  loadTransactions(false);
});

document.getElementById("prevBtn")?.addEventListener("click", () => {
  offset = Math.max(0, offset - limit);
  updateUrlFromIndexUI();
  loadTransactions(false);
});
(async () => {
  setPageInfo();

  await loadSenators();
  await loadTickersIndex();     // creates Tom Select

  applyUrlStateToIndexUI();     // now tickerTom exists
  updateUrlFromIndexUI();

  loadTransactions(false);
  refreshActivity();
})();

