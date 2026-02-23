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
  return v === null || v === undefined ? fallback : v;
}

let offset = 0;
const limit = 50;
let topChart = null;
let tickerTom = null;
let totalRows = 0;
let cachedActivity = [];

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
  if (!el) return;
  const hasMsg = Boolean(message);
  el.textContent = message;
  el.classList.toggle("d-none", !hasMsg);
}

function getIndexFilters() {
  return {
    senator: document.getElementById("senatorSelect")?.value ?? "",
    side: document.getElementById("sideSelect")?.value ?? "",
    ticker: tickerTom?.getValue()?.trim().toUpperCase() || "",
  };
}

function countActiveFilters(filters) {
  return [filters.senator, filters.side, filters.ticker].filter(Boolean).length;
}

function setSummaryCards(filters, currentPageRows) {
  const totalEl = document.getElementById("totalCountValue");
  const pageRangeEl = document.getElementById("pageRangeValue");
  const activeEl = document.getElementById("activeFiltersValue");

  if (totalEl) totalEl.textContent = totalRows.toLocaleString();

  const start = totalRows === 0 ? 0 : offset + 1;
  const end = totalRows === 0 ? 0 : Math.min(offset + currentPageRows, totalRows);

  if (pageRangeEl) pageRangeEl.textContent = `${start}-${end}`;
  if (activeEl) activeEl.textContent = String(countActiveFilters(filters));
}

function setPageInfo(currentPageRows = 0) {
  const el = document.getElementById("pageInfo");
  if (!el) return;

  const pageNumber = Math.floor(offset / limit) + 1;
  const start = totalRows === 0 ? 0 : offset + 1;
  const end = totalRows === 0 ? 0 : Math.min(offset + currentPageRows, totalRows);

  el.textContent = `Page ${pageNumber} • Showing ${start}-${end} of ${totalRows} results`;
}

function setPaginationState(currentPageRows) {
  const prevBtn = document.getElementById("prevBtn");
  const nextBtn = document.getElementById("nextBtn");

  if (prevBtn) prevBtn.disabled = offset === 0;

  const hasMoreRows = offset + currentPageRows < totalRows;
  if (nextBtn) nextBtn.disabled = !hasMoreRows;
}

async function loadSenators() {
  const select = document.getElementById("senatorSelect");
  if (!select) return;

  const data = await fetchJson(`${API_BASE}/senators`);
  select.innerHTML = `<option value="">All Senators</option>`;

  data.forEach((s) => {
    const opt = document.createElement("option");
    opt.value = s.full_name;
    opt.textContent = s.full_name;
    select.appendChild(opt);
  });
}

function buildFilterQuery(filters) {
  const params = new URLSearchParams();
  if (filters.senator) params.set("senator", filters.senator);
  if (filters.side) params.set("side", filters.side);
  if (filters.ticker) params.set("ticker", filters.ticker);
  return params.toString();
}

async function loadTransactions(resetOffset = false) {
  const tbody = document.querySelector("#transactionsTable tbody");
  if (!tbody) return;
  if (resetOffset) offset = 0;

  try {
    setStatus("");
    setLoading(true);

    const filters = getIndexFilters();
    const filterQS = buildFilterQuery(filters);
    const base = filterQS ? `&${filterQS}` : "";

    const [countData, rows] = await Promise.all([
      fetchJson(`${API_BASE}/transactions/count?${filterQS}`),
      fetchJson(`${API_BASE}/transactions?limit=${limit}&offset=${offset}${base}`),
    ]);

    totalRows = countData.total ?? 0;

    tbody.innerHTML = "";
    rows.forEach((t) => {
      const tr = document.createElement("tr");

      const senatorTd = document.createElement("td");
      senatorTd.textContent = t.full_name ?? "";

      const tickerTd = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = "badge text-bg-secondary";
      badge.textContent = t.ticker ?? "";
      tickerTd.appendChild(badge);

      const sideTd = document.createElement("td");
      const sideBadge = document.createElement("span");
      sideBadge.className = `badge ${t.side === "BUY" ? "text-bg-success" : "text-bg-danger"}`;
      sideBadge.textContent = t.side ?? "";
      sideTd.appendChild(sideBadge);

      const dateTd = document.createElement("td");
      dateTd.textContent = t.tx_date ?? "";

      const estimateTd = document.createElement("td");
      estimateTd.className = "text-end";
      estimateTd.textContent = t.tx_estimate ?? "";

      tr.append(senatorTd, tickerTd, sideTd, dateTd, estimateTd);
      tbody.appendChild(tr);
    });

    setSummaryCards(filters, rows.length);
    setPageInfo(rows.length);
    setPaginationState(rows.length);

    if (rows.length === 0) {
      setStatus("No results for the selected filters.");
    }

    updateUrlFromIndexUI();
  } catch (err) {
    console.error(err);
    setStatus(`Error loading transactions: ${err.message}`);
  } finally {
    setLoading(false);
  }
}

async function loadActivityTop(period, side, topN = 10) {
  const url = `${API_BASE}/activity/top?period=${encodeURIComponent(period)}&side=${encodeURIComponent(side)}&top_n=${topN}`;
  return fetchJson(url);
}

