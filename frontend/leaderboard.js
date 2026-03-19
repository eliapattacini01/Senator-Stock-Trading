const LB_API = window.API_BASE;

let _lbData    = [];
let _lbPeriod  = "1M";
let _lbChamber = "";

function chamberBadgeLb(c) {
  if (!c) return "";
  const cls = c === "Senate" ? "badge-senate" : "badge-house";
  return `<span class="${cls}">${c}</span>`;
}

function fmtReturn(r) {
  const pct  = (r * 100).toFixed(2);
  const sign = r >= 0 ? "+" : "";
  const col  = r >= 0 ? "var(--buy-color)" : "var(--sell-color)";
  return `<span style="color:${col};font-weight:700">${sign}${pct}%</span>`;
}

function fmtDollars(n) {
  if (!n) return "—";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "K";
  return "$" + n.toLocaleString();
}

function renderPodiumCard(member, rank) {
  if (!member) return "";
  const medals = ["🥇", "🥈", "🥉"];
  const colors  = ["#f59e0b", "#94a3b8", "#cd7f32"];
  const r = member.period_return;
  const sign = r >= 0 ? "+" : "";
  const col  = r >= 0 ? "var(--buy-color)" : "var(--sell-color)";
  return `
    <div class="stat-card" style="border-left:3px solid ${colors[rank - 1]};cursor:pointer"
         onclick="location.href='portfolio.html?person=${encodeURIComponent(member.full_name)}'">
      <div class="d-flex justify-content-between align-items-start">
        <span style="font-size:1.4rem">${medals[rank - 1]}</span>
        ${chamberBadgeLb(member.chamber)}
      </div>
      <div class="fw-semibold mt-2" style="font-size:0.95rem">${member.full_name}</div>
      <div style="font-size:1.6rem;font-weight:700;color:${col};font-variant-numeric:tabular-nums">
        ${sign}${(r * 100).toFixed(2)}%
      </div>
      <div class="d-flex gap-3 mt-1" style="font-size:0.75rem;color:var(--text-dim)">
        <span>${member.n_trades} trades</span>
        <span>${fmtDollars(member.total_invested)} deployed</span>
      </div>
    </div>`;
}

