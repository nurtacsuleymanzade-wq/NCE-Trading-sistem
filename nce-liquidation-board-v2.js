(function () {
  'use strict';

  var SYMBOL = 'BTCUSDT';
  var FAPI = 'https://fapi.binance.com/fapi/v1';
  var FDATA = 'https://fapi.binance.com/futures/data';
  var NCE_API = 'https://nce-api.78.46.134.148.sslip.io/api/v1';
  var requestCache = {};
  var wallState = {};

  function n(v, fallback) { var x = Number(v); return isFinite(x) ? x : (fallback == null ? null : fallback); }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, n(v, lo))); }
  function sum(a, fn) { return (a || []).reduce(function (s, x) { return s + n(fn ? fn(x) : x, 0); }, 0); }
  function mean(a) { return a && a.length ? sum(a) / a.length : null; }
  function quantile(a, p) {
    var x = (a || []).map(function (v) { return n(v); }).filter(function (v) { return v != null; }).sort(function (u, v) { return u - v; });
    if (!x.length) return null;
    return x[Math.min(x.length - 1, Math.max(0, Math.floor((x.length - 1) * p)))];
  }
  function esc(v) { return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) { return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function fmt(v, d) { var x = n(v); return x == null ? '—' : x.toLocaleString('tr-TR', {minimumFractionDigits:d || 0, maximumFractionDigits:d || 0}); }
  function money(v) { var x = Math.abs(n(v, 0)); if (x >= 1e9) return '$' + fmt(x / 1e9, 2) + 'B'; if (x >= 1e6) return '$' + fmt(x / 1e6, 2) + 'M'; if (x >= 1e3) return '$' + fmt(x / 1e3, 1) + 'K'; return '$' + fmt(x, 0); }
  function pct(v, d) { return fmt(n(v, 0), d == null ? 1 : d) + '%'; }
  function iso() { return new Date().toISOString(); }
  function dir(score) { return n(score, 0) >= 0 ? 'LONG' : 'SHORT'; }
  function sideText(price, current) { return price > current ? 'YUXARI' : 'AŞAĞI'; }

  function fetchJson(url, timeout) {
    var ctl = new AbortController();
    var timer = setTimeout(function () { ctl.abort(); }, timeout || 8500);
    return fetch(url, {cache:'no-store', signal:ctl.signal}).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    }).finally(function () { clearTimeout(timer); });
  }
  function cached(key, url, ttl, fallback) {
    var hit = requestCache[key], now = Date.now();
    if (hit && now - hit.at < ttl) return Promise.resolve(hit.value);
    return fetchJson(url).then(function (value) { requestCache[key] = {at:Date.now(), value:value}; return value; })
      .catch(function () { return hit ? hit.value : fallback; });
  }

  function cleanKlines(rows) {
    var seen = {};
    return (rows || []).map(function (r) { return {t:n(r[0]), o:n(r[1]), h:n(r[2]), l:n(r[3]), c:n(r[4]), v:n(r[5]), qv:n(r[7]), trades:n(r[8]), tbq:n(r[10])}; })
      .filter(function (x) { if (!x.t || !x.h || !x.l || !x.c || seen[x.t]) return false; seen[x.t] = true; return true; })
      .sort(function (a, b) { return a.t - b.t; });
  }
  function atr(rows, period) {
    var a = cleanKlines(rows), tr = [];
    for (var i = 1; i < a.length; i++) tr.push(Math.max(a[i].h - a[i].l, Math.abs(a[i].h - a[i - 1].c), Math.abs(a[i].l - a[i - 1].c)));
    return mean(tr.slice(-(period || 14)));
  }
  function normalizePrice(mark, ticker, klines) {
    var bid = n(ticker && ticker.bidPrice), ask = n(ticker && ticker.askPrice), mid = bid && ask ? (bid + ask) / 2 : null;
    var m = n(mark && mark.markPrice), close = klines && klines.length ? n(klines[klines.length - 1].c) : null;
    var candidates = [mid, m, close].filter(function (x) { return x && x > 0; }).sort(function (a, b) { return a - b; });
    return candidates.length ? candidates[Math.floor(candidates.length / 2)] : null;
  }

  function priorWeek(daily) {
    var now = new Date(), day = (now.getUTCDay() + 6) % 7;
    var weekStart = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - day);
    var rows = daily.filter(function (x) { return x.t >= weekStart - 7 * 86400000 && x.t < weekStart; });
    if (!rows.length) rows = daily.slice(-8, -1);
    return rows;
  }
  function clusterSwings(rows, kind, tolerance) {
    var swings = [], a = rows.slice(-288);
    for (var i = 2; i < a.length - 2; i++) {
      if (kind === 'HIGH' && a[i].h >= a[i-1].h && a[i].h >= a[i-2].h && a[i].h >= a[i+1].h && a[i].h >= a[i+2].h) swings.push(a[i].h);
      if (kind === 'LOW' && a[i].l <= a[i-1].l && a[i].l <= a[i-2].l && a[i].l <= a[i+1].l && a[i].l <= a[i+2].l) swings.push(a[i].l);
    }
    var groups = [];
    swings.sort(function (x, y) { return x - y; }).forEach(function (price) {
      var g = groups.find(function (z) { return Math.abs(z.price - price) <= tolerance; });
      if (g) { g.values.push(price); g.price = mean(g.values); } else groups.push({price:price, values:[price]});
    });
    return groups.filter(function (g) { return g.values.length >= 2; }).map(function (g) { return {kind:kind === 'HIGH' ? 'EQH' : 'EQL', price:g.price, touches:g.values.length, weight:kind === 'HIGH' ? 1.18 : 1.18, provenance:'DERIVED · 5m swing cluster'}; });
  }
  function volumeProfile(rows) {
    var a = rows.slice(-288); if (!a.length) return {};
    var low = Math.min.apply(null, a.map(function (x) { return x.l; }));
    var high = Math.max.apply(null, a.map(function (x) { return x.h; }));
    var step = Math.max((high - low) / 80, high * 0.00005), bins = [];
    for (var i = 0; i < 80; i++) bins.push({price:low + (i + .5) * step, volume:0, i:i});
    a.forEach(function (x) { var p = (x.h + x.l + x.c) / 3, idx = Math.max(0, Math.min(79, Math.floor((p - low) / step))); bins[idx].volume += n(x.qv, x.v); });
    var poc = bins.slice().sort(function (x, y) { return y.volume - x.volume; })[0], total = sum(bins, function (x) { return x.volume; }), acc = 0, chosen = [];
    bins.slice().sort(function (x, y) { return y.volume - x.volume; }).some(function (x) { chosen.push(x); acc += x.volume; return acc >= total * .70; });
    return {poc:poc.price, vah:Math.max.apply(null, chosen.map(function (x) { return x.price; })), val:Math.min.apply(null, chosen.map(function (x) { return x.price; }))};
  }
  function smartMoneyModel(klines5, dailyRows, current, atr5) {
    var k5 = cleanKlines(klines5), daily = cleanKlines(dailyRows), levels = [], prev = daily.length > 1 ? daily[daily.length - 2] : null, pw = priorWeek(daily);
    if (prev) { levels.push({kind:'PDH',price:prev.h,weight:1.35,provenance:'REAL · Binance 1D'}); levels.push({kind:'PDL',price:prev.l,weight:1.35,provenance:'REAL · Binance 1D'}); }
    if (pw.length) { levels.push({kind:'PWH',price:Math.max.apply(null,pw.map(function(x){return x.h;})),weight:1.45,provenance:'REAL · prior UTC week'}); levels.push({kind:'PWL',price:Math.min.apply(null,pw.map(function(x){return x.l;})),weight:1.45,provenance:'REAL · prior UTC week'}); }
    var tol = Math.max(n(atr5, current * .001) * .18, current * .00045);
    levels = levels.concat(clusterSwings(k5,'HIGH',tol), clusterSwings(k5,'LOW',tol));
    var vp = volumeProfile(k5);
    if (vp.poc) { levels.push({kind:'POC',price:vp.poc,weight:1.05,provenance:'DERIVED · 5m volume profile'}); levels.push({kind:'VAH',price:vp.vah,weight:1.0,provenance:'DERIVED · 70% value area'}); levels.push({kind:'VAL',price:vp.val,weight:1.0,provenance:'DERIVED · 70% value area'}); }
    levels = levels.filter(function(x){return x.price && Math.abs(x.price-current)/current < .12;}).map(function (x) {
      x.side = x.price > current ? 'UP' : 'DOWN'; x.distance_pct = Math.abs(x.price-current)/current*100; x.distance_atr = Math.abs(x.price-current)/Math.max(atr5 || current*.001,1); x.score = x.weight * Math.exp(-x.distance_atr/18) * (1 + Math.min(4,n(x.touches,1))*.08); return x;
    });
    var up = sum(levels.filter(function(x){return x.side==='UP';}),function(x){return x.score;}), down = sum(levels.filter(function(x){return x.side==='DOWN';}),function(x){return x.score;});
    var score = (up-down)/(up+down+1e-9)*100, direction = dir(score), eligible = levels.filter(function(x){return direction==='LONG'?x.price>current:x.price<current;}).sort(function(a,b){return b.score-a.score;});
    return {id:'SMART_MONEY',title:'Smart Money Liquidity Map',direction:direction,score:score,quality:levels.length>=4?92:levels.length?58:0,target:eligible[0]||null,levels:levels.sort(function(a,b){return a.price-b.price;}),explain:direction==='LONG'?'Üst tərəfdəki struktur likvidliyi daha cəlbedicidir.':'Alt tərəfdəki struktur likvidliyi daha cəlbedicidir.'};
  }

  function cleanTrades(rows, current) {
    var all = (rows || []).map(function (x) { var price=n(x.p), qty=n(x.q), usd=price*qty; return {price:price,qty:qty,usd:usd,buy:!Boolean(x.m),time:n(x.T)}; }).filter(function(x){return x.price&&x.qty&&Math.abs(x.price-current)/current<.02;});
    var cutoff = Date.now()-300000, recent=all.filter(function(x){return !x.time||x.time>=cutoff;}); if(recent.length<50) recent=all;
    var cap=quantile(recent.map(function(x){return x.usd;}),.995)||Infinity;
    recent.forEach(function(x){x.clean_usd=Math.min(x.usd,cap);}); return recent;
  }
  function flowModel(tradeRows, klines5, oiHist, funding, current, atr5) {
    var trades=cleanTrades(tradeRows,current), buy=sum(trades.filter(function(x){return x.buy;}),function(x){return x.clean_usd;}), sell=sum(trades.filter(function(x){return !x.buy;}),function(x){return x.clean_usd;}), delta=buy-sell, deltaPct=delta/(buy+sell+1e-9)*100;
    var k=cleanKlines(klines5), last=k[k.length-1], back=k[Math.max(0,k.length-4)], ret=last&&back?(last.c-back.c)/back.c*100:0;
    var oi=(oiHist||[]).map(function(x){return {v:n(x.sumOpenInterestValue)||n(x.sumOpenInterest),t:n(x.timestamp)};}).filter(function(x){return x.v;});
    var oiDelta=oi.length>3?(oi[oi.length-1].v-oi[Math.max(0,oi.length-7)].v)/oi[Math.max(0,oi.length-7)].v*100:null;
    var impulse=clamp(ret/(Math.max(atr5,1)/current*100)*18,-28,28), score=clamp(deltaPct*.58+impulse,-75,75);
    if(oiDelta!=null){if(ret>0&&oiDelta>0)score+=10;if(ret<0&&oiDelta>0)score-=10;if(ret>0&&oiDelta<0)score+=5;if(ret<0&&oiDelta<0)score-=5;}
    if(funding>.0005)score-=7;if(funding<-.0005)score+=7; score=clamp(score,-100,100);
    return {id:'ORDER_MONEY_FLOW',title:'Order + Money Flow',direction:dir(score),score:score,quality:trades.length>=100?88:trades.length?55:0,buy_usd:buy,sell_usd:sell,delta_usd:delta,delta_pct:deltaPct,oi_change_pct:oiDelta,funding:funding,price_return_pct:ret,trade_count:trades.length,explain:dir(score)==='LONG'?'Aqressiv alış və qiymət reaksiyası üstün gəlir.':'Aqressiv satış və qiymət reaksiyası üstün gəlir.'};
  }

  function liquidationModel(current, openInterest, funding, ratioRows, topRows, atr5) {
    var oi=n(openInterest&&openInterest.openInterest), oiUsd=oi?oi*current:null, ratio=ratioRows&&ratioRows.length?n(ratioRows[ratioRows.length-1].longShortRatio):null, top=topRows&&topRows.length?n(topRows[topRows.length-1].longShortRatio):null;
    var longShare=ratio&&ratio>0?ratio/(1+ratio):clamp(.5+n(funding,0)*350,.25,.75), shortShare=1-longShare;
    var priors=[{l:5,w:.08},{l:10,w:.18},{l:20,w:.24},{l:25,w:.18},{l:50,w:.15},{l:75,w:.09},{l:100,w:.08}], mmr=.005, fee=.0015, rows=[];
    priors.forEach(function(p){
      var down=current*(1-1/p.l+mmr+fee), up=current*(1+1/p.l-mmr-fee);
      rows.push({direction:'SHORT',liquidates:'LONG',side:'DOWN',leverage:p.l,price:down,model_notional:oiUsd?oiUsd*longShare*p.w:null,density:p.w*longShare});
      rows.push({direction:'LONG',liquidates:'SHORT',side:'UP',leverage:p.l,price:up,model_notional:oiUsd?oiUsd*shortShare*p.w:null,density:p.w*shortShare});
    });
    var maxD=Math.max.apply(null,rows.map(function(x){return x.density;}));
    rows.forEach(function(x){x.distance_pct=Math.abs(x.price-current)/current*100;x.distance_atr=Math.abs(x.price-current)/Math.max(atr5||current*.001,1);x.attraction=100*(x.density/maxD)*Math.exp(-x.distance_atr/22);});
    var up=sum(rows.filter(function(x){return x.side==='UP';}),function(x){return x.attraction;}), down=sum(rows.filter(function(x){return x.side==='DOWN';}),function(x){return x.attraction;}), score=(up-down)/(up+down+1e-9)*100;
    if(funding>.0003) score-=Math.min(18,funding*25000); if(funding<-.0003) score+=Math.min(18,Math.abs(funding)*25000); score=clamp(score,-100,100);
    var direction=dir(score), candidates=rows.filter(function(x){return x.direction===direction;}).sort(function(a,b){return b.attraction-a.attraction;});
    return {id:'LIQUIDATION_HEATMAP',title:'Estimated Liquidation Heatmap',direction:direction,score:score,quality:current&&oi? (ratio?86:72):45,target:candidates[0]||null,levels:rows.sort(function(a,b){return b.price-a.price;}),oi_btc:oi,oi_usd:oiUsd,long_share:longShare,short_share:shortShare,global_ratio:ratio,top_ratio:top,funding:funding,provenance:'ESTIMATED · OI cohort × public positioning × leverage prior',explain:direction==='LONG'?'Yuxarıdakı SHORT likvidasiya sıxlığı daha güclüdür.':'Aşağıdakı LONG likvidasiya sıxlığı daha güclüdür.'};
  }

  function parseBook(depth, side) { return ((depth&&depth[side])||[]).map(function(x){var price=n(x[0]),qty=n(x[1]);return {side:side==='bids'?'BID':'ASK',price:price,qty:qty,notional:price*qty};}).filter(function(x){return x.price&&x.qty;}); }
  function orderbookModel(depth,current,flow) {
    var bids=parseBook(depth,'bids'),asks=parseBook(depth,'asks'),all=bids.concat(asks),now=Date.now(),threshold=Math.max(1e6,quantile(all.map(function(x){return x.notional;}),.97)||1e6);
    var near=function(a,bps){return a.filter(function(x){return Math.abs(x.price-current)/current*10000<=bps;});}, bidNear=sum(near(bids,50),function(x){return x.notional;}),askNear=sum(near(asks,50),function(x){return x.notional;});
    var walls=all.filter(function(x){return x.notional>=threshold;}).sort(function(a,b){return Math.abs(a.price-current)-Math.abs(b.price-current);}).slice(0,20);
    var visible={};walls.forEach(function(w){var key=w.side+':'+w.price.toFixed(1),s=wallState[key];if(!s)s=wallState[key]={first:now,last:now,initial:w.qty,max:w.qty,min:w.qty,seen:0};s.last=now;s.seen++;s.max=Math.max(s.max,w.qty);s.min=Math.min(s.min,w.qty);visible[key]=true;w.age_sec=(now-s.first)/1000;w.stability=s.max?1-(s.max-s.min)/s.max:0;w.classification=w.age_sec>=15&&w.stability>=.70?'CONFIRMED':w.stability<.45?'SPOOF_RISK':'TESTING';w.distance_bps=Math.abs(w.price-current)/current*10000;});
    Object.keys(wallState).forEach(function(k){if(!visible[k]&&now-wallState[k].last>120000)delete wallState[k];});
    var buy=n(flow.buy_usd,0),sell=n(flow.sell_usd,0),support=(bidNear-askNear)/(bidNear+askNear+1e-9)*45,breakEdge=(buy/(askNear+1)-sell/(bidNear+1))*18,score=clamp(support+breakEdge,-100,100),direction=dir(score);
    var target=walls.filter(function(w){return direction==='LONG'?w.side==='ASK'&&w.price>current:w.side==='BID'&&w.price<current;}).sort(function(a,b){return Math.abs(a.price-current)-Math.abs(b.price-current);})[0]||null;
    var ranges=[[10,20],[20,50],[50,100],[100,150],[150,300],[300,Infinity]],buckets=ranges.map(function(r){var pick=function(a){return a.filter(function(x){return x.qty>=r[0]&&x.qty<r[1];});};return {label:r[1]===Infinity?'300+':r[0]+'–'+r[1],bid_count:pick(bids).length,ask_count:pick(asks).length,bid_btc:sum(pick(bids),function(x){return x.qty;}),ask_btc:sum(pick(asks),function(x){return x.qty;})};});
    return {id:'ORDERBOOK_BOT',title:'Order Book Bot',direction:direction,score:score,quality:all.length>=100?90:all.length?55:0,target:target,walls:walls,buckets:buckets,bid_notional_50bps:bidNear,ask_notional_50bps:askNear,imbalance_pct:(bidNear-askNear)/(bidNear+askNear+1e-9)*100,best_bid:bids[0]&&bids[0].price,best_ask:asks[0]&&asks[0].price,explain:direction==='LONG'?'Bid dəstəyi və ASK divarını keçmə gücü daha yüksəkdir.':'Ask təzyiqi və BID divarını qırma gücü daha yüksəkdir.'};
  }

  function finalDecision(modules,current,atr5) {
    var weights={SMART_MONEY:.25,ORDER_MONEY_FLOW:.30,LIQUIDATION_HEATMAP:.25,ORDERBOOK_BOT:.20},num=0,den=0;
    modules.forEach(function(m){var q=clamp(m.quality,0,100)/100,w=weights[m.id]||.25;num+=clamp(m.score,-100,100)*w*q;den+=w*q;});
    var score=den?num/den:0;if(Math.abs(score)<1){var f=modules.find(function(m){return m.id==='ORDER_MONEY_FLOW';});score=f&&f.score?f.score:(score>=0?1:-1);}
    var direction=dir(score), sign=direction==='LONG'?1:-1,candidates=[];
    modules.forEach(function(m){if(m.target&&((direction==='LONG'&&m.target.price>current)||(direction==='SHORT'&&m.target.price<current)))candidates.push({price:m.target.price,kind:m.target.kind||m.target.liquidates||m.target.side||m.id,source:m.id,strength:Math.abs(m.score)+n(m.target.attraction,0)+n(m.target.score,0)});});
    var smart=modules.find(function(m){return m.id==='SMART_MONEY';}); if(smart)smart.levels.forEach(function(x){if((direction==='LONG'&&x.price>current)||(direction==='SHORT'&&x.price<current))candidates.push({price:x.price,kind:x.kind,source:'SMART_MONEY',strength:55+n(x.score,0)*10});});
    candidates.forEach(function(x){x.distance_atr=Math.abs(x.price-current)/Math.max(atr5||current*.001,1);x.rank=x.strength*Math.exp(-x.distance_atr/24);});candidates.sort(function(a,b){return b.rank-a.rank;});
    var target=candidates[0]||{price:current+sign*Math.max(atr5||current*.001,1)*1.5,kind:'ATR TARGET',source:'CLEAN FALLBACK',rank:0};
    var opposite=smart?smart.levels.filter(function(x){return direction==='LONG'?x.price<current:x.price>current;}).sort(function(a,b){return Math.abs(a.price-current)-Math.abs(b.price-current);})[0]:null;
    var invalidation=opposite?opposite.price:current-sign*Math.max(atr5||current*.001,1)*1.2,coverage=sum(modules,function(m){return m.quality;})/(modules.length*100),confidence=clamp(coverage*42+Math.min(58,Math.abs(score)*.9),0,100);
    var ob=modules.find(function(m){return m.id==='ORDERBOOK_BOT';}), trigger=direction==='LONG'?Math.max(current+n(atr5,0)*.08,n(ob&&ob.best_ask,current)):Math.min(current-n(atr5,0)*.08,n(ob&&ob.best_bid,current));
    var eta=Math.max(1,Math.min(240,Math.round(Math.abs(target.price-current)/Math.max(atr5||current*.001,1)*5)));
    return {direction:direction,score:score,confidence:confidence,target:target,invalidation:invalidation,trigger:trigger,eta_min:eta,execution:confidence>=58&&modules.filter(function(m){return m.direction===direction;}).length>=3?'READY':'WAIT FOR TRIGGER',votes:modules.map(function(m){return {id:m.id,direction:m.direction,score:m.score,quality:m.quality};})};
  }

  function buildFrom(raw,tf) {
    var k5=cleanKlines(raw.klines5),current=normalizePrice(raw.premium,raw.ticker,k5);if(!current)throw new Error('No verified current price');var atr5=atr(raw.klines5,14)||current*.001;
    var funding=n(raw.premium&&raw.premium.lastFundingRate,0),smart=smartMoneyModel(raw.klines5,raw.daily,current,atr5),flow=flowModel(raw.trades,raw.klines5,raw.oiHist,funding,current,atr5),heat=liquidationModel(current,raw.openInterest,funding,raw.ratio,raw.topRatio,atr5),book=orderbookModel(raw.depth,current,flow),modules=[smart,flow,heat,book],decision=finalDecision(modules,current,atr5);
    return {status:'PASS',board_version:'NCE_LIQUIDATION_BOARD_V2',symbol:SYMBOL,timeframe:tf||'1m',time_utc:iso(),current_price:current,atr_5m:atr5,decision:decision,smart_money:smart,order_money_flow:flow,liquidation_heatmap:heat,orderbook_bot:book,data_policy:{direction:'LONG_OR_SHORT_ONLY',mixed_removed:true,price_semantics_checked:(decision.direction==='LONG'?decision.target.price>current:decision.target.price<current),observed:['order book','aggTrades','klines','open interest','funding','public long/short ratios'],derived:['EQH/EQL','PDH/PDL/PWH/PWL','volume profile','CVD/delta','wall persistence'],estimated:['liquidation cohort heatmap','ETA']}};
  }
  function build(tf) {
    return Promise.all([
      cached('depth',FAPI+'/depth?symbol='+SYMBOL+'&limit=1000',2500,{bids:[],asks:[]}),
      cached('ticker',FAPI+'/ticker/bookTicker?symbol='+SYMBOL,1200,{}),
      cached('trades',FAPI+'/aggTrades?symbol='+SYMBOL+'&limit=1000',2500,[]),
      cached('klines5',FAPI+'/klines?symbol='+SYMBOL+'&interval=5m&limit=576',45000,[]),
      cached('daily',FAPI+'/klines?symbol='+SYMBOL+'&interval=1d&limit=20',300000,[]),
      cached('oi',FAPI+'/openInterest?symbol='+SYMBOL,5000,{}),
      cached('premium',FAPI+'/premiumIndex?symbol='+SYMBOL,5000,{}),
      cached('ratio',FDATA+'/globalLongShortAccountRatio?pair='+SYMBOL+'&period=5m&limit=1',20000,[]),
      cached('topRatio',FDATA+'/topLongShortPositionRatio?pair='+SYMBOL+'&period=5m&limit=1',20000,[]),
      cached('oiHist',FDATA+'/openInterestHist?symbol='+SYMBOL+'&period=5m&limit=30',20000,[]),
      cached('backendBook',NCE_API+'/orderbook/live',3000,null),
      cached('backendFlow',NCE_API+'/capital-flow/summary?tf='+encodeURIComponent(tf||'1m')+'&symbol='+SYMBOL,5000,null)
    ]).then(function(a){return buildFrom({depth:a[0],ticker:a[1],trades:a[2],klines5:a[3],daily:a[4],openInterest:a[5],premium:a[6],ratio:a[7],topRatio:a[8],oiHist:a[9],backendBook:a[10],backendFlow:a[11]},tf);});
  }

  function ensureStyle(){if(document.getElementById('nce-lb2-style'))return;var s=document.createElement('style');s.id='nce-lb2-style';s.textContent='.lb2{--g:#3fb950;--r:#f85149;--b:#58a6ff;--y:#f2cc60;display:flex;flex-direction:column;gap:14px;max-width:1500px;margin:auto;color:#d7dee8}.lb2 *{box-sizing:border-box}.lb2-top,.lb2-frame{background:#121820;border:1px solid #2c3542;border-radius:12px}.lb2-top{padding:18px;border-color:#3f69a0;background:linear-gradient(135deg,#101a2d,#121820)}.lb2-kicker{font-size:12px;letter-spacing:.12em;color:#8abfff;font-weight:800}.lb2-decision{display:grid;grid-template-columns:minmax(220px,1.3fr) repeat(4,minmax(130px,.7fr));gap:10px;margin-top:12px}.lb2-main,.lb2-stat{padding:14px;background:#0d1117;border:1px solid #303946;border-radius:9px}.lb2-main.long{border-left:6px solid var(--g)}.lb2-main.short{border-left:6px solid var(--r)}.lb2-dir{font-size:34px;font-weight:900}.lb2-dir.long{color:var(--g)}.lb2-dir.short{color:var(--r)}.lb2-label{font-size:12px;color:#8b98aa;text-transform:uppercase;letter-spacing:.05em}.lb2-value{font-size:21px;font-weight:800;color:#f0f6fc;margin-top:5px}.lb2-sub{font-size:13px;color:#9aa7b8;margin-top:5px}.lb2-votes{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.lb2-vote{padding:12px;border-radius:9px;background:#0d1117;border:1px solid #303946}.lb2-vote.long{border-top:3px solid var(--g)}.lb2-vote.short{border-top:3px solid var(--r)}.lb2-vote b{display:block;margin:5px 0;font-size:18px}.lb2-frame{padding:18px}.lb2-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:14px}.lb2-head h3{margin:0;font-size:21px;color:#f0f6fc}.lb2-head p{margin:4px 0 0;color:#8b98aa}.lb2-badge{border:1px solid #4c5665;border-radius:14px;padding:4px 9px;font-size:12px;font-weight:800;white-space:nowrap}.lb2-real{color:#7ee787;border-color:#2f6d3a}.lb2-derived{color:#79c0ff;border-color:#315d83}.lb2-est{color:#f2cc60;border-color:#765c20}.lb2-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}.lb2-metric{background:#0d1117;border:1px solid #252e39;border-radius:8px;padding:11px}.lb2-table{width:100%;border-collapse:collapse;font-size:14px}.lb2-table th,.lb2-table td{padding:9px;border-bottom:1px solid #252e39;text-align:left}.lb2-table th{color:#8b98aa;font-size:12px}.lb2-long{color:var(--g);font-weight:800}.lb2-short{color:var(--r);font-weight:800}.lb2-heat{display:grid;gap:5px}.lb2-heatrow{display:grid;grid-template-columns:90px 85px 1fr 95px 90px;gap:8px;align-items:center;padding:6px 8px;background:#0d1117;border-radius:5px}.lb2-bar{height:10px;background:#252e39;border-radius:6px;overflow:hidden}.lb2-bar i{display:block;height:100%}.lb2-api{font-size:13px;color:#9aa7b8}.lb2-note{padding:10px 12px;border-left:4px solid var(--y);background:#17150f;color:#d8c98c;margin-top:10px}.lb2-scroll{overflow-x:auto}@media(max-width:900px){.lb2-decision{grid-template-columns:1fr 1fr}.lb2-main{grid-column:1/-1}.lb2-votes{grid-template-columns:1fr 1fr}.lb2-heatrow{grid-template-columns:70px 70px 1fr}.lb2-heatrow span:nth-last-child(-n+2){display:none}}@media(max-width:560px){.lb2-decision,.lb2-votes{grid-template-columns:1fr}.lb2-stat{padding:10px}.lb2-frame{padding:13px}.lb2-head{flex-direction:column}}';document.head.appendChild(s);}
  function metric(label,value,sub){return '<div class="lb2-metric"><div class="lb2-label">'+esc(label)+'</div><div class="lb2-value">'+value+'</div>'+(sub?'<div class="lb2-sub">'+esc(sub)+'</div>':'')+'</div>';}
  function moduleVote(m){var d=m.direction.toLowerCase();return '<div class="lb2-vote '+d+'"><span class="lb2-label">'+esc(m.title)+'</span><b class="lb2-'+d+'">'+m.direction+'</b><span>Skor '+fmt(Math.abs(m.score),0)+'/100 · keyfiyyət '+fmt(m.quality,0)+'</span></div>';}
  function render(j){ensureStyle();var d=j.decision,dc=d.direction.toLowerCase(),sm=j.smart_money,fl=j.order_money_flow,hm=j.liquidation_heatmap,ob=j.orderbook_bot;
    var smRows=sm.levels.map(function(x){return '<tr><td><b>'+esc(x.kind)+'</b></td><td>$'+fmt(x.price,1)+'</td><td>'+sideText(x.price,j.current_price)+'</td><td>'+pct(x.distance_pct,2)+'</td><td>'+esc(x.touches||'—')+'</td><td>'+esc(x.provenance)+'</td></tr>';}).join('');
    var heatRows=hm.levels.slice().sort(function(a,b){return Math.abs(a.price-j.current_price)-Math.abs(b.price-j.current_price);}).slice(0,14).map(function(x){var color=x.side==='UP'?'#3fb950':'#f85149';return '<div class="lb2-heatrow"><span class="lb2-'+x.direction.toLowerCase()+'">'+x.direction+'</span><span>'+x.leverage+'x</span><div class="lb2-bar"><i style="width:'+clamp(x.attraction,3,100)+'%;background:'+color+'"></i></div><span>$'+fmt(x.price,1)+'</span><span>'+money(x.model_notional)+'</span></div>';}).join('');
    var wallRows=ob.walls.map(function(w){return '<tr><td class="lb2-'+(w.side==='ASK'?'short':'long')+'">'+w.side+'</td><td>$'+fmt(w.price,1)+'</td><td>'+fmt(w.qty,2)+' BTC</td><td>'+money(w.notional)+'</td><td>'+fmt(w.distance_bps,1)+' bps</td><td>'+fmt(w.age_sec,0)+'s</td><td>'+esc(w.classification)+'</td></tr>';}).join('');
    var bucketRows=ob.buckets.map(function(x){return '<tr><td>'+x.label+' BTC</td><td>'+x.bid_count+' / '+fmt(x.bid_btc,1)+'</td><td>'+x.ask_count+' / '+fmt(x.ask_btc,1)+'</td></tr>';}).join('');
    var html='<div class="lb2"><section class="lb2-top"><div class="lb2-kicker">NCE LIQUIDATION DECISION BOARD · '+esc(j.symbol)+' · '+esc(j.timeframe)+'</div><div class="lb2-decision"><div class="lb2-main '+dc+'"><div class="lb2-label">TƏMİZLƏNMİŞ YEKUN İSTİQAMƏT</div><div class="lb2-dir '+dc+'">'+d.direction+'</div><div class="lb2-sub">'+(d.direction==='LONG'?'Qiymət yuxarı hədəfə yönəlir; hədəfdə SHORT-lar likvidasiya olur.':'Qiymət aşağı hədəfə yönəlir; hədəfdə LONG-lar likvidasiya olur.')+'</div></div>'+metric('CANLI QİYMƏT','$'+fmt(j.current_price,1),'Binance mark + book median')+metric('HƏDƏF','$'+fmt(d.target.price,1),d.target.kind+' · '+d.target.source)+metric('TƏTİKLƏYİCİ','$'+fmt(d.trigger,1),d.execution)+metric('ETİBAR',fmt(d.confidence,0)+'/100','ETA ≈ '+d.eta_min+' dəq')+'</div></section><div class="lb2-votes">'+[sm,fl,hm,ob].map(moduleVote).join('')+'</div>';
    html+='<section class="lb2-frame" id="lb2-smart"><div class="lb2-head"><div><h3>1 · Smart Money Hədəfləri</h3><p>EQH/EQL, PDH/PDL, PWH/PWL və Volume Profile səviyyələri ayrıca hesablanır.</p></div><span class="lb2-badge lb2-derived">REAL + DERIVED</span></div><div class="lb2-grid">'+metric('MODUL QƏRARI','<span class="lb2-'+sm.direction.toLowerCase()+'">'+sm.direction+'</span>',sm.explain)+metric('MODUL HƏDƏFİ',sm.target?'$'+fmt(sm.target.price,1):'ATR hədəfi',sm.target?sm.target.kind:'fallback')+metric('ATR 5M','$'+fmt(j.atr_5m,1),'realized range')+'</div><div class="lb2-scroll"><table class="lb2-table"><thead><tr><th>Səviyyə</th><th>Qiymət</th><th>Tərəf</th><th>Məsafə</th><th>Touch</th><th>Mənbə</th></tr></thead><tbody>'+smRows+'</tbody></table></div></section>';
    html+='<section class="lb2-frame" id="lb2-flow"><div class="lb2-head"><div><h3>2 · Order + Money Flow</h3><p>Yalnız icra olunmuş aggTrade axını; order-book niyyəti bu çərçivəyə qarışdırılmır.</p></div><span class="lb2-badge lb2-real">OBSERVED · REAL</span></div><div class="lb2-grid">'+metric('MODUL QƏRARI','<span class="lb2-'+fl.direction.toLowerCase()+'">'+fl.direction+'</span>',fl.explain)+metric('AGGRESSIVE BUY',money(fl.buy_usd),fl.trade_count+' cleaned trades')+metric('AGGRESSIVE SELL',money(fl.sell_usd),'same window')+metric('CVD / DELTA',(fl.delta_usd>=0?'+':'−')+money(fl.delta_usd),pct(fl.delta_pct,1))+metric('OI DƏYİŞİMİ',fl.oi_change_pct==null?'Mənbə gecikir':pct(fl.oi_change_pct,2),'5m history')+metric('FUNDING',pct(fl.funding*100,4),'crowding context')+'</div></section>';
    html+='<section class="lb2-frame" id="lb2-heatmap"><div class="lb2-head"><div><h3>3 · Coinglass-bənzəri Liquidation Heatmap</h3><p>Faktiki hesab liquidation qiyməti deyil; OI cohort və leverage paylanmasından təxmini sıxlıqdır.</p></div><span class="lb2-badge lb2-est">ESTIMATED · MODEL</span></div><div class="lb2-grid">'+metric('MODUL QƏRARI','<span class="lb2-'+hm.direction.toLowerCase()+'">'+hm.direction+'</span>',hm.explain)+metric('OPEN INTEREST',hm.oi_btc?fmt(hm.oi_btc,0)+' BTC':'Mənbə gecikir',hm.oi_usd?money(hm.oi_usd):'')+metric('LONG / SHORT PAYI',pct(hm.long_share*100,1)+' / '+pct(hm.short_share*100,1),'public account ratio')+metric('MODEL HƏDƏFİ',hm.target?'$'+fmt(hm.target.price,1):'ATR hədəfi',hm.target?hm.target.leverage+'x · '+hm.target.liquidates+' liquidation':'')+'</div><div class="lb2-heat">'+heatRows+'</div><div class="lb2-note">Rəng intensivliyi model sıxlığıdır; faiz ehtimalı və real hesabların cəmi deyil.</div></section>';
    html+='<section class="lb2-frame" id="lb2-book"><div class="lb2-head"><div><h3>4 · Order Book Bot</h3><p>Bid/ask divarları, 50 bps imbalance, persistence və spoof riski ayrıca izlənir.</p></div><span class="lb2-badge lb2-real">OBSERVED · REAL</span></div><div class="lb2-grid">'+metric('BOT QƏRARI','<span class="lb2-'+ob.direction.toLowerCase()+'">'+ob.direction+'</span>',ob.explain)+metric('BID 50 BPS',money(ob.bid_notional_50bps),'resting notional')+metric('ASK 50 BPS',money(ob.ask_notional_50bps),'resting notional')+metric('IMBALANCE',pct(ob.imbalance_pct,1),'positive = bid support')+'</div><div class="lb2-scroll"><table class="lb2-table"><thead><tr><th>Side</th><th>Qiymət</th><th>Həcm</th><th>Notional</th><th>Məsafə</th><th>Yaş</th><th>Sinif</th></tr></thead><tbody>'+wallRows+'</tbody></table></div><h4>Order ölçü bucket-ləri</h4><div class="lb2-scroll"><table class="lb2-table"><thead><tr><th>Bucket</th><th>BID count / BTC</th><th>ASK count / BTC</th></tr></thead><tbody>'+bucketRows+'</tbody></table></div></section>';
    html+='<section class="lb2-frame"><div class="lb2-head"><div><h3>API və data izləri</h3><p>Hansı məlumatın haradan gəldiyi açıq göstərilir.</p></div><span class="lb2-badge lb2-derived">AUDITABLE</span></div><div class="lb2-api">Binance Futures: <b>/fapi/v1/depth</b>, <b>/ticker/bookTicker</b>, <b>/aggTrades</b>, <b>/klines</b>, <b>/openInterest</b>, <b>/premiumIndex</b> · Binance Futures Data: <b>/globalLongShortAccountRatio</b>, <b>/topLongShortPositionRatio</b>, <b>/openInterestHist</b> · NCE backend: <b>/orderbook/live</b>, <b>/capital-flow/summary</b> health/fallback.</div></section></div>';
    return html;
  }

  window.NCELiquidationBoardV2={build:build,buildFrom:buildFrom,render:render,_test:{cleanKlines:cleanKlines,smartMoneyModel:smartMoneyModel,flowModel:flowModel,liquidationModel:liquidationModel,orderbookModel:orderbookModel,finalDecision:finalDecision}};
})();
