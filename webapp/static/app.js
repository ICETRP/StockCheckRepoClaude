let SCRIPTS = {};
let currentJob = null;
let pollTimer = null;
let lastProviderUsed = 'yahoo';
let chartObj = null;

const $ = (id) => document.getElementById(id);

async function init() {
  const res = await fetch('/api/scripts');
  SCRIPTS = await res.json();

  const strategySel = $('strategy');
  strategySel.innerHTML = Object.entries(SCRIPTS)
    .map(([key, cfg]) => `<option value="${key}">${cfg.label}</option>`)
    .join('');

  strategySel.addEventListener('change', renderStrategyFields);
  $('backtest').addEventListener('change', () => {
    $('date-fields').hidden = !$('backtest').checked;
    updateCmdPreview();
  });
  $('universe').addEventListener('change', () => {
    const useUniverse = !!$('universe').value;
    $('tickers-field').hidden = useUniverse;
    $('universe-limit-field').hidden = !useUniverse;
    updateCmdPreview();
  });
  $('run-btn').addEventListener('click', runStrategy);
  $('cancel-btn').addEventListener('click', cancelJob);
  $('refresh-history').addEventListener('click', loadHistory);

  document.querySelectorAll('#provider, #tickers, #entry_mode, #start, #end, #universe_limit')
    .forEach(el => el.addEventListener('input', updateCmdPreview));

  $('chart-close').addEventListener('click', closeChart);
  $('chart-modal').addEventListener('click', (e) => { if (e.target.id === 'chart-modal') closeChart(); });
  $('chart-reload').addEventListener('click', () => {
    if (chartObj && chartObj.ticker) loadChart(chartObj.ticker, $('chart-provider').value);
  });
  $('chart-provider').addEventListener('change', () => {
    if (chartObj && chartObj.ticker) loadChart(chartObj.ticker, $('chart-provider').value);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeChart(); });

  const runTickerSearch = () => {
    const raw = $('ticker-search').value.trim().toUpperCase();
    if (!raw) return;
    lastProviderUsed = $('ticker-search-provider').value || lastProviderUsed;
    openChart(raw);
  };
  $('ticker-search-btn').addEventListener('click', runTickerSearch);
  $('ticker-search').addEventListener('keydown', (e) => { if (e.key === 'Enter') runTickerSearch(); });

  renderStrategyFields();
  loadHistory();

  const allTickers = new Set();
  Object.values(SCRIPTS).forEach(cfg => (cfg.default_watchlist || []).forEach(t => allTickers.add(t)));
  $('global-ticker-list').innerHTML = [...allTickers].sort().map(t => `<option value="${t}">`).join('');
}

function renderStrategyFields() {
  const key = $('strategy').value;
  const cfg = SCRIPTS[key];

  // provider dropdown
  const providerSel = $('provider');
  providerSel.innerHTML = cfg.providers.map(p => `<option value="${p}">${p}</option>`).join('');

  // ticker datalist + placeholder
  const dl = $('ticker-list');
  dl.innerHTML = cfg.default_watchlist.map(t => `<option value="${t}">`).join('');
  $('tickers').value = '';
  $('tickers').placeholder = 'default: ' + cfg.default_watchlist.slice(0, 5).join(', ') + '…';

  // universe (breakout only)
  const universeField = $('universe-field');
  const universeSel = $('universe');
  if (cfg.supports_universe) {
    universeField.hidden = false;
    universeSel.innerHTML = '<option value="">— use tickers below —</option>' +
      cfg.universe_options.map(u => `<option value="${u}">${u}</option>`).join('');
    universeSel.value = '';
  } else {
    universeField.hidden = true;
    universeSel.value = '';
  }
  $('universe-limit-field').hidden = true;
  $('tickers-field').hidden = false;

  // entry mode (breakout only)
  $('entry-mode-field').hidden = !cfg.entry_modes;

  // dynamic numeric/text fields
  const container = $('dynamic-fields');
  container.innerHTML = cfg.fields.map(f => `
    <label class="field" data-fname="${f.name}">
      <span>${f.label}</span>
      <input id="dyn_${f.name}" type="${f.type}" ${f.step ? `step="${f.step}"` : ''} value="${f.default}">
    </label>
  `).join('');
  container.querySelectorAll('input').forEach(el => el.addEventListener('input', updateCmdPreview));

  updateCmdPreview();
}

