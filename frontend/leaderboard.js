const LB_API = window.API_BASE;

let _lbData    = [];
let _lbPeriod  = "1M";
let _lbChamber = "";

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function chamberBadgeLb(c) {
  if (!c) return "";
  return `<span style="font-size:0.65rem;padding:2px 8px;border-radius:9999px;background:#353437;color:#c3c6d8;border:1px solid rgba(67,70,85,0.25);font-family:'Space Grotesk',sans-serif;letter-spacing:0.04em;">${c.toUpperCase()}</span>`;
}

function fmtDollars(n) {
  if (!n) return "—";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return "$" + (n / 1e3).toFixed(0) + "K";
  return "$" + n.toLocaleString();
}

/**
 * Avatar HTML for use INSIDE a fixed-size container (e.g. w-16 h-16).
 * Uses w-full/h-full so it fills the parent. Compatible with _fillAvatars().
 * imgClass — extra Tailwind classes on the <img> (e.g. hover effects)
 * Fallback uses inline styles so it renders correctly regardless of Tailwind CDN timing.
 */
function _containerAvatar(name, imgClass, fbIconSize) {
  const url  = window.getMemberPhoto?.(name);
  const safe = (name || "").replace(/"/g, "&quot;");
  const shown   = url ? "display:block"  : "display:none";
  const fbShown = url ? "display:none"   : "display:flex";
  const src = url || "";
  const iconPx = fbIconSize || 20;
  return `<img src="${src}" data-member="${safe}" alt=""
      class="${imgClass} member-avatar-img"
      style="${shown};width:100%;height:100%;object-fit:cover;"
      onerror="this.style.display='none';var n=this.nextElementSibling;if(n)n.style.display='flex';">
    <div class="member-avatar-fallback"
         style="${fbShown};width:100%;height:100%;background:#353437;align-items:center;justify-content:center;border-radius:inherit;">
      <span class="material-symbols-outlined" style="font-size:${iconPx}px;color:#8d90a1;">person</span>
    </div>`;
}

/* ── Podium cards ────────────────────────────────────────────────────────── */

function renderPodiumCard(member, rank) {
  if (!member) return "";
  const r    = member.period_return;
  const pct  = (r * 100).toFixed(2);
  const sign = r >= 0 ? "+" : "";
  const retColor = r >= 0 ? "#53df9a" : "#ffb4ab";
  const url  = `portfolio.html?person=${encodeURIComponent(member.full_name)}`;

  if (rank === 1) {
    const avatar = _containerAvatar(
      member.full_name,
      "w-full h-full rounded-full object-cover shadow-lg group-hover:scale-110 transition-transform duration-700",
      40
    );
    return `
      <div onclick="location.href='${url}'"
           class="glass-card p-10 rounded-xl border-t border-primary/20 bg-gradient-to-b from-surface-container-high to-surface-container-low shadow-2xl relative overflow-hidden group cursor-pointer">
        <div class="absolute top-0 right-0 p-4">
          <span class="material-symbols-outlined text-4xl" style="color:#ddb8ff;font-variation-settings:'FILL' 1;">workspace_premium</span>
        </div>
        <div class="flex flex-col items-center text-center mb-8">
          <div class="w-24 h-24 rounded-full overflow-hidden border-4 border-primary p-1 mb-6">
            ${avatar}
          </div>
          <p class="font-label text-xs uppercase tracking-[0.3em] mb-2" style="color:#b4c5ff;">${member.chamber || ""}</p>
          <h3 class="font-headline font-extrabold text-4xl tracking-tight">${member.full_name}</h3>
        </div>
        <div class="flex flex-col items-center mb-8">
          <span class="text-6xl font-headline font-black tracking-tighter" style="color:${retColor};">${sign}${pct}%</span>
          <span class="text-sm font-label mt-1 tracking-widest" style="color:#c3c6d8;">RETURN</span>
        </div>
        <div class="grid grid-cols-2 gap-8 pt-8" style="border-top:1px solid rgba(67,70,85,0.2);">
          <div class="text-center">
            <p class="text-xs font-label uppercase mb-1" style="color:#8d90a1;">Trades</p>
            <p class="text-xl font-headline font-bold">${member.n_trades} Trades</p>
          </div>
          <div class="text-center">
            <p class="text-xs font-label uppercase mb-1" style="color:#8d90a1;">Capital</p>
            <p class="text-xl font-headline font-bold">${fmtDollars(member.total_invested)}</p>
          </div>
        </div>
      </div>`;
  }

  // Rank 2 or 3 — side card
  const avatar = _containerAvatar(
    member.full_name,
    "w-full h-full object-cover grayscale group-hover:grayscale-0 transition-all duration-700",
    28
  );
  return `
    <div onclick="location.href='${url}'"
         class="glass-card p-8 rounded-xl relative overflow-hidden group hover:bg-surface-container-high/40 transition-all duration-500 cursor-pointer"
         style="border:1px solid rgba(67,70,85,0.15);">
      <div class="absolute -top-4 -right-4 w-24 h-24 rounded-full blur-3xl opacity-20" style="background:#353437;"></div>
      <div class="flex justify-between items-start mb-6">
        <div class="w-16 h-16 rounded-full overflow-hidden" style="border:2px solid rgba(67,70,85,0.3);">
          ${avatar}
        </div>
        <span class="font-label text-3xl font-bold" style="color:rgba(67,70,85,0.6);">#${rank}</span>
      </div>
      <p class="font-label text-xs uppercase tracking-widest mb-1" style="color:#b4c5ff;">${member.chamber || ""}</p>
      <h3 class="font-headline font-bold text-2xl mb-4">${member.full_name}</h3>
      <div class="flex items-baseline gap-2 mb-6">
        <span class="text-4xl font-headline font-extrabold" style="color:${retColor};">${sign}${pct}%</span>
        <span class="text-xs font-label" style="color:#8d90a1;">RETURN</span>
      </div>
      <div class="grid grid-cols-2 gap-4 pt-4" style="border-top:1px solid rgba(67,70,85,0.15);">
        <div>
          <p class="font-label uppercase" style="font-size:0.6rem;color:#8d90a1;margin-bottom:0.2rem;">Trades</p>
          <p class="text-lg font-headline font-bold">${member.n_trades}</p>
        </div>
        <div>
          <p class="font-label uppercase" style="font-size:0.6rem;color:#8d90a1;margin-bottom:0.2rem;">Capital</p>
          <p class="text-lg font-headline font-bold">${fmtDollars(member.total_invested)}</p>
        </div>
      </div>
    </div>`;
}

/* ── Leaderboard render ──────────────────────────────────────────────────── */

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

  // Table rows (rank 4+)
  const tableRows = filtered.slice(3);
  if (tableRows.length === 0) {
    tableCard.classList.add("d-none");
    return;
  }

  tableCard.classList.remove("d-none");
  const tbody = document.querySelector("#lbTable tbody");
  tbody.innerHTML = "";
  tableRows.forEach((m, i) => {
    const r         = m.period_return;
    const pct       = (r * 100).toFixed(2);
    const sign      = r >= 0 ? "+" : "";
    const retColor  = r >= 0 ? "#53df9a" : "#ffb4ab";
    const rankStr   = String(i + 4).padStart(2, "0");
    const avatar    = _containerAvatar(m.full_name, "w-full h-full object-cover", 14);

    const tr = document.createElement("tr");
    tr.className = "hover:bg-surface-container-high/30 transition-colors group cursor-pointer";
    tr.onclick   = () => location.href = `portfolio.html?person=${encodeURIComponent(m.full_name)}`;
    tr.innerHTML = `
      <td class="px-6 py-5 font-label font-bold transition-colors group-hover:text-primary" style="color:#434655;">${rankStr}</td>
      <td class="px-6 py-5">
        <div class="flex items-center gap-3">
          <div class="w-8 h-8 rounded-full overflow-hidden flex-shrink-0" style="background:#353437;">
            ${avatar}
          </div>
          <span class="font-headline font-semibold text-sm">${m.full_name}</span>
        </div>
      </td>
      <td class="px-6 py-5">${chamberBadgeLb(m.chamber)}</td>
      <td class="px-6 py-5">
        <span class="font-label font-bold" style="color:${retColor};">${sign}${pct}%</span>
      </td>
      <td class="px-6 py-5 font-label text-sm">${fmtDollars(m.total_invested)}</td>
      <td class="px-6 py-5 text-right font-label text-sm" style="color:#8d90a1;">${m.n_trades} Trades</td>
    `;
    tbody.appendChild(tr);
  });
}

