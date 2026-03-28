/* ── Bar chart page (monthlychar.html) ──────────────────────────────────────── */
const BC_API = window.API_BASE;

let _bcData     = [];   // full API response
let _bcChart    = null;

function setStatus(msg) {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle("d-none", !msg);
}

/* Populate bucketSelect from loaded data */
function populateBuckets(data) {
  const select = document.getElementById("bucketSelect");
  const buckets = [...new Set(data.map(d => d.bucket_start))].sort().reverse();
  select.innerHTML = "";
  buckets.forEach(b => {
    const opt = document.createElement("option");
    opt.value = opt.textContent = b;
    select.appendChild(opt);
  });
}

/* Render chart for a single time bucket */
function renderBucketChart(bucketStart) {
  const rows = _bcData.filter(d => d.bucket_start === bucketStart);
  rows.sort((a, b) => b.n_senators - a.n_senators);

  const labels = rows.map(r => r.ticker);
  const values = rows.map(r => r.n_senators);

  const canvas = document.getElementById("topTickersChart");
  const ctx    = canvas.getContext("2d");

  if (_bcChart) {
    _bcChart.destroy();
    _bcChart = null;
  }

  _bcChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "# Members",
        data:   values,
        backgroundColor: "rgba(180,197,255,0.65)",
        borderColor:     "#b4c5ff",
        borderWidth:     0,
        borderRadius:    8,
        borderSkipped:   false,
        hoverBackgroundColor: "rgba(180,197,255,0.9)",
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
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
          callbacks: {
            afterLabel: ctx => {
              const row = rows[ctx.dataIndex];
              return [`${row.n_trades} trade(s)`,
                      row.total_estimate > 0
                        ? "$" + Number(row.total_estimate).toLocaleString()
                        : ""];
            },
          },
        },
      },
      scales: {
        x: {
          ticks:  { color: "#8d90a1", font: { family: "'Space Grotesk', sans-serif", size: 11 } },
          grid:   { display: false },
          border: { color: "rgba(67,70,85,0.25)" },
        },
        y: {
          beginAtZero: true,
          ticks: {
            color: "#8d90a1",
            font:  { family: "'Space Grotesk', sans-serif", size: 11 },
            stepSize: 1,
          },
          grid:   { color: "rgba(67,70,85,0.15)" },
          border: { color: "rgba(67,70,85,0.25)" },
          title: {
            display: true,
            text:    "# Unique Members",
            color:   "#8d90a1",
            font:    { family: "'Space Grotesk', sans-serif", size: 11 },
          },
        },
      },
    },
  });
}

async function loadChart() {
  const period  = document.getElementById("periodSelect").value;
  const side    = document.getElementById("sideAggSelect").value;

  setStatus("");
  const btn = document.getElementById("loadChartBtn");
  btn.disabled = true;
  btn.textContent = "Loading…";

  try {
    const url = `${BC_API}/activity/top?period=${period}&side=${side}&top_n=15`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _bcData = await res.json();

    if (_bcData.length === 0) {
      setStatus("No data found for the selected filters.");
      return;
    }

    populateBuckets(_bcData);
    const firstBucket = document.getElementById("bucketSelect").value;
    renderBucketChart(firstBucket);
  } catch (err) {
    setStatus(`Error loading chart: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Load Chart";
  }
}

document.getElementById("loadChartBtn").addEventListener("click", loadChart);

document.getElementById("bucketSelect").addEventListener("change", e => {
  if (_bcData.length > 0) renderBucketChart(e.target.value);
});

// Auto-load on page open (skipped on charts.html — tab event handles it instead)
if (!document.getElementById("charts-page")) {
  loadChart();
}
