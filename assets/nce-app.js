/* NCE Trading System — frontend (bir nüsxə kod). Məlumat: nce-core API (VPS, HTTPS).
 * Uydurma rəqəm yoxdur: backend əlçatmazdırsa "offline" göstərilir, köhnə məlumat "köhnədir" işarəsi ilə.
 * Bu, maliyyə məsləhəti deyil. */
(function () {
  'use strict';
  var QS = new URLSearchParams(location.search);
  var API = (QS.get('api') || 'https://151.115.77.234.sslip.io/nce-api').replace(/\/$/, '');
  var TFS = ['1s', '1m', '3m', '5m', '15m', '1h', '4h', '1D', '3D'];
  var PROFS = ['1h', '4h', '1D', '3D'];
  var EVAL = { '1s': '1m', '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '1h': '1h', '4h': '4h', '1D': '4h', '3D': '4h' };
  var TABS = [
    ['konsensus', 'Yekdil qərar'], ['tf', 'TF matrisi'], ['vp', 'Volume Profile'], ['ict', 'ICT strukturu'],
    ['flow', 'Capital Flow'], ['liq', 'Likvidasiya xəritəsi'], ['ssenari', 'Ssenarilər'], ['stat', 'Jurnal statistikası'], ['haqqinda', 'Metod']
  ];
  // köhnə URL-lərlə uyğunluq (?panel=Likidite+ve+Liquidation+Haritası və s.)
  var PANEL_ALIAS = { 'likidite ve liquidation haritası': 'liq', 'likvidasiya': 'liq', 'liquidation': 'liq', 'capital flow': 'flow',
    'volume profile': 'vp', 'ict': 'ict', 'konsensus': 'konsensus' };
  var TREND = { bullish: ['yüksələn', 'gn'], bearish: ['düşən', 'rd'], range: ['yatay', 'yl'] };
  var LEVEL_AZ = { POC: 'POC', VAH: 'VAH', VAL: 'VAL', HVN: 'HVN', LVN: 'LVN', nPOC: 'naked POC', EQH: 'EQH', EQL: 'EQL',
    FVG_mid: 'FVG ortası', OB_mid: 'OB ortası' };

  var S = { tf: TFS.indexOf(QS.get('tf')) >= 0 ? QS.get('tf') : '1h', prof: '1h', tab: 'konsensus', data: null, stats: null,
            err: null, lastOk: 0, chart: null, cs: null, dchart: null, ds: null, lines: [] };
  var pq = (QS.get('panel') || '').toLowerCase();
  if (pq) { var tk = TABS.filter(function (t) { return t[0] === pq; })[0]; S.tab = tk ? tk[0] : (PANEL_ALIAS[pq] || S.tab); }

  function $(id) { return document.getElementById(id); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function num(x, d) { if (x == null || !isFinite(x)) return '—'; return Number(x).toLocaleString('en-US', { minimumFractionDigits: d || 0, maximumFractionDigits: d || 0 }); }
  function pct(x, d) { return (x == null || !isFinite(x)) ? '—' : (100 * x).toFixed(d == null ? 0 : d) + '%'; }
  function px(x) { return num(x, x != null && Math.abs(x) < 1000 ? 2 : 0); }
  function sgn(x, d) { return (x == null || !isFinite(x)) ? '—' : (x >= 0 ? '+' : '−') + Math.abs(x).toFixed(d == null ? 2 : d); }
  function tagV(v) { return v ? '<span class="tag ok">✓ keçir</span>' : '<span class="tag no">baza</span>'; }
  function trendHtml(t) { var x = TREND[t]; return x ? '<b class="' + x[1] + '">' + x[0] + '</b>' : '<span class="muted">—</span>'; }
  function ago(iso) {
    if (!iso) return '—';
    var t = Date.parse(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z'); var s = (Date.now() - t) / 1000;
    if (!isFinite(s)) return '—'; if (s < 90) return Math.round(s) + ' s əvvəl'; if (s < 5400) return Math.round(s / 60) + ' dəq əvvəl'; return Math.round(s / 3600) + ' saat əvvəl';
  }
  function setUrl() {
    var q = new URLSearchParams(location.search); q.set('tf', S.tf); q.set('panel', S.tab);
    history.replaceState(null, '', location.pathname + '?' + q.toString());
  }

  // ---------------------------------------------------------------- UI qurulması
  function buildControls() {
    $('tf').innerHTML = TFS.map(function (t) { return '<button type="button" data-tf="' + t + '" class="' + (t === S.tf ? 'on' : '') + '">' + t + '</button>'; }).join('');
    $('prof-sel').innerHTML = PROFS.map(function (p) { return '<button type="button" data-prof="' + p + '" class="' + (p === S.prof ? 'on' : '') + '">' + p + '</button>'; }).join('');
    $('tabs').innerHTML = TABS.map(function (t) { return '<button type="button" data-tab="' + t[0] + '" class="' + (t[0] === S.tab ? 'on' : '') + '">' + t[1] + '</button>'; }).join('');
    $('tf').onclick = function (e) { var b = e.target.closest('button'); if (!b) return; S.tf = b.dataset.tf; buildControls(); setUrl(); renderAll(true); };
    $('prof-sel').onclick = function (e) { var b = e.target.closest('button'); if (!b) return; S.prof = b.dataset.prof; buildControls(); renderAll(false); };
    $('tabs').onclick = function (e) { var b = e.target.closest('button'); if (!b) return; S.tab = b.dataset.tab; buildControls(); setUrl(); renderPanel(); if (S.tab === 'stat') loadStats(); };
  }

  function initChart() {
    if (!window.LightweightCharts) { $('ct').innerHTML = '<div class="banner">Qrafik kitabxanası yüklənmədi.</div>'; return; }
    var LC = window.LightweightCharts;
    var opt = { layout: { background: { color: '#0d1117' }, textColor: '#8b949e' }, grid: { vertLines: { color: '#161b22' }, horzLines: { color: '#161b22' } },
      rightPriceScale: { borderColor: '#30363d' }, timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 }, autoSize: true };
    S.chart = LC.createChart($('ct'), opt);
    S.cs = S.chart.addCandlestickSeries({ upColor: '#3fb950', downColor: '#f85149', borderVisible: false, wickUpColor: '#3fb950', wickDownColor: '#f85149' });
    S.dchart = LC.createChart($('ct-delta'), Object.assign({}, opt, { timeScale: { visible: false }, rightPriceScale: { borderColor: '#30363d' } }));
    S.ds = S.dchart.addHistogramSeries({ priceFormat: { type: 'volume' } });
    S.chart.timeScale().subscribeVisibleLogicalRangeChange(function (r) { if (r) S.dchart.timeScale().setVisibleLogicalRange(r); });
  }

  // ---------------------------------------------------------------- məlumat
  function load() {
    var ctl = new AbortController(); var to = setTimeout(function () { ctl.abort(); }, 12000);
    return fetch(API + '/all.json', { cache: 'no-store', signal: ctl.signal }).then(function (r) {
      clearTimeout(to); if (!r.ok) throw new Error('HTTP ' + r.status); return r.json();
    }).then(function (d) {
      var first = !S.data; S.data = d; S.err = null; S.lastOk = Date.now(); renderAll(first);
    }).catch(function (e) { clearTimeout(to); S.err = e && e.message ? e.message : String(e); renderStatus(); if (!S.data) renderPanel(); });
  }
  function loadStats() {
    if (S.stats && Date.now() - S.stats._t < 10 * 60 * 1000) { renderPanel(); return; }
    fetch(API + '/stats.json', { cache: 'no-store' }).then(function (r) { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
      .then(function (d) { d._t = Date.now(); S.stats = d; if (S.tab === 'stat') renderPanel(); })
      .catch(function (e) { S.statsErr = String(e); if (S.tab === 'stat') renderPanel(); });
  }

  function C() { return S.data && S.data.consensus; }
  function M() { return C() && C().models; }

  // ---------------------------------------------------------------- render
  function renderStatus() {
    var st = $('status'), off = $('offline');
    if (S.err && !S.data) {
      st.className = 'pill st-off'; st.textContent = 'backend offline';
      off.hidden = false; off.textContent = 'Backend offline: məlumat alınmadı (' + S.err + '). Rəqəm göstərilmir — uydurma/köhnə fallback yoxdur.';
      return;
    }
    if (S.err) {
      st.className = 'pill st-stale'; st.textContent = 'əlaqə kəsildi · son məlumat ' + ago(new Date(S.lastOk).toISOString());
      off.hidden = false; off.textContent = 'Backend-ə hazırda qoşulmaq olmur (' + S.err + '). Aşağıdakı məlumat KÖHNƏDİR (' + ago(new Date(S.lastOk).toISOString()) + ').';
      return;
    }
    var d = S.data; if (!d) return;
    var stale = d.stale || !(d.engine_health && d.engine_health.ok);
    st.className = 'pill ' + (stale ? 'st-stale' : 'st-ok');
    st.textContent = stale ? 'məlumat köhnədir (' + Math.round(d.age_s) + ' s)' : 'canlı · ' + Math.round(d.age_s) + ' s';
    off.hidden = !stale;
    if (stale) off.textContent = 'Mühərrik məlumatı ' + Math.round(d.age_s) + ' saniyədir yenilənməyib — rəqəmlər köhnə ola bilər.';
    $('upd').textContent = 'Mühərrik: ' + (C() ? ago(C().generated) : '—') + ' · TF ' + S.tf;
  }

  function renderPrice() {
    var c = C(); if (!c) return;
    $('pr').textContent = px(c.price);
    var b = S.data.bars && S.data.bars['1h'];
    if (b && b.rows.length > 24) {
      var ci = b.cols.indexOf('c'); var old = b.rows[b.rows.length - 25][ci]; var ch = c.price / old - 1;
      $('cg').textContent = (ch >= 0 ? '+' : '') + (100 * ch).toFixed(2) + '% (24s)'; $('cg').className = 'pill ' + (ch >= 0 ? 'bg-gn' : 'bg-rd');
    }
  }

  function renderChart(fit) {
    if (!S.cs || !S.data || !S.data.bars) return;
    var b = S.data.bars[S.tf];
    S.chart.applyOptions({ timeScale: { secondsVisible: S.tf === '1s' } });
    if (!b || !b.rows.length) { S.cs.setData([]); S.ds.setData([]); $('legend').textContent = S.tf + ': bar yoxdur'; return; }
    var ix = {}; b.cols.forEach(function (c, i) { ix[c] = i; });
    var cd = [], dd = [], lastT = -1;
    b.rows.forEach(function (r) {
      if (r[ix.o] == null || r[ix.h] == null || r[ix.l] == null || r[ix.c] == null || r[0] <= lastT) return;
      lastT = r[0];
      cd.push({ time: r[0], open: r[ix.o], high: r[ix.h], low: r[ix.l], close: r[ix.c] });
      var de = ix.delta != null ? r[ix.delta] : null;
      if (de != null) dd.push({ time: r[0], value: de, color: de >= 0 ? '#3fb95099' : '#f8514999' });
    });
    S.cs.setData(cd); S.ds.setData(dd);
    S.lines.forEach(function (l) { try { S.cs.removePriceLine(l); } catch (e) {} }); S.lines = [];
    function line(p, color, title, style, w) { if (p == null || !isFinite(p)) return; S.lines.push(S.cs.createPriceLine({ price: p, color: color, lineWidth: w || 1, lineStyle: style == null ? 2 : style, axisLabelVisible: true, title: title })); }
    var m = M(); var leg = [];
    if (m) {
      var p = m.profiles && m.profiles[S.prof];
      if (p) { line(p.poc, '#f2cc60', S.prof + ' POC', 0, 2); line(p.vah, '#a371f7', S.prof + ' VAH', 2); line(p.val, '#a371f7', S.prof + ' VAL', 2);
        (p.naked_poc || []).slice(-3).forEach(function (n) { line(typeof n === 'object' ? n.price || n.level : n, '#d29922', 'nPOC', 3); });
        leg.push('<span class="yl">— ' + S.prof + ' POC/VAH/VAL</span>'); }
      var z = C().consensus.zones[EVAL[S.tf]];
      if (z) { (z.yuxari || []).concat(z.asagi || []).slice(0, 6).forEach(function (q) {
        line(q.mid, q.edge ? '#3fb950' : '#58a6ff88', (q.edge ? '✓ ' : '') + 'zona ' + pct(q.p), 1); });
        leg.push('<span class="bl">— zonalar (' + EVAL[S.tf] + ')</span>'); }
      var ict = m.ict && m.ict[S.tf === '1s' ? '1m' : S.tf];
      if (ict) { line(ict.protected_high, '#f85149aa', 'qorunan H', 3); line(ict.protected_low, '#3fb950aa', 'qorunan L', 3); }
      var lq = m.liquidation && m.liquidation.clusters;
      if (lq) lq.filter(function (c) { return c.density >= 0.5; }).forEach(function (c) { line(c.price, c.side === 'long_liq' ? '#ff7b72' : '#7ee787', (c.side === 'long_liq' ? 'L-liq ' : 'S-liq ') + pct(c.density), 1); });
    }
    $('legend').innerHTML = '<b>' + S.tf + '</b> · ' + leg.join(' · ') + ' · aşağıda: delta';
    if (fit) S.chart.timeScale().fitContent();
  }

  function renderAll(fit) { renderStatus(); renderPrice(); renderChart(fit); renderPanel(); }

  // ---------------------------------------------------------------- panellər
  function noData() { return '<div class="card empty">' + (S.err ? 'Backend offline — məlumat yoxdur.' : 'Yüklənir…') + '</div>'; }

  function pKonsensus() {
    var c = C(), cs = c.consensus, ez = EVAL[S.tf], z = cs.zones[ez] || {};
    var dir = cs.direction;
    var h = '<div class="grid">';
    h += '<div class="card" style="grid-column:1/-1"><h3>Xülasə</h3><div class="big">' + esc(c.xulase_az) + '</div></div>';
    h += '<div class="card"><h3>Yön</h3><div class="verdict ' + (dir.edge ? 'ok' : '') + '">' + esc(dir.text) + '</div>';
    var row = (M().tf_matrix || []).filter(function (r) { return r.tf === S.tf; })[0];
    if (row) {
      h += '<h4>' + S.tf + ' balı</h4><div class="kv"><span>Bal</span><span>' + sgn(row.score, 1) + ' (' + esc(row.pred) + ')</span>' +
        '<span>Data keyfiyyəti</span><span>' + pct(row.quality) + '</span>' +
        '<span>Tarixi isabət</span><span>' + (row.hist ? pct(row.hist.accuracy, 1) + ' vs baza ' + pct(row.hist.baseline_majority, 1) + ' (n=' + num(row.hist.n) + ')' : '—') + '</span>' +
        '<span>Kalibrlənmiş</span><span>' + (row.cal ? 'UP ' + pct(row.cal.UP) + ' · RANGE ' + pct(row.cal.RANGE) + ' · DOWN ' + pct(row.cal.DOWN) + (row.cal_works ? '' : ' <span class="muted">(baza — kalibrasiya bazanı keçmir)</span>') : '—') + '</span></div>';
    }
    h += '<div class="note">Bal və yön proqnozu ayrıca göstərilir, amma konsensusa yalnız bazanı statistik keçərsə girir.</div></div>';
    function zl(arr, t) {
      if (!arr || !arr.length) return '<div class="empty">' + t + ': zona yoxdur</div>';
      return arr.map(function (q) {
        return '<div class="zone ' + (q.edge ? 'edge' : '') + '"><div><b>' + px(q.lo) + (q.hi !== q.lo ? '–' + px(q.hi) : '') + '</b> <span class="muted">' + num(q.dist_atr, 2) + ' ATR</span><br><span class="muted" style="font-size:.8em">' +
          esc((q.levels || []).slice(0, 5).join(', ')) + ((q.levels || []).length > 5 ? ' …' : '') + '</span></div><div style="text-align:right"><b>' + pct(q.p) + '</b><br>' +
          (q.edge ? '<span class="tag ok">təsdiqlənmiş</span>' : '<span class="tag no">məsafə bazası</span>') + '</div></div>';
      }).join('');
    }
    h += '<div class="card"><h3>Qiymət ən çox hansı zonaya gedir? (' + ez + ' horizontu)</h3><h4>Yuxarıda</h4>' + zl(z.yuxari, 'Yuxarıda') + '<h4>Aşağıda</h4>' + zl(z.asagi, 'Aşağıda') +
      '<div class="note">Ehtimal = bu məsafədə istənilən səviyyəyə çatma bazası × (yalnız təsdiqlənmiş səviyyə varsa) onun ölçülmüş lift-i. "Məsafə bazası" — səviyyənin özünün əlavə gücü sübut olunmayıb.</div></div>';
    var v = cs.validated;
    h += '<div class="card"><h3>Bazanı keçən modellər (konsensusa girən)</h3>';
    if (v.hedef.length) h += '<table class="t"><tr><th>Səviyyə</th><th>TF</th><th class="n">n</th><th class="n">İsabət</th><th class="n">Baza</th><th class="n">Lift</th></tr>' +
      v.hedef.map(function (t) { return '<tr><td>' + esc(t.source + ' ' + t.kind + ' ' + (LEVEL_AZ[t.level] || t.level)) + '</td><td>' + t.eval_tf + '</td><td class="n">' + num(t.n) + '</td><td class="n">' + pct(t.hit) + '</td><td class="n">' + pct(t.baza) + '</td><td class="n"><b class="gn">' + num(t.lift, 2) + '</b></td></tr>'; }).join('') + '</table>';
    else h += '<div class="empty">Heç bir hədəf səviyyəsi bazanı praktik keçmir.</div>';
    h += '<h4>Hadisələr</h4>' + (v.hadise.length ? v.hadise.map(function (e) { return '<span class="tag ok">' + e.tf + ' ' + e.type + ': ' + pct(e.win) + ' vs ' + pct(e.baza) + '</span>'; }).join('') : '<div class="empty">Bazanı keçən ICT hadisəsi yoxdur.</div>');
    h += '<h4>Ssenarilər</h4>' + (v.ssenari.length ? v.ssenari.map(function (e) { return '<span class="tag ok">' + esc(e.scenario) + ' ' + e.horizon_h + 's</span>'; }).join('') : '<div class="empty">Bazanı keçən ssenari yoxdur.</div>');
    h += '<div class="note">' + esc(cs.metod.qayda) + '</div></div>';
    var dq = cs.data_keyfiyyeti || {};
    h += '<div class="card"><h3>Data keyfiyyəti (güvəndən ayrı)</h3><div class="kv"><span>Mühərrik</span><span>' + (dq.engine_ok ? '<b class="gn">işləyir</b>' : '<b class="rd">xəta</b>') + ' · ' + ago(dq.engine_ts) + '</span>' +
      '<span>Collector</span><span>' + (dq.collector_ok ? '<b class="gn">bütün axınlar canlı</b>' : '<b class="yl">köhnə axın: ' + esc((dq.kohne_axin || []).join(', ')) + '</b>') + ' · ' + ago(dq.collector_ts) + '</span>' +
      '<span>Orta TF keyfiyyəti</span><span>' + pct(dq.tf_keyfiyyet_orta) + '</span></div><div class="note">Data keyfiyyəti yüksək olsa da, proqnoz gücü ayrıca ölçülür.</div></div>';
    return h + '</div>';
  }

  function pTF() {
    var rows = M().tf_matrix || [];
    var h = '<div class="card"><h3>TF matrisi — hər zaman dilimi ayrıca</h3><div class="tw"><table class="t"><tr><th>TF</th><th>Trend (ICT)</th><th class="n">Bal</th><th>Bal</th><th class="n">Keyfiyyət</th><th>Proqnoz</th><th class="n">Delta</th><th class="n">Həcm/orta</th><th class="n">OB disbalans</th><th>Tarixi isabət (baza)</th><th>Kalibrlənmiş UP/RANGE/DOWN</th></tr>';
    rows.forEach(function (r) {
      var w = Math.min(100, Math.abs(r.score || 0)); var col = (r.score || 0) >= 0 ? '#3fb950' : '#f85149';
      h += '<tr' + (r.tf === S.tf ? ' style="background:#1f6feb22"' : '') + '><td><b>' + r.tf + '</b></td><td>' + trendHtml(r.trend) + '</td><td class="n">' + sgn(r.score, 0) + '</td><td style="min-width:80px"><div class="bar"><i style="width:' + w + '%;background:' + col + '"></i></div></td>' +
        '<td class="n">' + pct(r.quality) + '</td><td>' + esc(r.pred || '—') + '</td><td class="n ' + ((r.delta || 0) >= 0 ? 'gn' : 'rd') + '">' + num(r.delta, 1) + '</td><td class="n">' + num(r.vol_ratio, 2) + '</td><td class="n">' + num(r.ob_imb, 2) + '</td>' +
        '<td>' + (r.hist ? pct(r.hist.accuracy, 1) + ' <span class="muted">(' + pct(r.hist.baseline_majority, 1) + ', n=' + num(r.hist.n) + ')</span> ' + (r.hist.edge ? '<span class="tag ok">edge</span>' : '<span class="tag no">edge yoxdur</span>') : '—') + '</td>' +
        '<td>' + (r.cal ? pct(r.cal.UP) + ' / ' + pct(r.cal.RANGE) + ' / ' + pct(r.cal.DOWN) + (r.cal_works ? '' : ' <span class="muted">baza</span>') : '—') + '</td></tr>';
    });
    h += '</table></div><div class="note">Bal: delta / həcm / ATR / orderbook disbalansı / OI əsaslı şəffaf formula (−100…+100). Tarixi isabət: proqnoz jurnalında bal → horizont sonu sinfi; baza = həmişə ən çox rast gələn sinfi demək. "Edge yoxdur" — bal bu TF-də bazadan yaxşı deyil.</div></div>';
    return h;
  }

  function pVP() {
    var ps = M().profiles || {};
    var h = '<div class="grid">';
    PROFS.forEach(function (k) {
      var p = ps[k]; if (!p) return;
      h += '<div class="card"' + (k === S.prof ? ' style="border-color:#388bfd"' : '') + '><h3>' + k + ' profil ' + (k === '1h' ? '<span class="muted">(son 60 ədəd 1m şam)</span>' : '') + '</h3>' +
        '<div class="kv"><span>POC</span><span><b class="yl">' + px(p.poc) + '</b></span><span>VAH / VAL</span><span>' + px(p.vah) + ' / ' + px(p.val) + '</span>' +
        '<span>HVN</span><span>' + ((p.hvn || []).map(px).join(', ') || '—') + '</span><span>LVN</span><span>' + ((p.lvn || []).map(px).join(', ') || '—') + '</span>' +
        '<span>Naked POC</span><span>' + ((p.naked_poc || []).map(function (n) { return px(typeof n === 'object' ? (n.price || n.level) : n); }).join(', ') || '—') + '</span>' +
        '<span>Aralıq</span><span>' + px(p.lo) + ' – ' + px(p.hi) + ' (bin ' + px(p.bin) + ')</span></div>';
      h += '<h4>Hədəfə çatma statistikası (jurnal)</h4><table class="t"><tr><th>Səviyyə</th><th>TF</th><th class="n">n</th><th class="n">Çatdı</th><th class="n">Baza</th><th class="n">Lift</th><th></th></tr>';
      ['POC', 'VAH', 'VAL', 'HVN', 'LVN'].forEach(function (lv) {
        var s = p.stats && p.stats[lv];
        h += s ? '<tr><td>' + lv + '</td><td>' + s.eval_tf + '</td><td class="n">' + num(s.n) + '</td><td class="n">' + pct(s.hit) + '</td><td class="n">' + pct(s.baza) + '</td><td class="n ' + (s.lift >= 1.05 ? 'gn' : s.lift < 0.97 ? 'rd' : '') + '">' + num(s.lift, 2) + '</td><td>' + tagV(s.valid) + '</td></tr>'
               : '<tr><td>' + lv + '</td><td colspan="6" class="muted">statistika yoxdur</td></tr>';
      });
      if (p.npoc_stats) { var s2 = p.npoc_stats; h += '<tr><td>naked POC</td><td>' + s2.eval_tf + '</td><td class="n">' + num(s2.n) + '</td><td class="n">' + pct(s2.hit) + '</td><td class="n">' + pct(s2.baza) + '</td><td class="n ' + (s2.lift >= 1.05 ? 'gn' : '') + '">' + num(s2.lift, 2) + '</td><td>' + tagV(s2.valid) + '</td></tr>'; }
      h += '</table></div>';
    });
    return h + '</div><div class="note">"Çatdı" — hədəf horizontu ərzində qiymətin səviyyəyə toxunma payı. Baza — eyni məsafədəki istənilən səviyyəyə toxunma ehtimalı. Lift = çatdı / baza. Bazanı keçmək üçün: CI alt sərhədi bazadan yuxarı və lift ≥ 1.05.</div>';
  }

  function pICT() {
    var tf = S.tf === '1s' ? '1m' : S.tf; var s = M().ict && M().ict[tf]; var per = M().periods || {};
    if (!s) return '<div class="card empty">' + tf + ' üçün ICT strukturu yoxdur.</div>';
    function lst(a, f) { return (a && a.length) ? a.map(f).join('<br>') : '<span class="muted">—</span>'; }
    var h = '<div class="grid"><div class="card"><h3>ICT strukturu · ' + tf + '</h3><div class="kv"><span>Trend</span><span>' + trendHtml(s.trend) + '</span>' +
      '<span>Son swing H / L</span><span>' + px(s.last_swing_high) + ' / ' + px(s.last_swing_low) + '</span><span>Qorunan H / L</span><span>' + px(s.protected_high) + ' / ' + px(s.protected_low) + '</span>' +
      '<span>EQH</span><span>' + lst(s.eqh, function (x) { return px(typeof x === 'object' ? x.level || x.price : x); }) + '</span><span>EQL</span><span>' + lst(s.eql, function (x) { return px(typeof x === 'object' ? x.level || x.price : x); }) + '</span>' +
      '<span>FVG</span><span>' + lst(s.fvg, function (f) { return '<span class="' + (f.dir > 0 ? 'gn' : 'rd') + '">' + px(f.lo) + '–' + px(f.hi) + '</span>'; }) + '</span>' +
      '<span>Order block</span><span>' + lst(s.order_blocks, function (f) { return '<span class="' + (f.dir > 0 ? 'gn' : 'rd') + '">' + px(f.lo) + '–' + px(f.hi) + '</span>'; }) + '</span></div></div>';
    h += '<div class="card"><h3>Dövr səviyyələri</h3><div class="kv">' + [['PDH', 'PDL'], ['PWH', 'PWL'], ['PMH', 'PML'], ['asia_H', 'asia_L'], ['london_H', 'london_L'], ['newyork_H', 'newyork_L']].map(function (p) {
      return '<span>' + p[0].replace('_H', '') + ' H / L</span><span>' + px(per[p[0]]) + ' / ' + px(per[p[1]]) + '</span>'; }).join('') + '</div></div>';
    h += '<div class="card"><h3>Son hadisələr · ' + tf + '</h3><table class="t"><tr><th>Vaxt</th><th>Tip</th><th>Yön</th><th class="n">Səviyyə</th></tr>' +
      ((s.son_hadiseler || []).slice().reverse().map(function (e) { return '<tr><td>' + esc(String(e.t || '').replace('T', ' ').slice(0, 16)) + '</td><td>' + esc(e.type) + '</td><td class="' + (e.dir > 0 ? 'gn' : 'rd') + '">' + (e.dir > 0 ? '↑' : '↓') + '</td><td class="n">' + px(e.level) + '</td></tr>'; }).join('') || '<tr><td colspan="4" class="muted">—</td></tr>') + '</table></div>';
    h += '<div class="card"><h3>Hadisələrin tarixi nəticəsi · ' + tf + '</h3><table class="t"><tr><th>Tip</th><th class="n">n</th><th class="n">Qazanma</th><th class="n">Baza</th><th></th></tr>' +
      ((s.stats || []).map(function (e) { return '<tr><td>' + e.type + '</td><td class="n">' + num(e.n) + '</td><td class="n">' + pct(e.win) + '</td><td class="n">' + pct(e.baza) + '</td><td>' + tagV(e.valid) + '</td></tr>'; }).join('') || '<tr><td colspan="5" class="muted">—</td></tr>') +
      '</table><div class="note">' + esc(((s.stats || [])[0] || {}).olcu || 'üçlü bariyer ±1 ATR') + ' — hadisə istiqamətində +1 ATR, −1 ATR-dən əvvəl çatırsa "qazanma".</div></div></div>';
    return h;
  }

  function pFlow() {
    var f = M().flow || {}; var mp = f.max_pain;
    var h = '<div class="grid"><div class="card"><h3>Capital flow — şərh</h3>' + ((f.serh || []).map(function (s) { return '<div>• ' + esc(s) + '</div>'; }).join('') || '<div class="empty">—</div>') +
      '<div class="note">Son bağlanmış saat: ' + esc(String(f.hour || '').replace('T', ' ')) + '</div></div>';
    h += '<div class="card"><h3>Göstəricilər</h3><div class="kv"><span>Money flow (1s)</span><span class="' + ((f.mf_usd || 0) >= 0 ? 'gn' : 'rd') + '">' + num((f.mf_usd || 0) / 1e6, 1) + ' mln $</span>' +
      '<span>Spot delta</span><span>' + num(f.spot_delta, 1) + ' BTC</span><span>OI dəyişimi</span><span>' + pct(f.oi_chg, 2) + '</span><span>OI kvadrantı</span><span>' + esc(f.oi_quadrant || '—') + '</span>' +
      '<span>Funding</span><span>' + (f.funding != null ? (100 * f.funding).toFixed(4) + '%' : '—') + ' (persentil ' + pct(f.funding_pctile) + ')</span>' +
      '<span>Top treyder (mövqe L/S)</span><span>' + num(f.top_pos, 3) + '</span><span>Kütlə (hesab L/S)</span><span>' + num(f.crowd, 3) + '</span>' +
      '<span>Top vs kütlə</span><span>' + num(f.top_vs_crowd, 2) + '</span><span>Taker alış/satış</span><span>' + num(f.taker, 3) + ' (persentil ' + pct(f.taker_pctile) + ')</span></div></div>';
    h += '<div class="card"><h3>Deribit max pain</h3>' + (mp ? '<div class="kv"><span>Ən yaxın (' + esc(mp.nearest.expiry) + ')</span><span><b>' + px(mp.nearest.max_pain) + '</b> (' + sgn(mp.nearest.dist_pct, 1) + '%) · P/C ' + num(mp.nearest.pcr_oi, 2) + '</span>' +
      '<span>Ən böyük OI (' + esc(mp.largest.expiry) + ')</span><span><b>' + px(mp.largest.max_pain) + '</b> (' + sgn(mp.largest.dist_pct, 1) + '%) · ' + num(mp.largest.oi_btc) + ' BTC</span></div>' : '<div class="empty">Deribit məlumatı yoxdur.</div>') +
      '<div class="note">Max pain — opsion satıcılarının ən az ödədiyi ekspirasiya qiyməti. Proqnoz gücü ölçülməyib (canlı data 14.10-dan).</div></div></div>';
    return h;
  }

  function pLiq() {
    var l = M().liquidation || {}; var cl = (l.clusters || []).slice().sort(function (a, b) { return b.price - a.price; });
    var price = C().price;
    var h = '<div class="card"><h3>Likvidasiya xəritəsi (giriş qiymətinə əsaslanan)</h3>';
    if (!cl.length) h += '<div class="empty">Hazırda ±8% aralığında klaster yoxdur.</div>';
    else h += '<table class="t"><tr><th>Tərəf</th><th class="n">Qiymət</th><th class="n">Məsafə</th><th>Sıxlıq</th></tr>' + cl.map(function (c) {
      return '<tr><td class="' + (c.side === 'long_liq' ? 'rd' : 'gn') + '">' + (c.side === 'long_liq' ? 'Long likvidasiyası' : 'Short likvidasiyası') + '</td><td class="n">' + px(c.price) + '</td><td class="n">' + sgn(100 * (c.price / price - 1), 2) + '%</td><td style="min-width:120px"><div class="bar"><i style="width:' + Math.round(100 * c.density) + '%;background:' + (c.side === 'long_liq' ? '#f85149' : '#3fb950') + '"></i></div></td></tr>';
    }).join('') + '</table>';
    h += '<div class="note">Metod: hər 5 dəqiqədə OI artımı həmin barın VWAP-ına "giriş" kimi yazılır, leverage paylanması (5x–100x) ilə likvidasiya qiymətlərinə paylanır; OI azalanda qatlar proporsional azalır, qiymət səviyyəni keçəndə qat silinir. ' +
      'Doğrulama (Binance COIN-M 2023–24, toxunulmamış test): xəritənin "sıx" dediyi barlarda real likvidasiya 2.8× (long) / 4.9× (short) çox; köhnə "hazırkı qiymət" metodunda fərq yox idi. Mütləq dollar məbləğləri deyil, nisbi sıxlıqdır.</div></div>';
    return h;
  }

  function pScen() {
    var sc = M().scenarios || {};
    var h = '<div class="grid"><div class="card"><h3>Son 24 saatda yaranan ssenarilər</h3>';
    h += (sc.active || []).length ? '<table class="t"><tr><th>Vaxt</th><th>Ssenari</th><th>Yön</th><th>Horizont</th><th>Tarixi</th></tr>' + sc.active.map(function (a) {
      return '<tr><td>' + esc(String(a.t || '').replace('T', ' ').slice(0, 16)) + '</td><td><b>' + esc(a.scenario) + '</b><br><span class="muted" style="font-size:.85em">' + esc(a.doc || '') + '</span></td><td class="' + (a.dir > 0 ? 'gn' : 'rd') + '">' + (a.dir > 0 ? '↑' : '↓') + '</td><td>' + a.horizon_h + ' s</td><td>' +
        (a.hist ? pct(a.hist.win) + ' vs ' + pct(a.hist.baza) + ' (n=' + a.hist.n + ') ' + tagV(a.hist.valid) : '—') + '</td></tr>'; }).join('') + '</table>' : '<div class="empty">Son 24 saatda ssenari yaranmayıb.</div>';
    h += '</div><div class="card"><h3>Ssenari jurnalı — reallaşma statistikası</h3><div class="tw"><table class="t"><tr><th>Ssenari</th><th>Horizont</th><th class="n">n</th><th class="n">Reallaşdı</th><th class="n">Baza</th><th></th></tr>' +
      (sc.stats || []).map(function (s) { return '<tr><td title="' + esc(s.doc || '') + '">' + esc(s.scenario) + '</td><td>' + s.horizon_h + ' s</td><td class="n">' + num(s.n) + '</td><td class="n">' + pct(s.win) + '</td><td class="n">' + pct(s.baza) + '</td><td>' + tagV(s.valid) + '</td></tr>'; }).join('') +
      '</table></div><div class="note">Ölçü: 1×ATR(1h) üçlü bariyer, 4 və 24 saat. n < 30 olan ssenarilər qiymətləndirilmir.</div></div></div>';
    return h;
  }

  function pStat() {
    var st = S.stats;
    if (!st) return '<div class="card empty">' + (S.statsErr ? 'Statistika yüklənmədi: ' + esc(S.statsErr) : 'Statistika yüklənir…') + '</div>';
    var ez = EVAL[S.tf];
    var t = (st.targets || []).filter(function (x) { return x.eval_tf === ez; }).sort(function (a, b) { return (b.lift || 0) - (a.lift || 0); });
    var h = '<div class="card"><h3>Hədəf jurnalı · ' + ez + ' horizontu — hansı səviyyə daha çox çatır?</h3><div class="tw"><table class="t"><tr><th>Mənbə</th><th>Növ</th><th>Səviyyə</th><th class="n">n</th><th class="n">Çatdı</th><th class="n">CI</th><th class="n">Baza</th><th class="n">Lift</th><th class="n">Orta məsafə</th></tr>' +
      t.map(function (x) { var ok = x.n >= 100 && x.ci_lo > x.expected && x.lift >= 1.05; return '<tr><td>' + esc(x.source) + '</td><td>' + esc(x.kind) + '</td><td>' + esc(LEVEL_AZ[x.level] || x.level) + '</td><td class="n">' + num(x.n) + '</td><td class="n">' + pct(x.hit_rate) + '</td><td class="n muted">' + pct(x.ci_lo) + '–' + pct(x.ci_hi) + '</td><td class="n">' + pct(x.expected) + '</td><td class="n ' + (ok ? 'gn' : x.lift < 0.97 ? 'rd' : '') + '"><b>' + num(x.lift, 2) + '</b></td><td class="n">' + num(x.median_dist_atr, 2) + ' ATR</td></tr>'; }).join('') +
      '</table></div></div>';
    h += '<div class="grid" style="margin-top:12px"><div class="card"><h3>Proqnoz jurnalı (bal → nəticə)</h3><table class="t"><tr><th>TF</th><th class="n">n</th><th class="n">İsabət</th><th class="n">Baza</th><th class="n">UP/RANGE/DOWN tezliyi</th></tr>' +
      (st.predictions || []).slice().sort(function (a, b) { return TFS.indexOf(a.tf) - TFS.indexOf(b.tf); }).map(function (p) { return '<tr><td>' + p.tf + '</td><td class="n">' + num(p.n) + '</td><td class="n ' + (p.accuracy > p.baseline_majority ? 'gn' : 'rd') + '">' + pct(p.accuracy, 1) + '</td><td class="n">' + pct(p.baseline_majority, 1) + '</td><td class="n">' + pct(p.freq_up) + ' / ' + pct(p.freq_range) + ' / ' + pct(p.freq_down) + '</td></tr>'; }).join('') + '</table></div>';
    var ev = (st.events || []).filter(function (e) { return e.tf === (S.tf === '1s' ? '1m' : S.tf); });
    h += '<div class="card"><h3>ICT hadisələri · ' + (S.tf === '1s' ? '1m' : S.tf) + '</h3><table class="t"><tr><th>Tip</th><th class="n">n</th><th class="n">Qazanma</th><th class="n">Məğlub</th><th class="n">Baza</th></tr>' +
      (ev.map(function (e) { return '<tr><td>' + e.type + '</td><td class="n">' + num(e.n) + '</td><td class="n">' + pct(e.win) + '</td><td class="n">' + pct(e.loss) + '</td><td class="n">' + pct(e.base_win) + '</td></tr>'; }).join('') || '<tr><td colspan="5" class="muted">—</td></tr>') + '</table></div></div>';
    h += '<div class="note">Mənbə: tarixi geri doldurma (90 gün Binance arxivi + 1 saniyəlik bərpa) və canlı jurnal. Yenilənmə: 30 dəqiqədə bir (' + ago(st.generated) + '). Diqqət: eyni anda yaranan hədəflər bir-birindən asılıdır, CI-lər olduğundan dar görünə bilər.</div>';
    return h;
  }

  function pAbout() {
    var cal = (C() && C().calibration) || {};
    return '<div class="grid"><div class="card"><h3>Sistem necə qurulub</h3><div class="note" style="font-size:.92em;color:#c9d1d9">' +
      '<b>Collector</b> (7/24, VPS): Binance futures order book 100 ms, trade, likvidasiya, funding; spot trade; Bybit order book/trade/likvidasiya; OI, top treyder, L/S, taker; Deribit opsion (max pain). Xam data saatlıq sıxılmış fayllarda, gündə 2 dəfə Drive arxivi.<br><br>' +
      '<b>Mühərrik</b> (hər 60 s): 1s, 1m, 3m, 5m, 15m, 1h, 4h, 1D, 3D barları; delta/həcm/ATR balı; volume profile (1h profil = son 60 ədəd 1m şam, VA 70%, AMT qaydaları); ICT (BOS, CHoCH, MSB, EQH/EQL, FVG, OB, PDH/PDL, PWH/PWL, sessiyalar); capital flow; likvidasiya xəritəsi.<br><br>' +
      '<b>Jurnallar</b>: hər hədəf (hansı profil/səviyyə, nə vaxt çatdı), hər ICT hadisəsi, hər ssenari, hər bal proqnozu — həll olunur və baza ilə müqayisə edilir.<br><br>' +
      '<b>Yekdil qərar</b>: hər model ayrıca göstərilir; konsensusa yalnız bazanı statistik (CI) və praktik (lift ≥ 1.05) keçən modellər girir. Heç biri keçmirsə — açıq "edge yoxdur".</div></div>' +
      '<div class="card"><h3>Kalibrasiya vəziyyəti (proqnoz jurnalı)</h3><table class="t"><tr><th>TF</th><th class="n">n</th><th class="n">Brier (kalibr.)</th><th class="n">Brier (baza)</th><th></th></tr>' +
      TFS.map(function (tf) { var c = cal[tf]; if (!c) return ''; return '<tr><td>' + tf + '</td><td class="n">' + num(c.n) + '</td><td class="n">' + num(c.brier_cal, 4) + '</td><td class="n">' + num(c.brier_base, 4) + '</td><td>' + (c.ok ? (c.works ? '<span class="tag ok">işləyir</span>' : '<span class="tag no">bazanı keçmir</span>') : '<span class="tag no">' + esc(c.sebeb || 'yox') + '</span>') + '</td></tr>'; }).join('') +
      '</table><div class="note">Histogram kalibrasiyası, zamana görə 70/30 bölgü; yalnız test dəsti ≥300 və Brier bazadan aydın aşağı olduqda "işləyir". 6 saatdan bir yenidən qurulur.</div></div>' +
      '<div class="card"><h3>Dürüst məhdudiyyətlər</h3><div class="note" style="font-size:.92em">• 1 dəqiqədən 1 günə qədər bal əsaslı yön proqnozu indiyə qədər bazanı keçmir.<br>• Profil səviyyələrinin çoxu bazaya yaxındır (lift 0.95–1.03); zəif müsbət: 1D naked POC, FVG ortası, EQH/EQL.<br>• Canlı likvidasiya, spot delta və max pain 14.10.2026-dan var; tarixi arxivdə yoxdur.<br>• Canlı 1s barları 5 dəqiqəyə qədər gecikə bilər (collector snapshot-ları 5 dəqiqəlik fayllarda).<br>• Köhnə serverdəki observer, paper trade və Hova datası itib; köhnə sayt "Köhnə sayt (arxiv)" linkindədir.</div></div></div>';
  }

  function renderPanel() {
    var el = $('panel');
    if (!C()) { el.innerHTML = noData(); return; }
    var f = { konsensus: pKonsensus, tf: pTF, vp: pVP, ict: pICT, flow: pFlow, liq: pLiq, ssenari: pScen, stat: pStat, haqqinda: pAbout }[S.tab] || pKonsensus;
    try { el.innerHTML = f(); } catch (e) { el.innerHTML = '<div class="banner">Panel xətası: ' + esc(e.message) + '</div>'; if (window.console) console.error(e); }
  }

  // ---------------------------------------------------------------- start
  buildControls(); initChart(); renderStatus(); renderPanel();
  if (S.tab === 'stat') loadStats();
  load(); setInterval(load, 15000);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) load(); });
})();