function collectParams() {
  const key = $('strategy').value;
  const cfg = SCRIPTS[key];
  const params = {
    script: key,
    provider: $('provider').value,
    tickers: $('tickers').value,
    backtest: $('backtest').checked,
    start: $('start').value,
    end: $('end').value,
  };
  if (cfg.supports_universe) {
    params.universe = $('universe').value;
    params.universe_limit = $('universe_limit').value;
  }
  if (cfg.entry_modes) {
    params.entry_mode = $('entry_mode').value;
  }
  cfg.fields.forEach(f => {
    const el = $('dyn_' + f.name);
    if (el) params[f.name] = el.value;
  });
  return params;
}

function updateCmdPreview() {
  const p = collectParams();
  const cfg = SCRIPTS[p.script];
  let parts = ['python', cfg.file];
  if (cfg.supports_universe && p.universe) {
    parts.push('--universe', p.universe, '--universe-limit', p.universe_limit || '100');
  } else {
    parts.push('--tickers', (p.tickers || cfg.default_watchlist.join(' ')));
  }
  if (p.provider) parts.push(cfg.provider_flag, p.provider);
  if (p.entry_mode) parts.push('--entry-mode', p.entry_mode);
  cfg.fields.forEach(f => {
    if (f.name === 'universe_limit') return;
    if (p[f.name] !== undefined && p[f.name] !== '') parts.push(f.flag, p[f.name]);
  });
  if (p.backtest) {
    parts.push('--backtest', '--start', p.start || '2023-01-01');
    if (p.end) parts.push('--end', p.end);
  }
  parts.push('--csv', 'outputs/<auto-named>.csv');
  $('cmd-preview').textContent = parts.join(' ');
}