function fillBucketSelect(activityRows) {
  const bucketSelect = document.getElementById("bucketSelect");
  if (!bucketSelect) return;

  const buckets = [...new Set(activityRows.map((r) => r.bucket_start))];
  const prev = bucketSelect.value;

  bucketSelect.innerHTML = "";
  buckets.forEach((b) => {
    const opt = document.createElement("option");
    opt.value = b;
    opt.textContent = b;
    bucketSelect.appendChild(opt);
  });

  if (prev && buckets.includes(prev)) bucketSelect.value = prev;
  else if (buckets.length > 0) bucketSelect.value = buckets[0];
}

function renderTopTickersChart(activityRows, bucketStart) {
  const rows = activityRows.filter((r) => r.bucket_start === bucketStart);
  const labels = rows.map((r) => r.ticker);
  const values = rows.map((r) => r.n_senators);

  const ctx = document.getElementById("topTickersChart");
  if (!ctx) return;

  if (topChart) topChart.destroy();

  topChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{ label: "# Senators", data: values }],
    },
    options: {
      responsive: true,
      plugins: { legend: { display: true } },
      scales: { y: { beginAtZero: true, precision: 0 } },
    },
  });
}

function renderSelectedBucket() {
  const bucket = document.getElementById("bucketSelect")?.value;
  if (!bucket) {
    setStatus("No bucket selected.");
    return;
  }
  renderTopTickersChart(cachedActivity, bucket);
}

async function refreshActivity() {
  if (!document.getElementById("periodSelect") || !document.getElementById("sideAggSelect") || !document.getElementById("bucketSelect")) return;

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

async function loadTickersIndex() {
  const select = document.getElementById("tickerSelectIndex");
  if (!select) return;

  const tickers = await fetchJson(`${API_BASE}/tickers`);

  select.innerHTML = "";
  const emptyOpt = document.createElement("option");
  emptyOpt.value = "";
  emptyOpt.textContent = "All tickers";
  select.appendChild(emptyOpt);

  tickers.forEach((t) => {
    const opt = document.createElement("option");
    opt.value = t.ticker;
    opt.textContent = t.ticker;
    select.appendChild(opt);
  });

  if (tickerTom) tickerTom.destroy();

  tickerTom = new TomSelect(select, {
    maxItems: 1,
    allowEmptyOption: true,
    placeholder: "Search ticker…",
    create: false,
    sortField: { field: "text", direction: "asc" },
    onChange() {
      onIndexFiltersChanged();
    },
  });
}

function applyUrlStateToIndexUI() {
  const params = getQS();
  const senator = qsGet(params, "senator", "");
  const side = qsGet(params, "side", "");
  const ticker = qsGet(params, "ticker", "");

  const senatorEl = document.getElementById("senatorSelect");
  const sideEl = document.getElementById("sideSelect");

  if (senatorEl) senatorEl.value = senator;
  if (sideEl) sideEl.value = side;
  if (tickerTom && ticker) tickerTom.setValue(ticker, true);

  const offsetFromUrl = parseInt(qsGet(params, "offset", "0"), 10);
  if (!Number.isNaN(offsetFromUrl) && offsetFromUrl >= 0) offset = offsetFromUrl;
}

function updateUrlFromIndexUI() {
  const params = getQS();
  const filters = getIndexFilters();

  if (filters.senator) params.set("senator", filters.senator);
  else params.delete("senator");

  if (filters.side) params.set("side", filters.side);
  else params.delete("side");

  if (filters.ticker) params.set("ticker", filters.ticker);
  else params.delete("ticker");

  params.set("limit", String(limit));
  params.set("offset", String(offset));

  setQS(params);
}

function onIndexFiltersChanged() {
  offset = 0;
  loadTransactions(false);
}

function clearFilters() {
  document.getElementById("senatorSelect").value = "";
  document.getElementById("sideSelect").value = "";
  if (tickerTom) tickerTom.setValue("", true);
  onIndexFiltersChanged();
}

// Optional chart listeners for pages that include chart controls.
document.getElementById("periodSelect")?.addEventListener("change", refreshActivity);
document.getElementById("sideAggSelect")?.addEventListener("change", refreshActivity);
document.getElementById("bucketSelect")?.addEventListener("change", renderSelectedBucket);
document.getElementById("loadChartBtn")?.addEventListener("click", refreshActivity);

// Index page listeners.
document.getElementById("senatorSelect")?.addEventListener("change", onIndexFiltersChanged);
document.getElementById("sideSelect")?.addEventListener("change", onIndexFiltersChanged);
document.getElementById("loadBtn")?.addEventListener("click", () => onIndexFiltersChanged());
document.getElementById("clearBtn")?.addEventListener("click", clearFilters);

document.getElementById("prevBtn")?.addEventListener("click", () => {
  offset = Math.max(0, offset - limit);
  loadTransactions(false);
});

document.getElementById("nextBtn")?.addEventListener("click", () => {
  offset += limit;
  loadTransactions(false);
});

(async () => {
  try {
    setLoading(true);
    await loadSenators();
    await loadTickersIndex();
    applyUrlStateToIndexUI();
    await loadTransactions(false);
    refreshActivity();
  } catch (err) {
    console.error(err);
    setStatus(`Startup error: ${err.message}`);
  } finally {
    setLoading(false);
  }
})();
