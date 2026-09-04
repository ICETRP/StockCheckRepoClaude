const state = { trades: [], closingId: null };

function fmt(n) {
  if (n === "" || n === null || n === undefined) return "—";
  const num = Number(n);
  return Number.isFinite(num) ? num.toFixed(2) : String(n);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function loadTrades() {
  const box = document.getElementById("trades-table");
  box.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const res = await fetch("/api/trades");
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    state.trades = data;
    renderStats();
    renderTable();
  } catch (e) {
    box.innerHTML = `<p class="muted">Error loading trades: ${esc(e.message)}</p>`;
  }
}

function renderStats() {
  const closed = state.trades.filter((t) => t.status === "closed");
  const open = state.trades.filter((t) => t.status !== "closed");
  const wins = closed.filter((t) => Number(t.pnl) > 0);
  const winRate = closed.length ? (wins.length / closed.length) * 100 : null;
  const rValues = closed.map((t) => Number(t.r_multiple)).filter((n) => Number.isFinite(n));
  const avgR = rValues.length ? rValues.reduce((a, b) => a + b, 0) / rValues.length : null;

  document.getElementById("stats-box").innerHTML = `
    <div class="meta-box"><div class="meta-label">Total</div><div class="meta-value">${state.trades.length}</div></div>
    <div class="meta-box"><div class="meta-label">Open</div><div class="meta-value">${open.length}</div></div>
    <div class="meta-box"><div class="meta-label">Closed</div><div class="meta-value">${closed.length}</div></div>
    <div class="meta-box"><div class="meta-label">Win Rate</div><div class="meta-value">${winRate === null ? "—" : winRate.toFixed(0) + "%"}</div></div>
    <div class="meta-box"><div class="meta-label">Avg R-Multiple</div><div class="meta-value">${avgR === null ? "—" : avgR.toFixed(2) + "R"}</div></div>
  `;
}

function renderTable() {
  const box = document.getElementById("trades-table");
  if (!state.trades.length) {
    box.innerHTML = '<p class="muted">No trades logged yet.</p>';
    return;
  }
  const rows = [...state.trades].reverse().map((t) => `
    <tr>
      <td>${esc(t.ticker)}</td>
      <td>${esc(t.strategy)}</td>
      <td>${esc(t.entry_date)}</td>
      <td>${fmt(t.entry_price)}</td>
      <td>${fmt(t.stop_price)}</td>
      <td>${t.target_price === "" ? "—" : fmt(t.target_price)}</td>
      <td>${esc(t.qty)}</td>
      <td>${esc(t.status)}</td>
      <td>${esc(t.exit_date) || "—"}</td>
      <td>${t.exit_price === "" ? "—" : fmt(t.exit_price)}</td>
      <td>${t.pnl === "" ? "—" : fmt(t.pnl)}</td>
      <td>${t.r_multiple === "" ? "—" : fmt(t.r_multiple) + "R"}</td>
      <td>${esc(t.notes)}</td>
      <td>${t.status === "closed" ? "" : `<button class="btn btn-small close-btn" data-id="${esc(t.id)}" data-ticker="${esc(t.ticker)}">Close</button>`}</td>
    </tr>
  `).join("");
  box.innerHTML = `
    <table>
      <thead><tr>
        <th>Ticker</th><th>Strategy</th><th>Entry Date</th><th>Entry</th><th>Stop</th>
        <th>Target</th><th>Qty</th><th>Status</th><th>Exit Date</th><th>Exit</th>
        <th>PnL</th><th>R</th><th>Notes</th><th></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
  box.querySelectorAll(".close-btn").forEach((btn) => {
    btn.addEventListener("click", () => openCloseModal(btn.dataset.id, btn.dataset.ticker));
  });
}

function openCloseModal(id, ticker) {
  state.closingId = id;
  document.getElementById("close-ticker").textContent = ticker;
  document.getElementById("c-exit-date").value = new Date().toISOString().slice(0, 10);
  document.getElementById("c-exit-price").value = "";
  document.getElementById("close-status").textContent = "";
  document.getElementById("close-modal").hidden = false;
}

document.getElementById("close-modal-x").addEventListener("click", () => {
  document.getElementById("close-modal").hidden = true;
});

document.getElementById("c-confirm").addEventListener("click", async () => {
  const id = state.closingId;
  const statusEl = document.getElementById("close-status");
  const body = {
    exit_date: document.getElementById("c-exit-date").value,
    exit_price: document.getElementById("c-exit-price").value,
    exit_reason: document.getElementById("c-exit-reason").value,
  };
  statusEl.textContent = "Saving…";
  try {
    const res = await fetch(`/api/trades/${encodeURIComponent(id)}/close`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    document.getElementById("close-modal").hidden = true;
    await loadTrades();
  } catch (e) {
    statusEl.textContent = "Error: " + e.message;
  }
});

document.getElementById("new-trade-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const statusEl = document.getElementById("new-trade-status");
  const body = {
    ticker: document.getElementById("t-ticker").value,
    strategy: document.getElementById("t-strategy").value,
    entry_date: document.getElementById("t-entry-date").value,
    qty: document.getElementById("t-qty").value,
    entry_price: document.getElementById("t-entry-price").value,
    stop_price: document.getElementById("t-stop-price").value,
    target_price: document.getElementById("t-target-price").value,
    notes: document.getElementById("t-notes").value,
  };
  statusEl.textContent = "Saving…";
  try {
    const res = await fetch("/api/trades", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    statusEl.textContent = "Saved.";
    e.target.reset();
    document.getElementById("t-entry-date").value = new Date().toISOString().slice(0, 10);
    await loadTrades();
  } catch (e2) {
    statusEl.textContent = "Error: " + e2.message;
  }
});

document.getElementById("refresh-trades").addEventListener("click", loadTrades);

document.getElementById("t-entry-date").value = new Date().toISOString().slice(0, 10);
loadTrades();
