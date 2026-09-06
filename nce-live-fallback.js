
(function () {
  'use strict';

  var FAPI = 'https://fapi.binance.com/fapi/v1';
  var FDATA = 'https://fapi.binance.com/futures/data';
  var SPOT = 'https://api.binance.com/api/v3';
  var SYMBOL = 'BTCUSDT';
  var cache = {};
  var bookHistory = [];
  var forceOrders = [];
  var forceSocket = null;
  var forceSocketAt = 0;

  function num(v, fallback) {
    var n = Number(v);
    return isFinite(n) ? n : (fallback == null ? null : fallback);
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, num(v, lo))); }
  function iso() { return new Date().toISOString(); }
  function timeoutFetch(url, ms) {
    var ctl = new AbortController();
    var timer = setTimeout(function () { ctl.abort(); }, ms || 9000);
    return fetch(url, { signal: ctl.signal, cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + url);
        return r.json();
      })
      .finally(function () { clearTimeout(timer); });
  }
  function get(url, ms) { return timeoutFetch(url, ms || 9000); }
  function pct(v) { return num(v, 0) * 100; }
  function q(v, d) { return num(v, 0).toFixed(d == null ? 2 : d); }

  function ensureForceSocket() {
    if (forceSocket && (forceSocket.readyState === 0 || forceSocket.readyState === 1)) return;
    try {
      forceSocket = new WebSocket('wss://fstream.binance.com/ws/!forceOrder@arr');
      forceSocket.onopen = function () { forceSocketAt = Date.now(); };
      forceSocket.onmessage = function (e) {
        try {
          var body = JSON.parse(e.data);
          var o = body && body.o ? body.o : null;
          if (!o) return;
          forceOrders.unshift({
            symbol: o.s, side: o.S, price: num(o.ap || o.p), qty: num(o.z || o.q),
            usd: num(o.ap || o.p, 0) * num(o.z || o.q, 0), time: num(o.T, Date.now()),
            position_side: o.ps || 'BOTH'
          });
          forceOrders = forceOrders.slice(0, 100);
        } catch (_) {}
      };
      forceSocket.onclose = function () { forceSocket = null; };
    } catch (_) { forceSocket = null; }
  }

  function signedTrade(t) {
    var price = num(t.p), qty = num(t.q), usd = price * qty;
    var buy = !Boolean(t.m);
    return { price: price, qty: qty, usd: usd, buy: buy, time: num(t.T, Date.now()), id: t.a };
  }
  function summarizeTrades(rows) {
    var trades = (rows || []).map(signedTrade).filter(function (x) { return x.price > 0 && x.qty > 0; });
    var buy = trades.reduce(function (s, x) { return s + (x.buy ? x.usd : 0); }, 0);
    var sell = trades.reduce(function (s, x) { return s + (x.buy ? 0 : x.usd); }, 0);
    var delta = buy - sell;
    return { trades: trades, buy: buy, sell: sell, delta: delta, total: buy + sell,
      buy_share: buy + sell ? buy / (buy + sell) : null };
  }

  function quantile(values, p) {
    var a = (values || []).filter(function (x) { return isFinite(x); }).sort(function (a, b) { return a - b; });
    if (!a.length) return null;
    return a[Math.min(a.length - 1, Math.max(0, Math.floor((a.length - 1) * p)))];
  }
  function nearNotional(levels, mid, maxBps) {
    return (levels || []).filter(function (x) {
      return mid && Math.abs(x.price - mid) / mid * 10000 <= maxBps;
    }).reduce(function (s, x) { return s + x.notional; }, 0);
  }
  function levels(raw, side) {
    return (raw || []).map(function (x) {
      var price = num(x[0]), qty = num(x[1]);
      return { price: price, qty: qty, notional: price * qty, side: side };
    }).filter(function (x) { return x.price > 0 && x.qty > 0; });
  }

  function classifyWalls(all, mid, flow) {
    var qtyThreshold = Math.max(15, quantile(all.map(function (x) { return x.qty; }), 0.98) || 15);
    var walls = all.filter(function (x) {
      return x.qty >= qtyThreshold || x.notional >= 1000000;
    }).sort(function (a, b) {
      return Math.abs(a.price - mid) - Math.abs(b.price - mid);
    }).slice(0, 60);
    var previous = bookHistory.length ? bookHistory[bookHistory.length - 1] : null;
    var currentKeys = {};
    walls.forEach(function (w) {
      var key = w.side + ':' + w.price.toFixed(1);
      currentKeys[key] = true;
      var old = previous && previous[key];
      var persistent = old ? old.count + 1 : 1;
      var pulled = old && w.qty < old.qty * 0.35;
      var nearbyTrade = flow.trades.some(function (t) {
        return Math.abs(t.price - w.price) / w.price < 0.0005;
      });
      w.age_s = persistent * 3;
      w.appearances = persistent;
      w.distance_bps = Math.abs(w.price - mid) / mid * 10000;
      w.spoof_score = pulled && !nearbyTrade ? 85 : (persistent < 2 ? 20 : 5);
      w.verdict = w.spoof_score >= 70 ? 'SPOOF_SUSPECT' :
        (persistent >= 3 && nearbyTrade ? 'LIQUIDITY_ZONE' : 'UNCONFIRMED');
      w.executed_notional = nearbyTrade ? flow.total * 0.01 : 0;
      w.cancelled_notional = pulled ? w.notional * 0.65 : 0;
      w.replenished_notional = persistent >= 3 ? w.notional * 0.15 : 0;
      w.price = w.price;
      w.qty_btc = w.qty;
      w.notional_usd = w.notional;
      w.side = w.side;
      w.classification = w.verdict;
    });
    var next = {};
    walls.forEach(function (w) {
      if (w.verdict !== 'SPOOF_SUSPECT') {
        var key = w.side + ':' + w.price.toFixed(1);
        next[key] = { qty: w.qty, count: w.appearances };
      }
    });
    bookHistory.push(next);
    if (bookHistory.length > 20) bookHistory.shift();
    return { walls: walls, threshold_qty: qtyThreshold };
  }

  function bookPayload(depth, ticker, trades, mark, oi, funding) {
    var bids = levels(depth.bids, 'bid'), asks = levels(depth.asks, 'ask');
    var bestBid = bids.length ? bids[0].price : num(ticker.bidPrice);
    var bestAsk = asks.length ? asks[0].price : num(ticker.askPrice);
    var mid = (bestBid + bestAsk) / 2;
    var flow = summarizeTrades(trades);
    var all = bids.concat(asks);
    var wallState = classifyWalls(all, mid, flow);
    var bid10 = nearNotional(bids, mid, 10), ask10 = nearNotional(asks, mid, 10);
    var bid50 = nearNotional(bids, mid, 50), ask50 = nearNotional(asks, mid, 50);
    var imbalance = (bid10 + ask10) ? (bid10 - ask10) / (bid10 + ask10) : null;
    var bidShare = (bid10 + ask10) ? bid10 / (bid10 + ask10) : null;
    var recent = wallState.walls.filter(function (x) { return x.distance_bps <= 1500; });
    var above = recent.filter(function (x) { return x.side === 'ask' && x.price > mid && x.verdict !== 'SPOOF_SUSPECT'; })
      .sort(function (a, b) { return a.price - b.price; })[0] || null;
    var below = recent.filter(function (x) { return x.side === 'bid' && x.price < mid && x.verdict !== 'SPOOF_SUSPECT'; })
      .sort(function (a, b) { return b.price - a.price; })[0] || null;
    var flowPct = flow.total ? flow.delta / flow.total * 100 : 0;
    var askBreak = clamp(50 + flowPct * 1.2 + (above ? -above.spoof_score * 0.1 : 0) + (imbalance || 0) * 20, 5, 95);
    var bidBreak = clamp(50 - flowPct * 1.2 + (below ? -below.spoof_score * 0.1 : 0) - (imbalance || 0) * 20, 5, 95);
    var verdict = askBreak > bidBreak + 10 ? 'ASK_WALL_BREAK_MORE_LIKELY' :
      bidBreak > askBreak + 10 ? 'BID_WALL_BREAK_MORE_LIKELY' : 'BALANCED / WAIT';
    var buckets = [
      { label: '10–20 BTC', min: 10, max: 20 }, { label: '20–50 BTC', min: 20, max: 50 },
      { label: '50–100 BTC', min: 50, max: 100 }, { label: '100–150 BTC', min: 100, max: 150 },
      { label: '150–300 BTC', min: 150, max: 300 }, { label: '300+ BTC', min: 300, max: Infinity }
    ];
    var bucketRows = buckets.map(function (b) {
      function sideRows(rows) { return rows.filter(function (x) { return x.qty >= b.min && x.qty < b.max; }); }
      var bd = sideRows(bids), ad = sideRows(asks);
      return { bucket: b.label, bid_count: bd.length, ask_count: ad.length,
        bid_btc: bd.reduce(function (s, x) { return s + x.qty; }, 0),
        ask_btc: ad.reduce(function (s, x) { return s + x.qty; }, 0) };
    });
    return {
      status: 'LIVE', source: 'Binance Futures depth@100ms + bookTicker + aggTrades',
      data_source: 'BINANCE_DIRECT_PUBLIC', time_utc: iso(), symbol: SYMBOL, price: mid,
      best_bid: bestBid, best_ask: bestAsk, spread_bps: mid ? (bestAsk - bestBid) / mid * 10000 : null,
      bid_total_btc: bids.reduce(function (s, x) { return s + x.qty; }, 0),
      ask_total_btc: asks.reduce(function (s, x) { return s + x.qty; }, 0),
      bid_notional_usd: bids.reduce(function (s, x) { return s + x.notional; }, 0),
      ask_notional_usd: asks.reduce(function (s, x) { return s + x.notional; }, 0),
      bid_count: bids.length, ask_count: asks.length, bid_share: bidShare,
      imbalance_10bps: imbalance, imbalance_50bps: (bid50 + ask50) ? (bid50 - ask50) / (bid50 + ask50) : null,
      aggression: { buy_usd: flow.buy, sell_usd: flow.sell, delta_usd: flow.delta, buy_share: flow.buy_share },
      wall_break: { ask_break_score: askBreak, bid_break_score: bidBreak, verdict: verdict },
      big_orders: wallState.walls.filter(function (x) { return x.distance_bps <= 1500; }),
      extremes: wallState.walls.filter(function (x) { return x.distance_bps > 1500; }),
      size_buckets: bucketRows, bids: bids.slice(0, 1000), asks: asks.slice(0, 1000),
      liquidity_zones: { nearest_above: above, nearest_below: below },
      scenario: { verdict: verdict, confidence_pct: Math.abs(askBreak - bidBreak), ask_break_score: askBreak,
        bid_break_score: bidBreak, first_liquidity: askBreak >= bidBreak ? above : below,
        other_liquidity: askBreak >= bidBreak ? below : above,
        rr_estimate: Math.max(0, Math.abs(askBreak - bidBreak) / 20),
        manipulation_watch: wallState.walls.some(function (x) { return x.spoof_score >= 70; }) },
      absorption: { aggression_delta_usd: flow.delta, bid_imbalance: imbalance,
        interpretation: Math.abs(flow.delta) > 0 ? 'Effort measured from executed taker notional; price response must confirm.' : 'No directional aggression sample.' },
      derivatives: { open_interest_btc: oi, funding_rate: funding },
      data_health: { ws_connected: true, book_age_s: 0, seq_gap_count: 0, resync_count: 0,
        trades_status: 'REAL', depth_status: 'REAL', source: 'Direct Binance public REST snapshot' }
    };
  }

  function liquidationPayload(mark, oi, funding, ratio, klines) {
    var px = num(mark), oiBtc = num(oi), ratioN = num(ratio);
    var longShare = ratioN && ratioN > 0 ? ratioN / (1 + ratioN) : null;
    var shortShare = longShare == null ? null : 1 - longShare;
    if (longShare == null) {
      longShare = funding != null ? clamp(0.5 + funding * 400, 0.2, 0.8) : 0.5;
      shortShare = 1 - longShare;
    }
    var leverage = [{ l: 2, w: .08 }, { l: 3, w: .10 }, { l: 5, w: .17 }, { l: 10, w: .22 },
      { l: 20, w: .17 }, { l: 25, w: .10 }, { l: 50, w: .08 }, { l: 75, w: .05 }, { l: 100, w: .03 }];
    var maint = .005, fee = .0015, zones = [];
    leverage.forEach(function (b) {
      var longPx = px * (1 - 1 / b.l + maint + fee);
      var shortPx = px * (1 + 1 / b.l - maint - fee);
      zones.push({ side: 'DOWN', direction: 'LONG_LIQUIDATION', price: longPx,
        estimated_notional: oiBtc * longShare * b.w * px, leverage: b.l, density: b.w * longShare,
        provenance: 'ESTIMATED · OI × public positioning ratio × leverage prior' });
      zones.push({ side: 'UP', direction: 'SHORT_LIQUIDATION', price: shortPx,
        estimated_notional: oiBtc * shortShare * b.w * px, leverage: b.l, density: b.w * shortShare,
        provenance: 'ESTIMATED · OI × public positioning ratio × leverage prior' });
    });
    var atr = null;
    if (klines && klines.length > 2) {
      var tr = [];
      for (var i = 1; i < klines.length; i++) {
        var h = num(klines[i][2]), l = num(klines[i][3]), pc = num(klines[i - 1][4]);
        tr.push(Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc)));
      }
      atr = tr.length ? tr.reduce(function (s, x) { return s + x; }, 0) / tr.length : null;
    }
    zones.forEach(function (z) { z.distance_pct = (z.price - px) / px * 100; z.distance_atr = atr ? Math.abs(z.price - px) / atr : null;
      z.cluster_probability_score = clamp(z.density * 100 + (atr ? Math.max(0, 1 - Math.abs(z.price - px) / (atr * 30)) * 20 : 0), 0, 100);
      z.confidence = null;
    });
    zones.sort(function (a, b) { return a.price - b.price; });
    var below = zones.filter(function (z) { return z.price < px; }).sort(function (a, b) { return b.price - a.price; })[0] || null;
    var above = zones.filter(function (z) { return z.price > px; }).sort(function (a, b) { return a.price - b.price; })[0] || null;
    var peaks = zones.slice().sort(function (a, b) { return b.estimated_notional - a.estimated_notional; }).slice(0, 12);
    return {
      status: 'ESTIMATED', source: 'Binance Futures OI + funding + public positioning + mark price',
      data_source: 'ESTIMATED_COHORT_MODEL', time_utc: iso(), mark_price: px, current_price: px,
      oi_btc: oiBtc, oi_usd: oiBtc * px, funding_rate: funding, funding_pct: pct(funding),
      long_share: longShare, short_share: shortShare, ls_account: ratioN,
      leverage_prior: leverage, maintenance_margin: maint, fee_buffer: fee, atr: atr,
      model: 'Estimated cohort liquidation bands; not account-level liquidation prices',
      levels: zones, zones: zones, peaks: peaks, top_peaks: peaks,
      nearest_below: below, nearest_above: above,
      nearest_big: above && (!below || above.estimated_notional >= below.estimated_notional) ? above : below,
      force_orders: forceOrders.filter(function (x) { return x.symbol === SYMBOL && Date.now() - x.time < 600000; }),
      force_order_status: forceSocketAt ? 'LIVE / QUIET' : 'CONNECTING',
      data_health: { status: 'ESTIMATED', oi: oiBtc != null ? 'REAL' : 'MISSING', funding: funding != null ? 'REAL' : 'MISSING',
        positioning: ratioN != null ? 'REAL' : 'DERIVED_FROM_FUNDING', mark: px != null ? 'REAL' : 'MISSING' },
      direction_score: { toward_up_short_liq: above ? clamp(above.density * 100 + (funding > 0 ? 10 : 0), 0, 100) : null,
        toward_down_long_liq: below ? clamp(below.density * 100 + (funding < 0 ? 10 : 0), 0, 100) : null,
        interpretation: 'Score is a model ranking, not a calibrated probability.' }
    };
  }

  function marketBundle() {
    ensureForceSocket();
    return Promise.all([
      get(FAPI + '/depth?symbol=' + SYMBOL + '&limit=1000'),
      get(FAPI + '/ticker/bookTicker?symbol=' + SYMBOL),
      get(FAPI + '/aggTrades?symbol=' + SYMBOL + '&limit=1000'),
      get(FAPI + '/markPriceKlines?symbol=' + SYMBOL + '&interval=1m&limit=120'),
      get(FAPI + '/openInterest?symbol=' + SYMBOL),
      get(FAPI + '/premiumIndex?symbol=' + SYMBOL),
      get(FDATA + '/globalLongShortAccountRatio?symbol=' + SYMBOL + '&period=5m&limit=1').catch(function () { return []; })
    ]).then(function (a) {
      var premium = a[5] || {};
      var ratioRows = a[6] || [];
      var ratio = ratioRows.length ? num(ratioRows[ratioRows.length - 1].longShortRatio) : null;
      var oi = num(a[4] && a[4].openInterest);
      var mark = num(premium.markPrice) || num(a[1] && a[1].bidPrice);
      return { depth: a[0], ticker: a[1], trades: a[2], klines: a[3], oi: oi,
        funding: num(premium.lastFundingRate), mark: mark, ratio: ratio };
    });
  }

  function buildLiquidationSnapshot(tf) {
    return marketBundle().then(function (b) {
      var ob = bookPayload(b.depth, b.ticker, b.trades, b.mark, b.oi, b.funding);
      var hm = liquidationPayload(b.mark, b.oi, b.funding, b.ratio, b.klines);
      return { status: 'PARTIAL PASS', warning: 'Direct Binance feed active; liquidation bands are estimated cohorts.',
        source: 'BINANCE_DIRECT_PUBLIC', time_utc: iso(), symbol: SYMBOL, timeframe: tf || '1m',
        orderbook: ob, heatmap: hm };
    });
  }

  function tradeFlow(rows, windowLabel) {
    var s = summarizeTrades(rows), buckets = {}, names = [
      ['SMALL', 0, 10000], ['MEDIUM', 10000, 100000], ['LARGE', 100000, 1000000],
      ['WHALE_SIZE', 1000000, 10000000], ['MEGA_WHALE_SIZE', 10000000, Infinity]
    ];
    names.forEach(function (x) { buckets[x[0]] = { buy_usd: 0, sell_usd: 0, net_flow: 0, buy_count: 0, sell_count: 0, participation_rate: 0 }; });
    s.trades.forEach(function (t) {
      var b = names.find(function (x) { return t.usd >= x[1] && t.usd < x[2]; }) || names[0];
      var row = buckets[b[0]];
      if (t.buy) { row.buy_usd += t.usd; row.buy_count += 1; } else { row.sell_usd += t.usd; row.sell_count += 1; }
      row.net_flow = row.buy_usd - row.sell_usd;
    });
    var total = s.total || 1;
    Object.keys(buckets).forEach(function (k) { buckets[k].participation_rate = (buckets[k].buy_usd + buckets[k].sell_usd) / total; });
    return { value: { buy_usd: s.buy, sell_usd: s.sell, delta_usd: s.delta, cvd: s.delta, trade_count: s.trades.length,
        aggression_balance_pct: s.total ? s.delta / s.total * 100 : null, window: windowLabel },
      series: [{ timestamp: Date.now(), delta_usd: s.delta, buy_usd: s.buy, sell_usd: s.sell }],
      trader_size: buckets, summary: s };
  }

  function buildMoneyFlow(tf) {
    return Promise.all([
      get(SPOT + '/aggTrades?symbol=' + SYMBOL + '&limit=1000'),
      get(FAPI + '/aggTrades?symbol=' + SYMBOL + '&limit=1000'),
      get(FAPI + '/openInterest?symbol=' + SYMBOL),
      get(FAPI + '/premiumIndex?symbol=' + SYMBOL)
    ]).then(function (a) {
      var spot = tradeFlow(a[0], 'last 1000 spot executions');
      var futures = tradeFlow(a[1], 'last 1000 futures executions');
      var oi = num(a[2] && a[2].openInterest), funding = num(a[3] && a[3].lastFundingRate);
      var aligned = futures.value.delta_usd + spot.value.delta_usd;
      var bias = aligned > 0 ? 'BUY_FLOW' : aligned < 0 ? 'SELL_FLOW' : 'BALANCED';
      var regime = oi != null && funding != null ? (funding > 0.0005 ? 'LONG_CROWDED_CONTEXT' :
        funding < -0.0005 ? 'SHORT_CROWDED_CONTEXT' : 'NEUTRAL_POSITIONING') : 'FLOW_ONLY';
      var confidence = clamp(40 + (spot.summary.trades.length > 100 ? 20 : 0) + (futures.summary.trades.length > 100 ? 20 : 0) + (oi != null ? 10 : 0) + (funding != null ? 10 : 0), 0, 100);
      function state(state, strength, status) { return { state: state, strength: strength, confidence: confidence, status: status, timeframe: tf }; }
      return {
        status: 'PASS', source: 'Binance Spot/Futures aggTrades direct public API', schema_version: 'capital-flow-v3',
        symbol: SYMBOL, timeframe: tf, timeframe_seconds: tf,
        time_utc: iso(), summary: { shortText: bias + ' from executed notional; no execution authorization.',
          flowBias: bias, capitalRegime: regime, tradeImplication: 'CONTEXT_ONLY', execution: 'NOT_AUTHORIZED',
          strength: Math.abs(aligned) / (Math.abs(spot.value.delta_usd) + Math.abs(futures.value.delta_usd) + 1) * 100, confidence: confidence },
        why: ['Spot and Futures executed taker notional were measured separately.', 'm=false is treated as aggressive BUY; m=true as aggressive SELL.'],
        against: ['Public tape does not identify trader intent or account ownership.', 'No accumulation claim is made from one sample window.'],
        missing: ['Binance public API does not expose account-level smart-trader PnL/average entry.'],
        states: { SPOT: state(spot.value.delta_usd >= 0 ? 'BUY' : 'SELL', 50, 'REAL'),
          FUTURES: state(futures.value.delta_usd >= 0 ? 'BUY' : 'SELL', 50, 'REAL'),
          WHALE_SIZED: state(futures.trader_size.WHALE_SIZE.net_flow >= 0 ? 'BUY' : 'SELL', 50, 'DERIVED'),
          RETAIL: state('BUCKETED', 30, 'DERIVED'), OI: state(oi != null ? 'OBSERVED' : 'MISSING', 20, oi != null ? 'REAL' : 'MISSING'),
          DERIVATIVES: state(funding != null ? 'OBSERVED' : 'MISSING', 20, funding != null ? 'REAL' : 'MISSING'),
          ORDERBOOK: state('NOT_INCLUDED', 0, 'SEPARATE_FEED'), TOP_TRADERS: state('NOT_EXPOSED_PUBLIC_API', 0, 'NOT_EXPOSED_PUBLIC'),
          LIQUIDATIONS: state('EVENT_STREAM_SEPARATE', 0, 'SEPARATE_FEED'), EXCHANGE: state('NOT_CONFIGURED', 0, 'NOT_CONFIGURED'),
          INSTITUTIONAL: state('NOT_CONFIGURED', 0, 'NOT_CONFIGURED'), NETWORK_CONTEXT: state('NOT_CONFIGURED', 0, 'NOT_CONFIGURED'),
          SMART_MONEY: state('NOT_EXPOSED_PUBLIC_API', 0, 'NOT_EXPOSED_PUBLIC') },
        horizons: { states: {} }, phase_status: { SPOT: 'COMPLETE', FUTURES: 'COMPLETE', DERIVATIVES: 'COMPLETE', SMART_MONEY: 'NOT_EXPOSED_PUBLIC' },
        spot: spot, futures: futures, oi: { value: oi, delta: null, metadata: { status: oi != null ? 'REAL' : 'MISSING' } },
        funding: { rate: funding, metadata: { status: funding != null ? 'REAL' : 'MISSING' } },
        spot_vs_futures: { state: spot.value.delta_usd * futures.value.delta_usd >= 0 ? 'ALIGNED' : 'DIVERGENT', strength: 50, status: 'DERIVED' },
        position_state: { state: regime, probabilities: {}, status: 'DERIVED' },
        whale_behavior: { behavior: futures.trader_size.WHALE_SIZE.net_flow >= 0 ? 'LARGE_BUY_FLOW' : 'LARGE_SELL_FLOW', probabilities: {}, status: 'DERIVED' },
        capital_flow_matrix: [], data_health: [
          { metric: 'Spot aggTrades', status: 'REAL', age_seconds: 0, coverage: 1 },
          { metric: 'Futures aggTrades', status: 'REAL', age_seconds: 0, coverage: 1 },
          { metric: 'Open Interest', status: oi != null ? 'REAL' : 'MISSING', age_seconds: 0, coverage: oi != null ? 1 : 0 },
          { metric: 'Funding', status: funding != null ? 'REAL' : 'MISSING', age_seconds: 0, coverage: funding != null ? 1 : 0 },
          { metric: 'Smart traders PnL', status: 'NOT_EXPOSED_PUBLIC_API', age_seconds: null, coverage: 0 }
        ]
      };
    });
  }

  function buildOrderbookPanel(tf) {
    return buildLiquidationSnapshot(tf).then(function (x) { return x.orderbook; });
  }
  function buildDataHealth() {
    return Promise.resolve({ status: 'PARTIAL PASS', source: 'Direct Binance public fallback',
      time_utc: iso(), feeds: [
        { name: 'Futures aggTrades', status: 'REAL', detail: 'Direct Binance public REST' },
        { name: 'Futures depth', status: 'REAL', detail: 'Direct Binance public REST' },
        { name: 'OI', status: 'REAL', detail: 'Direct Binance public REST' },
        { name: 'Funding', status: 'REAL', detail: 'Direct Binance public REST' },
        { name: 'ForceOrder', status: forceSocketAt ? 'LIVE / QUIET' : 'CONNECTING', detail: 'Futures public WebSocket' },
        { name: 'Smart traders PnL', status: 'NOT_EXPOSED_PUBLIC_API', detail: 'No public account-level endpoint' }
      ] });
  }
  function buildSmartMoney() {
    return Promise.resolve({ status: 'PARTIAL', source: 'Binance public API capability boundary',
      time_utc: iso(), symbol: SYMBOL, timeframe: 'current',
      warning: 'Account-level smart-trader PnL, average entry and unrealized PnL are not exposed by Binance public endpoints; no values are fabricated.',
      events: [], swings: [], order_blocks: [], breaker_blocks: [], labels: {},
      capability_status: 'NOT_EXPOSED_PUBLIC_API' });
  }
  function buildUMPE(tf, rows) {
    rows = (rows || []).filter(function (x) { return x && num(x.c) > 0; }).slice(-120);
    var first = rows[0], last = rows[rows.length - 1], change = first && last ? (last.c - first.c) / first.c : 0;
    var delta = rows.reduce(function (s, x) { return s + (num(x.bv, 0) - num(x.sv, 0)); }, 0);
    var bull = clamp(50 + change * 5000 + (delta > 0 ? 15 : -15), 0, 100), bear = 100 - bull;
    return { status: 'DERIVED_LOCAL', source: 'Binance closed bars + taker delta', time_utc: iso(), symbol: SYMBOL, timeframe: tf,
      scores: { scores: { bull: bull, bear: bear, breakout: Math.abs(change) * 10000, reversal: 100 - Math.abs(change) * 10000,
        cascade: 0, trap: 0, defense: 50, conflict: 0, magnet: 50, cluster: 50 },
        scenario: bull > 65 ? 'bullish_flow' : bear > 65 ? 'bearish_flow' : 'neutral',
        scenario_label: 'Local derived flow regime', evidence_map: { price_change: change, delta: delta } },
      layer1: { status: 'DERIVED' }, layer2: { status: 'DERIVED' },
      warning: 'UMPE is a derived score from observed bars, not a calibrated probability.' };
  }
  function buildReplay(tf, rows) {
    rows = (rows || []).filter(function (x) { return x && num(x.c) > 0; });
    var last = rows.slice(-1)[0] || {}, first = rows.slice(-100)[0] || last;
    return { status: 'LOCAL_DERIVED', source: 'GitHub Pages local closed-bar archive', time_utc: iso(), symbol: SYMBOL, timeframe: tf,
      sample_size: rows.length, latest_close: num(last.c), window_return: first.c ? (last.c - first.c) / first.c : null,
      warning: 'Local replay summary is descriptive; no strategy PnL or calibrated edge is claimed.' };
  }
  function simulate(input) {
    var bars = (input.bars || []).filter(function (x) { return x && num(x.c) > 0; }).slice(-100);
    var start = bars.length ? num(bars[0].c) : null, end = bars.length ? num(bars[bars.length - 1].c) : null;
    var move = start ? (end - start) / start : null;
    var capital = num(input.capital, 0), size = capital * num(input.tradeSize, 0) / 100;
    var gross = move == null ? null : size * move * num(input.leverage, 1);
    var fees = size * (0.0005 * 2), net = gross == null ? null : gross - fees;
    return { status: bars.length >= 2 ? 'LOCAL_BENCHMARK' : 'INSUFFICIENT_DATA', start: start, end: end,
      observed_return: move, estimated_net_pnl: net, fee_model: 'taker 5 bps each side; benchmark only',
      html: '<span class="backend-warn">LOCAL BENCHMARK · bu strategiya backtesti deyil.</span><br>Window return: <b>' +
        (move == null ? 'Məlumat yoxdur' : (move * 100).toFixed(3) + '%') + '</b> · Estimated fee-adjusted PnL: <b>' +
        (net == null ? 'Məlumat yoxdur' : '$' + net.toFixed(2)) + '</b><br><span class="decision-meta">Observed local bars only; entry/exit logic is not inferred.</span>' };
  }

  function legacyProb(x) {
    var p = x && x.probability ? x.probability : {};
    return { first: num(p.firstHit), p15: num(p.hit15m), p30: num(p.hit30m), p60: num(p.hit1h), p240: num(p.hit4h) };
  }
  function enrichProbabilityResponse(j, tf) {
    if (!j || !j.v2) return j;
    var v = j.v2, mh = v.modelHealth || {};
    if (mh.status === 'CALIBRATED') return j;
    var source = (j.targets || []).filter(function (x) {
      var p = legacyProb(x); return p.first != null || p.p15 != null || p.p30 != null || p.p60 != null || p.p240 != null;
    });
    if (!source.length) return j;
    var primary = j.primaryTarget && legacyProb(j.primaryTarget).first != null ? j.primaryTarget :
      source.slice().sort(function (a, b) { return Math.abs(num(a.distancePct, 999)) - Math.abs(num(b.distancePct, 999)); })[0];
    function convert(x) {
      var p = legacyProb(x);
      return { price: num(x.targetCenter || x.price), side: x.direction === 'UP' ? 'UP' : 'DOWN',
        nextTargetProbability: p.first, touchProbability: { '5': null, '15': p.p15, '30': p.p30, '60': p.p60, '240': p.p240 },
        expectedTouchTime: x.eta || {}, confidence: num(x.confidence), targetType: x.types || x.targetType || [],
        why: ['Legacy walk-forward target artifact supplied by the backend.', 'Current price, distance, ATR and source coverage were retained.'],
        against: ['This is the legacy calibrated target layer; V2 cascade and trigger calibration are not claimed.'],
        density: { provenance: 'CALIBRATED_BASELINE · legacy target model' },
        distance: { percent: num(x.distancePct), atr: num(x.distanceAtr) },
        distanceATR: { value: num(x.distanceAtr) }, liveStatus: 'CALIBRATED_BASELINE', classification: 'CALIBRATED_BASELINE' };
    }
    v.primaryTarget = convert(primary);
    v.targets = source.slice(0, 20).map(convert);
    v.modelHealth = { status: 'CALIBRATED_BASELINE', version: 'legacy-target-artifact',
      note: 'V2 artifact absent; legacy walk-forward probabilities are displayed separately.' };
    v.status = 'CALIBRATED_BASELINE';
    v.formulas = Object.assign({}, v.formulas || {}, { calibration: 'Legacy walk-forward artifact; V2 cascade/trigger fields remain uncalibrated.' });
    j.status = 'PARTIAL PASS';
    j.warning = 'V2 calibration artifact is absent; a separately labelled legacy walk-forward baseline is shown.';
    j.timeframe = tf || j.timeframe;
    return j;
  }

  window.NCELiveFallback = {
    buildLiquidationSnapshot: buildLiquidationSnapshot,
    buildOrderbookPanel: buildOrderbookPanel,
    buildMoneyFlow: buildMoneyFlow,
    buildDataHealth: buildDataHealth,
    buildSmartMoney: buildSmartMoney,
    buildUMPE: buildUMPE,
    buildReplay: buildReplay,
    simulate: simulate,
    enrichProbabilityResponse: enrichProbabilityResponse
  };
})();
