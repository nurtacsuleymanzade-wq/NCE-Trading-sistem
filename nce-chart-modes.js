// NCE Chart Modes: Şam / Ask-Bid footprint / Delta / Volume overlays
// Data: Binance USD-M futures aggTrades (REST backfill + WS live). m=true => buyer is maker => SELL aggressor (bid).
(function(){
  'use strict';
  var REST = 'https://fapi.binance.com/fapi/v1/aggTrades';
  var WS = 'wss://fstream.binance.com/ws/btcusdt@aggTrade';
  var BASE_STEP = 1;             // trades stored on $1 rows, re-binned on draw
  var BACKFILL_BARS = 40, MAX_PAGES = 4, CONC = 3;
  var TF_SEC = {'1s':1,'1m':60,'5m':300,'15m':900,'30m':1800,'1h':3600,'4h':14400,'1D':86400,'3D':259200};
  var fp = {};                   // barTime -> {rows:{price:{b,a}}, b, a, n, partial}
  var fpTF = null, ws = null, canvas = null, ctx = null, dirty = true, pending = {}, queue = [], active = 0, hooked = false;
  window.chartMode = 'candle';
  window.NCE_FOOTPRINT = fp;

  function C(){ return window.chart; }
  function S(){ return window.cs; }
  function tf(){ return window.curTF || '1m'; }
  function bars(){ return (window.curData || []).filter(function(x){ return x && x.t; }); }
  function tfSec(){ return TF_SEC[tf()] || 60; }

  window.checkAbsorption = function(bar) {
    if (!bar || !bar.v) return null;
    var range = bar.h - bar.l, avgRange = bar.atr || range;
    var imbalance = Math.abs((bar.bv||0) - (bar.sv||0)) / (bar.v || 1);
    if (bar.v > (bar.avgVol || bar.v) * 1.8 && range < avgRange * 0.6) return imbalance > 0.6 ? 'possible trapped / absorption' : 'high volume absorption';
    return null;
  };

  // ---------- data ----------
  function addTrade(ms, price, qty, sellAggr){
    var t = Math.floor(ms / 1000 / tfSec()) * tfSec();
    var b = fp[t] || (fp[t] = {rows:{}, b:0, a:0, n:0});
    var k = Math.floor(price / BASE_STEP) * BASE_STEP;
    var r = b.rows[k] || (b.rows[k] = {b:0, a:0});
    if (sellAggr) { r.b += qty; b.b += qty; } else { r.a += qty; b.a += qty; }
    b.n++; dirty = true;
  }
  function resetIfTFChanged(){
    if (fpTF === tf()) return;
    fpTF = tf(); Object.keys(fp).forEach(function(k){ delete fp[k]; }); pending = {}; queue = []; dirty = true;
  }
  function fetchBar(t){
    var start = t * 1000, end = start + tfSec() * 1000 - 1, page = 0, fromId = null;
    if (fp[t]) { fp[t] = {rows:{}, b:0, a:0, n:0}; }
    function next(){
      var url = REST + '?symbol=BTCUSDT&limit=1000' + (fromId != null ? '&fromId=' + fromId : '&startTime=' + start + '&endTime=' + Math.min(end, start + 3599999));
      return fetch(url).then(function(r){ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }).then(function(arr){
        if (fpTF !== tf()) return;
        var done = false;
        arr.forEach(function(d){ if (d.T > end) { done = true; return; } if (d.T >= start) addTrade(d.T, +d.p, +d.q, !!d.m); });
        page++;
        if (fp[t]) fp[t].partial = !done && arr.length === 1000;
        if (!done && arr.length === 1000 && page < MAX_PAGES) { fromId = arr[arr.length - 1].a + 1; return next(); }
      });
    }
    return next();
  }
  function pump(){
    while (active < CONC && queue.length) {
      var t = queue.shift(); active++;
      fetchBar(t).catch(function(e){ console.warn('[NCE footprint] backfill failed', e && e.message || e); }).then(function(){ active--; dirty = true; pump(); });
    }
  }
  function backfill(){
    var c = C(); if (!c) return;
    var list = bars(); if (!list.length) return;
    var lr = c.timeScale().getVisibleLogicalRange();
    var hi = lr ? Math.min(list.length - 1, Math.ceil(lr.to)) : list.length - 1;
    var lo = Math.max(0, hi - BACKFILL_BARS + 1, lr ? Math.floor(lr.from) : 0);
    var nowBar = Math.floor(Date.now() / 1000 / tfSec()) * tfSec();
    for (var i = hi; i >= lo; i--) { var t = list[i].t; if (pending[t]) continue; pending[t] = 1; if (t >= nowBar && fp[t]) continue; queue.push(t); }
    pump();
  }
  function openWS(){
    if (ws) return;
    try { ws = new WebSocket(WS); } catch(e) { return; }
    ws.onmessage = function(e){ try { var d = JSON.parse(e.data); d = d.data || d; addTrade(+d.T, +d.p, +d.q, !!d.m); } catch(_){} };
    ws.onclose = function(){ ws = null; if (window.chartMode !== 'candle') setTimeout(openWS, 3000); };
  }
  function closeWS(){ if (ws) { var w = ws; ws = null; w.onclose = null; try { w.close(); } catch(_){} } }

  // ---------- drawing ----------
  function ensureCanvas(){
    var host = document.getElementById('ct'); if (!host) return false;
    if (!canvas) {
      canvas = document.createElement('canvas'); canvas.id = 'nce-mode-overlay';
      canvas.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;z-index:4;';
      if (getComputedStyle(host).position === 'static') host.style.position = 'relative';
      host.appendChild(canvas); ctx = canvas.getContext('2d');
    }
    var w = host.clientWidth, h = host.clientHeight, dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) { canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr); canvas.style.width = w + 'px'; canvas.style.height = h + 'px'; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return true;
  }
  function plotW(){ try { return C().timeScale().width(); } catch(_) { return canvas.clientWidth - 96; } }
  function plotH(){ try { return C().paneSize ? C().paneSize().height : canvas.clientHeight - 28; } catch(_) { return canvas.clientHeight - 28; } }
  function nice(x){ var s=[0.1,0.5,1,2,5,10,20,25,50,100,250,500,1000,2500,5000]; for (var i=0;i<s.length;i++) if (s[i]>=x) return s[i]; return 10000; }
  function fmt(q){ return q >= 1000 ? (q/1000).toFixed(1)+'k' : q >= 100 ? q.toFixed(0) : q >= 10 ? q.toFixed(1) : q.toFixed(2); }
  function visible(){
    var c = C(), s = S(), list = bars(), ts = c.timeScale(), W = plotW(), out = [];
    list.forEach(function(b, i){ var x = ts.timeToCoordinate(b.t); if (x == null || x < -50 || x > W + 50) return; out.push({b:b, i:i, x:x}); });
    return out;
  }
  function binned(f, step){
    var m = {}; Object.keys(f.rows).forEach(function(k){ var p = Math.floor(+k / step) * step, r = f.rows[k]; var z = m[p] || (m[p] = {b:0, a:0}); z.b += r.b; z.a += r.a; }); return m;
  }
  function label(x, y, txt, col){ ctx.font = 'bold 10px sans-serif'; var w = ctx.measureText(txt).width + 6; ctx.fillStyle = col; ctx.fillRect(x - w/2, y - 7, w, 14); ctx.fillStyle = '#0d1117'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(txt, x, y); }

  function drawFootprint(vis){
    var s = S(), W = plotW(), H = plotH(), sp = C().timeScale().options().barSpacing || 6;
    var hiP = s.coordinateToPrice(0), loP = s.coordinateToPrice(H); if (hiP == null || loP == null) return;
    var pxPerUsd = H / Math.max(1e-9, hiP - loP);
    var step = Math.max(BASE_STEP, nice((sp >= 60 ? 13 : 4) / pxPerUsd));
    var cellW = Math.max(2, sp * 0.9), showText = sp >= 56 && step * pxPerUsd >= 11, have = 0;
    var avgV = 0; vis.forEach(function(v){ avgV += (v.b.v || 0); }); avgV /= Math.max(1, vis.length);
    vis.forEach(function(v){
      var f = fp[v.b.t]; if (!f || !f.n) return; have++;
      var m = binned(f, step), keys = Object.keys(m).map(Number).sort(function(a,b){ return a-b; });
      var maxV = 0, poc = null; keys.forEach(function(p){ var t = m[p].a + m[p].b; if (t > maxV) { maxV = t; poc = p; } });
      var x0 = v.x - cellW / 2;
      keys.forEach(function(p){
        var r = m[p], y1 = s.priceToCoordinate(p + step), y0 = s.priceToCoordinate(p); if (y1 == null || y0 == null) return;
        var h = Math.max(1, y0 - y1), below = m[p - step], above = m[p + step];
        var buyImb = r.a > 0 && (below ? r.a >= 3 * Math.max(below.b, 1e-9) : r.a >= 2 * Math.max(r.b, 1e-9)) && r.a > 0.05 * maxV;
        var sellImb = r.b > 0 && (above ? r.b >= 3 * Math.max(above.a, 1e-9) : r.b >= 2 * Math.max(r.a, 1e-9)) && r.b > 0.05 * maxV;
        var inten = 0.15 + 0.6 * (r.a + r.b) / (maxV || 1);
        ctx.fillStyle = buyImb ? 'rgba(63,185,80,' + Math.max(.55, inten) + ')' : sellImb ? 'rgba(248,81,73,' + Math.max(.55, inten) + ')' : (r.a >= r.b ? 'rgba(38,166,154,' + inten * .6 + ')' : 'rgba(239,83,80,' + inten * .6 + ')');
        ctx.fillRect(x0, y1, cellW, h - (h > 3 ? 1 : 0));
        if (p === poc) { ctx.strokeStyle = '#f2cc60'; ctx.lineWidth = 1.5; ctx.strokeRect(x0 + .5, y1 + .5, cellW - 1, h - 1); }
        if (showText) { ctx.font = Math.min(11, h - 2) + 'px monospace'; ctx.textBaseline = 'middle'; ctx.fillStyle = '#e6edf3'; ctx.textAlign = 'right'; ctx.fillText(fmt(r.b), v.x - 3, y1 + h/2); ctx.textAlign = 'left'; ctx.fillText('x ' + fmt(r.a), v.x - 1, y1 + h/2); }
      });
      // absorption / trapped at bar extremes: heavy aggressor volume at the extreme, but price closed away (no continuation)
      var top = keys[keys.length-1], bot = keys[0], n = Math.max(1, Math.ceil(keys.length * 0.2)), tv = f.a + f.b;
      var topA = 0, topB = 0, botA = 0, botB = 0; keys.slice(-n).forEach(function(p){ topA += m[p].a; topB += m[p].b; }); keys.slice(0, n).forEach(function(p){ botA += m[p].a; botB += m[p].b; });
      var rng = (v.b.h - v.b.l) || 1, closeLoc = (v.b.c - v.b.l) / rng, xa = v.x;
      if (tv > 0 && (topA + topB) / tv > 0.3 && closeLoc < 0.4) label(xa, (s.priceToCoordinate(v.b.h) || 0) - 10, topA > topB ? 'Trapped buyers' : 'Absorb ↑', '#f85149');
      if (tv > 0 && (botA + botB) / tv > 0.3 && closeLoc > 0.6) label(xa, (s.priceToCoordinate(v.b.l) || 0) + 10, botB > botA ? 'Trapped sellers' : 'Absorb ↓', '#3fb950');
      var ab = window.checkAbsorption({h:v.b.h, l:v.b.l, v:tv, bv:f.a, sv:f.b, avgVol: avgV ? avgV / (v.b.c || 1) : tv, atr: null});
      if (ab && tv > 0) label(xa, (s.priceToCoordinate(v.b.h) || 0) - 24, 'ABS', '#d29922');
    });
    status(have ? ('Ask-Bid footprint · row $' + step + ' · ' + have + ' bar' + (sp < 56 ? ' · rəqəmlər üçün zoom in' : '')) : 'Ask-Bid: aggTrade data yüklənir… (Binance fapi)');
  }

  function barFlow(b){ var f = fp[b.t]; if (f && f.n) return {buy: f.a * (b.c || 1), sell: f.b * (b.c || 1), src:'fp'}; var bv = +b.bv || 0, sv = +b.sv || 0; if (!bv && !sv && b.delta != null) { var v = +b.v || 0; bv = (v + b.delta) / 2; sv = (v - b.delta) / 2; } return {buy: bv, sell: sv, src:'bar'}; }

  function drawBottom(vis, kind){
    var H = plotH(), W = plotW(), sp = C().timeScale().options().barSpacing || 6, pane = Math.max(60, H * 0.22), base = H - 4, w = Math.max(1, sp * 0.7);
    ctx.fillStyle = 'rgba(13,17,23,.82)'; ctx.fillRect(0, H - pane - 8, W, pane + 8);
    var vals = vis.map(function(v){ return barFlow(v.b); });
    if (kind === 'delta') {
      var mx = 0; vals.forEach(function(f){ mx = Math.max(mx, Math.abs(f.buy - f.sell)); }); mx = mx || 1;
      var mid = H - pane / 2 - 4; ctx.strokeStyle = '#30363d'; ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(W, mid); ctx.stroke();
      var cvd = 0, pts = [], cmin = 0, cmax = 0;
      vis.forEach(function(v, i){ var d = vals[i].buy - vals[i].sell, h = d / mx * (pane / 2 - 4); ctx.fillStyle = d >= 0 ? '#3fb950' : '#f85149'; ctx.fillRect(v.x - w/2, h >= 0 ? mid - h : mid, w, Math.abs(h)); cvd += d; pts.push([v.x, cvd]); cmin = Math.min(cmin, cvd); cmax = Math.max(cmax, cvd); });
      ctx.strokeStyle = '#58a6ff'; ctx.lineWidth = 1.5; ctx.beginPath(); pts.forEach(function(p, i){ var y = (H - pane - 4) + (1 - (p[1] - cmin) / ((cmax - cmin) || 1)) * (pane - 4); i ? ctx.lineTo(p[0], y) : ctx.moveTo(p[0], y); }); ctx.stroke();
      ctx.fillStyle = '#8b949e'; ctx.font = '10px sans-serif'; ctx.textAlign = 'left'; ctx.fillText('Delta (ask−bid) · CVD (mavi)', 6, H - pane);
      status('Delta: bar başına alış − satış aqressiv həcm, CVD xətti');
    } else {
      var mv = 0; vals.forEach(function(f){ mv = Math.max(mv, f.buy + f.sell); }); mv = mv || 1;
      vis.forEach(function(v, i){ var hb = vals[i].buy / mv * (pane - 6), hs = vals[i].sell / mv * (pane - 6); ctx.fillStyle = '#f85149'; ctx.fillRect(v.x - w/2, base - hs, w, hs); ctx.fillStyle = '#3fb950'; ctx.fillRect(v.x - w/2, base - hs - hb, w, hb); });
      ctx.fillStyle = '#8b949e'; ctx.font = '10px sans-serif'; ctx.textAlign = 'left'; ctx.fillText('Volume: alış (yaşıl) / satış (qırmızı)', 6, H - pane);
      drawProfile(vis); status('Volume: alış/satış split + sağda visible-range volume profile');
    }
  }
  function drawProfile(vis){
    var s = S(), H = plotH(), W = plotW(), rows = {}, step = null;
    var hiP = s.coordinateToPrice(0), loP = s.coordinateToPrice(H * 0.75); if (hiP == null || loP == null) return;
    step = nice((hiP - loP) / 60);
    vis.forEach(function(v){
      var f = fp[v.b.t];
      if (f && f.n) { var m = binned(f, step); Object.keys(m).forEach(function(p){ var r = rows[p] || (rows[p] = {a:0, b:0}); r.a += m[p].a * v.b.c; r.b += m[p].b * v.b.c; }); }
      else { var fl = barFlow(v.b), lo = Math.floor(v.b.l / step) * step, n = Math.max(1, Math.round((v.b.h - lo) / step) + 1); for (var p = lo, k = 0; k < n; k++, p += step) { var key = +p.toFixed(2), r = rows[key] || (rows[key] = {a:0, b:0}); r.a += fl.buy / n; r.b += fl.sell / n; } }
    });
    var mx = 0, poc = null; Object.keys(rows).forEach(function(p){ var t = rows[p].a + rows[p].b; if (t > mx) { mx = t; poc = p; } });
    var maxW = W * 0.18;
    Object.keys(rows).forEach(function(p){
      var y1 = s.priceToCoordinate(+p + step), y0 = s.priceToCoordinate(+p); if (y1 == null || y0 == null || y0 > H * 0.76) return;
      var r = rows[p], wa = r.a / mx * maxW, wb = r.b / mx * maxW, h = Math.max(1, y0 - y1 - 1);
      ctx.fillStyle = 'rgba(248,81,73,.45)'; ctx.fillRect(W - wb, y1, wb, h); ctx.fillStyle = 'rgba(63,185,80,.45)'; ctx.fillRect(W - wb - wa, y1, wa, h);
      if (p === poc) { ctx.strokeStyle = '#f2cc60'; ctx.strokeRect(W - wa - wb, y1, wa + wb, h); }
    });
  }
  function status(t){ var el = document.getElementById('nce-mode-status'); if (!el) { var bar = document.getElementById('chart-modes'); if (!bar) return; el = document.createElement('span'); el.id = 'nce-mode-status'; el.style.cssText = 'color:#8b949e;font-size:10px;align-self:center;padding:0 4px;max-width:260px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'; bar.appendChild(el); } el.textContent = t || ''; }

  function draw(){
    if (!C() || !S() || !ensureCanvas()) return;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    var mode = window.chartMode; if (mode === 'candle') { status(''); return; }
    resetIfTFChanged();
    var vis = visible(); if (!vis.length) return;
    if (mode === 'askbid') drawFootprint(vis); else drawBottom(vis, mode);
  }
  window.nceModesRedraw = function(){ dirty = true; };

  function hook(){
    if (hooked || !C()) return; hooked = true;
    C().timeScale().subscribeVisibleLogicalRangeChange(function(){ dirty = true; if (window.chartMode !== 'candle') { clearTimeout(hook.t); hook.t = setTimeout(backfill, 400); } });
    if (window.ResizeObserver) new ResizeObserver(function(){ dirty = true; }).observe(document.getElementById('ct'));
    window.addEventListener('resize', function(){ dirty = true; });
    var ct = document.getElementById('ct'); ['wheel','pointermove','pointerup'].forEach(function(ev){ ct.addEventListener(ev, function(){ dirty = true; }, {passive:true}); });
  }
  var lastLen = 0, lastTF = null;
  setInterval(function(){
    if (!hooked) hook();
    if (window.chartMode === 'candle') { if (dirty) { dirty = false; draw(); } return; }
    var L = bars().length; if (L !== lastLen || tf() !== lastTF) { lastLen = L; if (tf() !== lastTF) { lastTF = tf(); resetIfTFChanged(); backfill(); } dirty = true; }
    dirty = true; draw(); dirty = false; // redraw continuously (~4/s) so vertical price-scale zoom also stays aligned
  }, 250);

  window.applyChartMode = function(mode) {
    window.chartMode = mode;
    document.querySelectorAll('.mode-btn').forEach(function(b){ var on = b.dataset.mode === mode; b.classList.toggle('on', on); b.style.background = on ? '#1f6feb' : '#161b22'; b.style.color = on ? '#fff' : '#c9d1d9'; });
    try { localStorage.setItem('nceChartMode', mode); } catch(_){}
    if (mode === 'candle') closeWS(); else { resetIfTFChanged(); openWS(); backfill(); }
    dirty = true; draw();
  };
  document.addEventListener('click', function(e){ var btn = e.target.closest && e.target.closest('.mode-btn'); if (btn) { e.preventDefault(); e.stopPropagation(); window.applyChartMode(btn.dataset.mode); } }, true);
  var cm = document.getElementById('chart-modes'); if (cm) { cm.style.zIndex = '30'; cm.style.pointerEvents = 'auto'; }
  setTimeout(function(){ var m = null; try { m = localStorage.getItem('nceChartMode'); } catch(_){} if (m && m !== 'candle') window.applyChartMode(m); }, 1500);
})();
