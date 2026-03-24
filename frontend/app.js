const API_BASE = window.API_BASE;

/* ── URL state helpers ──────────────────────────────────────────────────────── */
function getQS()  { return new URLSearchParams(window.location.search); }
function setQS(p) {
  const qs = p.toString();
  history.replaceState(null, "", qs ? `${location.pathname}?${qs}` : location.pathname);
}
function qsGet(p, key, fb = "") { const v = p.get(key); return v ?? fb; }

/* ── State ──────────────────────────────────────────────────────────────────── */
let offset   = 0;
const limit  = 50;
let tickerTom = null;

/* ── UI helpers ─────────────────────────────────────────────────────────────── */
function setLoading(on) {
  document.getElementById("loading")?.classList.toggle("d-none", !on);
}

function setStatus(msg = "") {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = msg;
  el.classList.toggle("d-none", !msg);
}

function setPageInfo() {
  const el = document.getElementById("pageInfo");
  if (!el) return;
  const page = Math.floor(offset / limit) + 1;
  el.textContent = `Page ${page}  (rows ${offset + 1}–${offset + limit})`;
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status} — ${txt.slice(0, 200)}`);
  }
  return res.json();
}

function fmt(n) {
  if (n == null) return "—";
  return Number(n).toLocaleString();
}

/* ── Stats ──────────────────────────────────────────────────────────────────── */
async function loadStats() {
  try {
    const s = await fetchJson(`${API_BASE}/stats`);
    document.getElementById("statTotal").textContent   = fmt(s.total_transactions);
    document.getElementById("statMembers").textContent = fmt(s.total_members);
    document.getElementById("statBuys").textContent    = fmt(s.total_buys);
    document.getElementById("statSells").textContent   = fmt(s.total_sells);
    document.getElementById("statMembersSub").textContent =
      `${fmt(s.senate_members ?? 0)} Senate · ${fmt(s.house_members ?? 0)} House`;
  } catch (_) {}
}

/* ── Senators / Members dropdown ────────────────────────────────────────────── */
async function loadSenators(chamber = "") {
  const select = document.getElementById("senatorSelect");
  if (!select) return;
  try {
    let url = `${API_BASE}/senators?limit=1000`;
    if (chamber) url += `&chamber=${encodeURIComponent(chamber)}`;
    const data = await fetchJson(url);
    select.innerHTML = `<option value="">All Members</option>`;
    data.forEach(s => {
      const opt = document.createElement("option");
      opt.value = opt.textContent = s.full_name;
      select.appendChild(opt);
    });
  } catch (err) {
    console.error(err);
  }
}

/* ── Tickers dropdown ───────────────────────────────────────────────────────── */
async function loadTickersIndex() {
  setLoading(true);
  try {
    const tickers = await fetchJson(`${API_BASE}/tickers`);
    const select  = document.getElementById("tickerSelectIndex");
    if (!select) return;

    select.innerHTML = `<option value="">All tickers</option>`;
    tickers.forEach(t => {
      const opt = document.createElement("option");
      opt.value = opt.textContent = t.ticker;
      select.appendChild(opt);
    });

    if (tickerTom) tickerTom.destroy();
    tickerTom = new TomSelect(select, {
      maxItems: 1,
      allowEmptyOption: true,
      placeholder: "Search ticker…",
      create: false,
      sortField: { field: "text", direction: "asc" },
      onChange() { offset = 0; updateUrl(); loadTransactions(false); },
    });
  } catch (err) {
    console.error(err);
    setStatus(`Error loading tickers: ${err.message}`);
  } finally {
    setLoading(false);
  }
}

/* ── Transactions ───────────────────────────────────────────────────────────── */
function chamberBadge(c) {
  if (!c) return "";
  return `<span style="font-family:'Space Grotesk',sans-serif;font-size:0.65rem;font-weight:500;padding:2px 10px;border-radius:9999px;background:#1f1f21;color:#c3c6d8;border:1px solid rgba(67,70,85,0.3);">${c}</span>`;
}

async function loadTransactions(resetOffset = false) {
  const tbody = document.querySelector("#transactionsTable tbody");
  if (!tbody) return;
  if (resetOffset) offset = 0;

  setStatus("");
  setLoading(true);
  setPageInfo();

  const senator = document.getElementById("senatorSelect")?.value  ?? "";
  const side    = document.getElementById("sideSelect")?.value     ?? "";
  const chamber = document.getElementById("chamberSelect")?.value  ?? "";
  const ticker  = tickerTom?.getValue()?.trim().toUpperCase() ?? "";

  let url = `${API_BASE}/transactions?limit=${limit}&offset=${offset}`;
  if (senator) url += `&senator=${encodeURIComponent(senator)}`;
  if (side)    url += `&side=${encodeURIComponent(side)}`;
  if (ticker)  url += `&ticker=${encodeURIComponent(ticker)}`;
  if (chamber) url += `&chamber=${encodeURIComponent(chamber)}`;

  try {
    const data = await fetchJson(url);
    tbody.innerHTML = "";

    data.forEach(t => {
      const tr = document.createElement("tr");
      tr.style.cssText = "transition:background 0.15s;cursor:default;";
      tr.onmouseenter = () => tr.style.background = "rgba(255,255,255,0.03)";
      tr.onmouseleave = () => tr.style.background = "";

      const avatarHtml = window.memberAvatarHtml
        ? window.memberAvatarHtml(t.full_name, 36)
        : `<div style="width:36px;height:36px;border-radius:50%;background:#353437;display:flex;align-items:center;justify-content:center;flex-shrink:0;"><span class="material-symbols-outlined" style="font-size:18px;color:#8d90a1;">person</span></div>`;

      const sideBadge = t.side === "BUY"
        ? `<span style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.62rem;padding:2px 9px;border-radius:9999px;background:rgba(83,223,154,0.1);color:#53df9a;border:1px solid rgba(83,223,154,0.2);letter-spacing:0.05em;">BUY</span>`
        : `<span style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.62rem;padding:2px 9px;border-radius:9999px;background:rgba(255,180,171,0.1);color:#ffb4ab;border:1px solid rgba(255,180,171,0.2);letter-spacing:0.05em;">SELL</span>`;

      const amtFmt = t.tx_estimate != null ? "$" + fmt(t.tx_estimate) : "—";

      tr.innerHTML = `
        <td style="padding:16px 24px;">
          <div style="display:flex;align-items:center;gap:12px;">
            <div style="width:36px;height:36px;border-radius:50%;overflow:hidden;flex-shrink:0;border:1px solid rgba(67,70,85,0.2);">${avatarHtml}</div>
            <a href="portfolio.html?person=${encodeURIComponent(t.full_name)}"
               style="font-family:'Plus Jakarta Sans',sans-serif;font-weight:600;font-size:0.875rem;color:#e5e1e4;text-decoration:none;"
               onmouseover="this.style.color='#b4c5ff'" onmouseout="this.style.color='#e5e1e4'">${t.full_name}</a>
          </div>
        </td>
        <td style="padding:16px 24px;">${chamberBadge(t.chamber)}</td>
        <td style="padding:16px 24px;">
          <span style="font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.7rem;padding:4px 8px;border-radius:6px;background:rgba(255,255,255,0.05);color:#b4c5ff;">${t.ticker}</span>
        </td>
        <td style="padding:16px 24px;">${sideBadge}</td>
        <td style="padding:16px 24px;font-family:'Space Grotesk',sans-serif;font-size:0.8rem;color:#c3c6d8;">${t.tx_date ?? ""}</td>
        <td style="padding:16px 24px;font-family:'Space Grotesk',sans-serif;font-size:0.8rem;color:#8d90a1;">${t.file_date ?? "—"}</td>
        <td style="padding:16px 24px;text-align:right;font-family:'Space Grotesk',sans-serif;font-weight:700;font-size:0.875rem;color:#e5e1e4;">${amtFmt}</td>
      `;
      tbody.appendChild(tr);
    });

    if (data.length === 0) setStatus("No results for the selected filters.");
  } catch (err) {
    console.error(err);
    setStatus(`Error loading transactions: ${err.message}`);
  } finally {
    setLoading(false);
  }
}