function renderLeaderboard(data) {
  const filtered = _lbChamber
    ? data.filter(m => m.chamber === _lbChamber)
    : data;

  const podium    = document.getElementById("lbPodium");
  const tableCard = document.getElementById("lbTableCard");
  const empty     = document.getElementById("lbEmpty");

  if (filtered.length === 0) {
    podium.classList.add("d-none");
    tableCard.classList.add("d-none");
    empty.classList.remove("d-none");
    return;
  }

  empty.classList.add("d-none");

  // Top 3 podium
  podium.classList.remove("d-none");
  document.getElementById("podium1").innerHTML = renderPodiumCard(filtered[0], 1);
  document.getElementById("podium2").innerHTML = renderPodiumCard(filtered[1], 2);
  document.getElementById("podium3").innerHTML = renderPodiumCard(filtered[2], 3);

  // Table (rank 4 onward — or all if < 4)
  const tableRows = filtered.slice(3);
  if (tableRows.length === 0) {
    tableCard.classList.add("d-none");
    return;
  }

  tableCard.classList.remove("d-none");
  const tbody = document.querySelector("#lbTable tbody");
  tbody.innerHTML = "";
  tableRows.forEach((m, i) => {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    tr.onclick = () => location.href = `portfolio.html?person=${encodeURIComponent(m.full_name)}`;
    tr.innerHTML = `
      <td class="text-secondary" style="font-size:0.8rem">${i + 4}</td>
      <td class="fw-medium">${m.full_name}</td>
      <td>${chamberBadgeLb(m.chamber)}</td>
      <td class="text-end">${fmtReturn(m.period_return)}</td>
      <td class="text-end text-secondary num" style="font-size:0.85rem">${fmtDollars(m.total_invested)}</td>
      <td class="text-end text-secondary" style="font-size:0.85rem">${m.n_trades}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function loadLeaderboard() {
  document.getElementById("lbLoading").classList.remove("d-none");
  document.getElementById("lbPodium").classList.add("d-none");
  document.getElementById("lbTableCard").classList.add("d-none");
  document.getElementById("lbEmpty").classList.add("d-none");
  document.getElementById("lbStatus").classList.add("d-none");

  try {
    const data = await fetch(`${LB_API}/leaderboard?period=${_lbPeriod}&limit=50`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
    _lbData = data;
    renderLeaderboard(data);
  } catch (err) {
    const el = document.getElementById("lbStatus");
    el.textContent = `Error loading leaderboard: ${err.message}`;
    el.classList.remove("d-none");
  } finally {
    document.getElementById("lbLoading").classList.add("d-none");
  }
}

// Period buttons
document.getElementById("lbPeriods").addEventListener("click", e => {
  const btn = e.target.closest(".period-btn");
  if (!btn) return;
  document.querySelectorAll("#lbPeriods .period-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  _lbPeriod = btn.dataset.period;
  loadLeaderboard();
});

// Chamber filter (client-side, no re-fetch)
document.getElementById("chamberFilter").addEventListener("change", e => {
  _lbChamber = e.target.value;
  renderLeaderboard(_lbData);
});

loadLeaderboard();

/* ── Top Stocks ─────────────────────────────────────────────────────────── */

function renderTickerList(rows, containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!rows.length) { el.innerHTML = `<div class="text-secondary small">No data</div>`; return; }

  el.innerHTML = rows.map((r, i) => {
    const changeHtml = r.price_change != null
      ? (() => {
          const pct  = (r.price_change * 100).toFixed(1);
          const sign = r.price_change >= 0 ? "+" : "";
          const col  = r.price_change >= 0 ? "var(--buy-color)" : "var(--sell-color)";
          return `<span style="color:${col};font-weight:600">${sign}${pct}%</span>`;
        })()
      : `<span class="text-secondary">—</span>`;

    return `
      <div class="d-flex align-items-center justify-content-between py-2 ${i < rows.length - 1 ? "border-bottom" : ""}" style="border-color:rgba(255,255,255,0.06)!important">
        <div class="d-flex align-items-center gap-3">
          <span class="text-secondary" style="font-size:0.75rem;width:1rem">${i + 1}</span>
          <a href="timeseries.html?ticker=${encodeURIComponent(r.ticker)}"
             class="fw-semibold badge-ticker text-decoration-none" style="font-size:0.95rem">${r.ticker}</a>
          <span class="text-secondary" style="font-size:0.75rem">${r.n_trades} trades · ${r.n_members} members</span>
        </div>
        <div>${changeHtml}</div>
      </div>`;
  }).join("");
}

async function loadTopStocks() {
  document.getElementById("tsLoading")?.classList.remove("d-none");
  document.getElementById("tsCards")?.classList.add("d-none");
  try {
    const url = `${LB_API}/top-stocks?period=${_lbPeriod}&top_n=5${_lbChamber ? "&chamber=" + encodeURIComponent(_lbChamber) : ""}`;
    const data = await fetch(url).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); });
    renderTickerList(data.buys,  "tsBuyList");
    renderTickerList(data.sells, "tsSellList");
    document.getElementById("tsCards")?.classList.remove("d-none");
  } catch (err) {
    console.warn("Top stocks error:", err);
  } finally {
    document.getElementById("tsLoading")?.classList.add("d-none");
  }
}

loadTopStocks();

// Re-load top stocks when period or chamber changes
document.getElementById("lbPeriods").addEventListener("click", e => {
  if (e.target.closest(".period-btn")) loadTopStocks();
});
document.getElementById("chamberFilter").addEventListener("change", () => loadTopStocks());
