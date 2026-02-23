const API_BASE = window.API_BASE;
let topChart = null;
let cachedActivity = [];

function getQS() {
  return new URLSearchParams(window.location.search);
}

function setQS(params) {
  const qs = params.toString();
  const newUrl = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
  history.replaceState(null, "", newUrl);
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
    throw new Error(`HTTP ${res.status} ${res.statusText} — ${text.slice(0, 200)}`);
  }
  return await res.json();
}

function metricLabel(metric) {
  if (metric === "n_trades") return "Trades";
  if (metric === "total_estimate") return "Estimated amount";
  return "Unique senators";
}

function valueForMetric(row, metric) {
  return Number(row?.[metric] ?? 0);
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

function updateSummaryCards(rows, metric) {
  const bucketCount = new Set(rows.map((r) => r.bucket_start)).size;
  const currentBucket = document.getElementById("bucketSelect")?.value;
  const visibleRows = rows.filter((r) => r.bucket_start === currentBucket);
  const peak = visibleRows.reduce((max, r) => Math.max(max, valueForMetric(r, metric)), 0);

  const bucketEl = document.getElementById("bucketCountValue");
  const visibleEl = document.getElementById("visibleTickersValue");
  const peakEl = document.getElementById("metricPeakValue");

  if (bucketEl) bucketEl.textContent = String(bucketCount);
  if (visibleEl) visibleEl.textContent = String(visibleRows.length);
  if (peakEl) peakEl.textContent = peak.toLocaleString();
}

function renderTopTickersChart(activityRows, bucketStart, metric) {
  const rows = activityRows
    .filter((r) => r.bucket_start === bucketStart)
    .sort((a, b) => valueForMetric(b, metric) - valueForMetric(a, metric) || a.ticker.localeCompare(b.ticker));

  const labels = rows.map((r) => r.ticker);
  const values = rows.map((r) => valueForMetric(r, metric));

  const ctx = document.getElementById("topTickersChart");
  if (!ctx) return;

  if (topChart) topChart.destroy();

  topChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: metricLabel(metric),
        data: values,
        backgroundColor: "rgba(59,130,246,0.6)",
        borderColor: "rgba(96,165,250,1)",
        borderWidth: 1,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: true } },
      scales: {
        y: {
          beginAtZero: true,
          ticks: {
            callback: (value) => Number(value).toLocaleString(),
          },
        },
      },
    },
  });
}

function updateUrlFromUi() {
  const params = getQS();
  params.set("period", document.getElementById("periodSelect")?.value ?? "week");
  params.set("side", document.getElementById("sideAggSelect")?.value ?? "BUY");
  params.set("top_n", document.getElementById("topNSelect")?.value ?? "10");
  params.set("metric", document.getElementById("metricSelect")?.value ?? "n_senators");

  const bucket = document.getElementById("bucketSelect")?.value;
  if (bucket) params.set("bucket", bucket);
  else params.delete("bucket");

  setQS(params);
}

function applyUrlStateToUi() {
  const params = getQS();
  const period = params.get("period");
  const side = params.get("side");
  const topN = params.get("top_n");
  const metric = params.get("metric");

  if (["week", "month", "year"].includes(period)) document.getElementById("periodSelect").value = period;
  if (["BUY", "SELL"].includes(side)) document.getElementById("sideAggSelect").value = side;
  if (["5", "10", "15", "20"].includes(topN)) document.getElementById("topNSelect").value = topN;
  if (["n_senators", "n_trades", "total_estimate"].includes(metric)) document.getElementById("metricSelect").value = metric;
}

function renderSelectedBucket() {
  const bucket = document.getElementById("bucketSelect")?.value;
  const metric = document.getElementById("metricSelect")?.value || "n_senators";

  if (!bucket) {
    setStatus("No bucket selected.");
    return;
  }

  setStatus("");
  renderTopTickersChart(cachedActivity, bucket, metric);
  updateSummaryCards(cachedActivity, metric);
  updateUrlFromUi();
}

async function refreshActivity() {
  try {
    setStatus("");
    setLoading(true);

    const period = document.getElementById("periodSelect").value;
    const side = document.getElementById("sideAggSelect").value;
    const topN = Number(document.getElementById("topNSelect").value);

    cachedActivity = await loadActivityTop(period, side, topN);

    if (cachedActivity.length === 0) {
      setStatus("No data for this selection.");
      return;
    }

    fillBucketSelect(cachedActivity);

    const bucketFromUrl = getQS().get("bucket");
    const bucketSelect = document.getElementById("bucketSelect");
    if (bucketFromUrl && [...bucketSelect.options].some((o) => o.value === bucketFromUrl)) {
      bucketSelect.value = bucketFromUrl;
    }

    renderSelectedBucket();
  } catch (err) {
    console.error(err);
    setStatus(`Error loading chart: ${err.message}`);
  } finally {
    setLoading(false);
  }
}

document.getElementById("periodSelect")?.addEventListener("change", refreshActivity);
document.getElementById("sideAggSelect")?.addEventListener("change", refreshActivity);
document.getElementById("topNSelect")?.addEventListener("change", refreshActivity);
document.getElementById("metricSelect")?.addEventListener("change", renderSelectedBucket);
document.getElementById("bucketSelect")?.addEventListener("change", renderSelectedBucket);
document.getElementById("loadChartBtn")?.addEventListener("click", refreshActivity);

(async () => {
  applyUrlStateToUi();
  await refreshActivity();
})();