/* ── URL state ──────────────────────────────────────────────────────────────── */
function updateUrl() {
  const p = getQS();
  const senator = document.getElementById("senatorSelect")?.value  ?? "";
  const side    = document.getElementById("sideSelect")?.value     ?? "";
  const chamber = document.getElementById("chamberSelect")?.value  ?? "";
  const ticker  = tickerTom?.getValue() ?? "";

  senator ? p.set("senator", senator) : p.delete("senator");
  side    ? p.set("side",    side)    : p.delete("side");
  chamber ? p.set("chamber", chamber) : p.delete("chamber");
  ticker  ? p.set("ticker",  ticker)  : p.delete("ticker");
  p.set("offset", String(offset));
  setQS(p);
}

function applyUrl() {
  const p       = getQS();
  const senator = qsGet(p, "senator");
  const side    = qsGet(p, "side");
  const chamber = qsGet(p, "chamber");
  const ticker  = qsGet(p, "ticker");
  const off     = parseInt(qsGet(p, "offset", "0"), 10);

  if (!isNaN(off) && off >= 0) offset = off;

  const senEl = document.getElementById("senatorSelect");
  const sideEl= document.getElementById("sideSelect");
  const chEl  = document.getElementById("chamberSelect");
  if (senEl && senator) senEl.value = senator;
  if (sideEl && side)   sideEl.value = side;
  if (chEl  && chamber) chEl.value  = chamber;
  if (tickerTom && ticker) tickerTom.setValue(ticker, true);
}

/* ── Event wiring ───────────────────────────────────────────────────────────── */
function onFiltersChanged() {
  offset = 0;
  updateUrl();
  loadTransactions(false);
}

document.getElementById("chamberSelect")?.addEventListener("change", async () => {
  const chamber = document.getElementById("chamberSelect").value;
  await loadSenators(chamber);
  onFiltersChanged();
});
document.getElementById("senatorSelect")?.addEventListener("change", onFiltersChanged);
document.getElementById("sideSelect")?.addEventListener("change", onFiltersChanged);
document.getElementById("loadBtn")?.addEventListener("click", onFiltersChanged);

document.getElementById("nextBtn")?.addEventListener("click", () => {
  offset += limit; updateUrl(); loadTransactions(false);
});
document.getElementById("prevBtn")?.addEventListener("click", () => {
  offset = Math.max(0, offset - limit); updateUrl(); loadTransactions(false);
});

/* ── Init ───────────────────────────────────────────────────────────────────── */
(async () => {
  setPageInfo();
  await Promise.all([loadStats(), loadSenators(), loadTickersIndex()]);
  applyUrl();
  updateUrl();
  loadTransactions(false);
})();

// Re-render table photos once legislators.json finishes loading
document.addEventListener("legislators:ready", function () {
  const tbody = document.querySelector("#transactionsTable tbody");
  if (tbody && tbody.children.length) loadTransactions(false);
});