/* ── Load / event wiring ─────────────────────────────────────────────────── */

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

// Re-render with photos once legislators.json finishes loading
document.addEventListener("legislators:ready", function () {
  if (_lbData.length) renderLeaderboard(_lbData);
});

/* ── Top Stocks ──────────────────────────────────────────────────────────── */

function renderTickerList(rows, containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  if (!rows.length) {
    el.innerHTML = `<p class="font-label text-xs text-on-surface-variant">No data available</p>`;
    return;
  }

  const isBuy      = containerId === "tsBuyList";
  const tickerColor = isBuy ? "#b4c5ff" : "#ffb4ab";

  el.innerHTML = rows.map((r, i) => {
    const changeHtml = r.price_change != null
      ? (() => {
          const pct   = (r.price_change * 100).toFixed(1);
          const sign  = r.price_change >= 0 ? "+" : "";
          const color = r.price_change >= 0 ? "#53df9a" : "#ffb4ab";
          return `<span class="font-label font-bold text-sm" style="color:${color};">${sign}${pct}%</span>`;
        })()
      : `<span class="font-label text-sm" style="color:#8d90a1;">—</span>`;

    const sep = i < rows.length - 1
      ? "border-bottom:1px solid rgba(67,70,85,0.12);padding-bottom:1.25rem;"
      : "";

    return `
      <div style="display:flex;align-items:center;justify-content:space-between;${sep}">
        <div style="display:flex;align-items:center;gap:1rem;">
          <a href="timeseries.html?ticker=${encodeURIComponent(r.ticker)}"
             onclick="event.stopPropagation();"
             style="width:42px;height:42px;border-radius:6px;background:#353437;display:flex;align-items:center;justify-content:center;font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.7rem;color:${tickerColor};text-decoration:none;flex-shrink:0;">${r.ticker}</a>
          <div>
            <p class="font-headline font-semibold text-sm" style="margin:0 0 2px;">${r.ticker}</p>
            <p style="font-family:'Space Grotesk',sans-serif;font-size:0.65rem;color:#8d90a1;text-transform:uppercase;margin:0;">${r.n_trades} trades · ${r.n_members} members</p>
          </div>
        </div>
        <div style="text-align:right;">${changeHtml}</div>
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