async function runStrategy() {
  const params = collectParams();
  $('run-btn').disabled = true;
  $('cancel-btn').hidden = false;
  setBadge('running');
  $('log-box').textContent = '';
  $('results-table').innerHTML = '<p class="muted">Running…</p>';
  $('results-meta').textContent = '';

  const res = await fetch('/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  const data = await res.json();
  if (data.error) {
    setBadge('error');
    $('log-box').textContent = 'Error: ' + data.error;
    $('run-btn').disabled = false;
    $('cancel-btn').hidden = true;
    return;
  }
  currentJob = data.job_id;
  $('cmd-preview').textContent = data.cmd.join(' ');
  pollJob(0);
}

function pollJob(since) {
  clearTimeout(pollTimer);
  fetch(`/api/jobs/${currentJob}?since=${since}`)
    .then(r => r.json())
    .then(data => {
      if (data.log && data.log.length) {
        const box = $('log-box');
        box.textContent += data.log.join('\n') + '\n';
        box.scrollTop = box.scrollHeight;
      }
      if (data.status === 'running' || data.status === 'queued') {
        pollTimer = setTimeout(() => pollJob(data.total), 1200);
      } else {
        setBadge(data.status);
        $('run-btn').disabled = false;
        $('cancel-btn').hidden = true;
        renderResults(data);
        loadHistory();
      }
    })
    .catch(() => {
      pollTimer = setTimeout(() => pollJob(since), 2000);
    });
}

function cancelJob() {
  if (!currentJob) return;
  fetch(`/api/jobs/${currentJob}/cancel`, { method: 'POST' });
}

function setBadge(status) {
  const badge = $('status-badge');
  badge.hidden = false;
  badge.className = 'badge ' + status;
  badge.textContent = status;
}

function renderResults(data) {
  if (data.provider) lastProviderUsed = data.provider;
  $('results-meta').textContent = data.csv
    ? `Saved to ${data.csv} (return code ${data.returncode})`
    : `Return code ${data.returncode} — no CSV produced`;
  renderTable(data.table);
}

// Known "how close to actionable" signals across the three screeners.
const STATUS_COLUMNS = ['STATUS', 'SIGNAL'];
const STATUS_RANK = { 'BROKEN OUT': 3, 'LONG': 3, 'BUY/HOLD': 3, 'SETUP (watch)': 2 };
const DISTANCE_COLUMNS = ['%_below_resistance', 'pct_below_resistance'];

function rankRows(table) {
  const boolCols = table.columns.filter(c => table.rows.every(r => typeof r[c] === 'boolean'));
  const statusCol = table.columns.find(c => STATUS_COLUMNS.includes(c));
  const distCol = table.columns.find(c => DISTANCE_COLUMNS.includes(c));
  if (!statusCol && !boolCols.length && !distCol) return null; // nothing to rank on — render as-is

  const scored = table.rows.map((row, idx) => {
    let score = 0;
    if (statusCol) score += (STATUS_RANK[row[statusCol]] || 0) * 1000;
    score += boolCols.reduce((s, c) => s + (row[c] === true ? 1 : 0), 0) * 10;
    if (distCol && typeof row[distCol] === 'number') score -= row[distCol] / 10;
    return { row, idx, score };
  });
  scored.sort((a, b) => b.score - a.score || a.idx - b.idx);

  const n = scored.length;
  const highCutoff = Math.max(3, Math.ceil(n * 0.1));
  const medCutoff = Math.max(highCutoff, Math.ceil(n * 0.35));
  return scored.map((s, i) => ({
    row: s.row,
    rank: i + 1,
    tier: i < highCutoff ? 'high' : (i < medCutoff ? 'med' : 'low'),
  }));
}

function renderTable(table) {
  const el = $('results-table');
  if (!table || table.error) {
    el.innerHTML = `<p class="muted">${table && table.error ? 'Could not read CSV: ' + table.error : 'No results table.'}</p>`;
    return;
  }
  if (!table.rows.length) {
    el.innerHTML = '<p class="muted">No rows returned.</p>';
    return;
  }

  const ranked = rankRows(table);
  const tickerCol = table.columns.find(c => c.toLowerCase() === 'ticker');
  const head = (ranked ? '<th>#</th>' : '') + table.columns.map(c => `<th>${c}</th>`).join('');

  const cellHtml = (row, c) => {
    const val = row[c] ?? '';
    if (c === tickerCol && val) {
      return `<td class="ticker-cell" data-ticker="${val}" title="Click to open chart">${val}</td>`;
    }
    return `<td>${val}</td>`;
  };

  let rows;
  if (ranked) {
    rows = ranked.map(({ row, rank, tier }) =>
      `<tr class="tier-${tier}"><td class="rank-cell">${rank}</td>` +
      table.columns.map(c => cellHtml(row, c)).join('') + '</tr>'
    ).join('');
  } else {
    rows = table.rows.map(r =>
      '<tr>' + table.columns.map(c => cellHtml(r, c)).join('') + '</tr>'
    ).join('');
  }

  const legend = ranked ? `
    <div class="rank-legend">
      <span><i class="dot dot-high"></i>Monitor first</span>
      <span><i class="dot dot-med"></i>Watch next</span>
      <span><i class="dot dot-low"></i>Lower priority</span>
    </div>` : '';

  el.innerHTML = `${legend}<table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table>`;

  if (tickerCol) {
    el.querySelectorAll('td.ticker-cell').forEach(td => {
      td.addEventListener('click', () => openChart(td.dataset.ticker));
    });
  }
}

async function loadHistory() {
  const res = await fetch('/api/history');
  const items = await res.json();
  const el = $('history-list');
  if (!items.length) {
    el.innerHTML = '<p class="muted">No runs yet.</p>';
    return;
  }
  el.innerHTML = items.map(it => `
    <div class="history-row" data-id="${it.id}">
      <div class="hr-main">
        <div class="hr-title">${it.label} — ${it.mode}</div>
        <div class="hr-sub">${it.provider || 'default'} · ${it.tickers} · ${it.started}</div>
      </div>
      <span class="badge ${it.status}">${it.status}</span>
    </div>
  `).join('');
  el.querySelectorAll('.history-row').forEach(row => {
    row.addEventListener('click', () => loadHistoryItem(row.dataset.id));
  });
}

async function loadHistoryItem(id) {
  const res = await fetch(`/api/history/${id}/data`);
  const data = await res.json();
  if (data.error) return;
  if (data.provider) lastProviderUsed = data.provider;
  $('log-box').textContent = (data.full_log || []).join('\n');
  $('results-meta').textContent = data.csv
    ? `Loaded from history: ${data.csv} (return code ${data.returncode})`
    : `Return code ${data.returncode} — no CSV produced`;
  renderTable(data.table);
  setBadge(data.status);
}

// ==================== Chart modal (candles + EMA/BB/Fib/SL-TP + 1-month projection) ====================

const PROJECTION_COLOR = '#e8b23e';

function openChart(ticker) {
  $('chart-modal').hidden = false;
  $('chart-provider').value = lastProviderUsed || 'yahoo';
  loadChart(ticker, $('chart-provider').value);
}

function closeChart() {
  $('chart-modal').hidden = true;
  if (chartObj && chartObj.chart) {
    chartObj.chart.remove();
  }
  chartObj = null;
}

async function loadChart(ticker, provider) {
  $('chart-title').textContent = ticker;
  $('chart-subtitle').textContent = `Loading ${provider} data…`;
  $('chart-legend').innerHTML = '';
  $('chart-meta').innerHTML = '';
  $('verdict-box').hidden = true;
  const container = $('chart-container');
  container.innerHTML = '';
  const status = $('chart-status');
  status.hidden = false;
  status.className = 'chart-status';
  status.textContent = 'Loading chart…';

  if (chartObj && chartObj.chart) {
    chartObj.chart.remove();
    chartObj = null;
  }

  try {
    const res = await fetch(`/api/chart/${encodeURIComponent(ticker)}?provider=${encodeURIComponent(provider)}`);
    const data = await res.json();
    if (data.error) {
      status.className = 'chart-status error';
      status.textContent = `Could not load chart: ${data.error}`;
      return;
    }
    if (typeof LightweightCharts === 'undefined') {
      throw new Error('Charting library failed to load (static/vendor/lightweight-charts.standalone.production.js missing or blocked).');
    }
    renderChart(ticker, provider, data);
    status.hidden = true;
  } catch (e) {
    status.hidden = false;
    status.className = 'chart-status error';
    status.textContent = `Could not load chart: ${e.message || e}`;
  }
}

function renderChart(ticker, provider, data) {
  const container = $('chart-container');
  const chart = LightweightCharts.createChart(container, {
    layout: { background: { color: '#0b0f19' }, textColor: '#b9c4d8' },
    grid: { vertLines: { color: '#1b2438' }, horzLines: { color: '#1b2438' } },
    rightPriceScale: { borderColor: '#2a3550' },
    timeScale: { borderColor: '#2a3550', timeVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    width: container.clientWidth,
    height: container.clientHeight,
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: '#22c58b', downColor: '#ef5a6f', borderVisible: false,
    wickUpColor: '#22c58b', wickDownColor: '#ef5a6f',
  });
  candleSeries.setData(data.candles);

  const volSeries = chart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: 'vol',
    color: '#2a3550',
  });
  chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
  volSeries.setData(data.candles.map(c => ({
    time: c.time, value: c.volume,
    color: c.close >= c.open ? 'rgba(34,197,139,0.5)' : 'rgba(239,90,111,0.5)',
  })));

  const ema20 = chart.addLineSeries({ color: '#4f8cff', lineWidth: 2, title: 'EMA20' });
  ema20.setData(data.ema20);
  const ema50 = chart.addLineSeries({ color: '#e8b23e', lineWidth: 2, title: 'EMA50' });
  ema50.setData(data.ema50);
  const bbUpper = chart.addLineSeries({ color: 'rgba(190,160,255,0.45)', lineWidth: 1, title: 'BB Upper' });
  bbUpper.setData(data.bb_upper);
  const bbLower = chart.addLineSeries({ color: 'rgba(190,160,255,0.45)', lineWidth: 1, title: 'BB Lower' });
  bbLower.setData(data.bb_lower);

  // Fibonacci retracement — only the 3 most-watched levels, to keep the chart readable
  const KEY_FIB_LEVELS = [0.382, 0.5, 0.618];
  const fibColors = { 0.382: '#8aa0d6', 0.5: '#e8b23e', 0.618: '#8aa0d6' };
  data.fib_levels.filter(f => KEY_FIB_LEVELS.includes(f.level)).forEach(f => {
    candleSeries.createPriceLine({
      price: f.price,
      color: fibColors[f.level] || '#5f6a80',
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted,
      axisLabelVisible: true,
      title: `fib ${Math.round(f.level * 100)}%`,
    });
  });

  // Current price — solid, brightest line on the chart, drawn last so it stands out
  candleSeries.createPriceLine({
    price: data.last_close, color: '#e6ebf5', lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Solid, title: 'Current',
  });
  candleSeries.createPriceLine({
    price: data.sl, color: '#ef5a6f', lineWidth: 2,
    lineStyle: LightweightCharts.LineStyle.Dashed, title: 'SL',
  });
  candleSeries.createPriceLine({
    price: data.tp, color: '#22c58b', lineWidth: 2,
    lineStyle: LightweightCharts.LineStyle.Dashed, title: 'TP',
  });
  if (data.resistance) {
    candleSeries.createPriceLine({
      price: data.resistance, color: '#e8b23e', lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed, title: 'Resistance',
    });
  }

  // 1-month-ahead projection cone, anchored to the last real close so it connects visually
  const lastCandle = data.candles[data.candles.length - 1];
  const anchor = { time: lastCandle.time, value: lastCandle.close };
  const projMid = chart.addLineSeries({ color: PROJECTION_COLOR, lineWidth: 2, lineStyle: LightweightCharts.LineStyle.Dashed, title: 'Projected' });
  const projUpper = chart.addLineSeries({ color: 'rgba(232,178,62,0.5)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted });
  const projLower = chart.addLineSeries({ color: 'rgba(232,178,62,0.5)', lineWidth: 1, lineStyle: LightweightCharts.LineStyle.Dotted });
  projMid.setData([anchor, ...data.projection.map(p => ({ time: p.time, value: p.mid }))]);
  projUpper.setData([anchor, ...data.projection.map(p => ({ time: p.time, value: p.upper }))]);
  projLower.setData([anchor, ...data.projection.map(p => ({ time: p.time, value: p.lower }))]);

  chart.timeScale().fitContent();
  chartObj = { chart, ticker };

  const resizeObserver = new ResizeObserver(() => {
    chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
  });
  resizeObserver.observe(container);

  // legend + meta panels
  const proj1m = data.projection[data.projection.length - 1];
  const upPct = ((proj1m.upper / data.last_close - 1) * 100).toFixed(1);
  const downPct = ((proj1m.lower / data.last_close - 1) * 100).toFixed(1);

  $('chart-title').textContent = `${ticker} · $${data.last_close}`;
  $('chart-subtitle').textContent = `${provider} · 1-month projected range ${downPct}% / +${upPct}%`;

  $('chart-legend').innerHTML = `
    <span class="lg-item"><i class="sw" style="background:#4f8cff"></i>EMA20</span>
    <span class="lg-item"><i class="sw" style="background:#e8b23e"></i>EMA50</span>
    <span class="lg-item"><i class="sw" style="background:rgba(190,160,255,0.7)"></i>Bollinger (20, 2σ)</span>
    <span class="lg-item"><i class="sw" style="background:#8aa0d6"></i>Fib 38/50/62%</span>
    <span class="lg-item"><i class="sw" style="background:${PROJECTION_COLOR};border-style:dashed"></i>1-month projection</span>
    <span class="pill pill-sl">SL ${data.sl}</span>
    <span class="pill pill-tp">TP ${data.tp}</span>
    ${data.resistance ? `<span class="pill pill-res">Resistance ${data.resistance}</span>` : ''}
  `;

  $('chart-meta').innerHTML = `
    <div class="meta-box"><div class="meta-label">Suggested Stop-Loss</div><div class="meta-value" style="color:var(--danger)">${data.sl}</div></div>
    <div class="meta-box"><div class="meta-label">Suggested Take-Profit</div><div class="meta-value" style="color:var(--accent-2)">${data.tp} (2:1 R:R)</div></div>
    <div class="meta-box"><div class="meta-label">ATR(14)</div><div class="meta-value">${data.atr}</div></div>
  `;

  renderVerdict(data);
}

function renderVerdict(data) {
  const v = data.verdict;
  const box = $('verdict-box');
  if (!v) { box.hidden = true; return; }
  box.hidden = false;
  const badge = $('verdict-badge');
  badge.textContent = v.action;
  badge.className = 'verdict-badge ' + v.action;
  $('verdict-price').textContent = data.last_close;
  $('verdict-notes').innerHTML = v.notes.map(n => `<li>${n}</li>`).join('');
}

init();
