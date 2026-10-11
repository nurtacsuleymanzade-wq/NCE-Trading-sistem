// NCE Chart Modes - Ask-Bid priority + structure notes
(function(){
  'use strict';
  window.chartMode = 'candle';

  window.applyChartMode = function(mode) {
    window.chartMode = mode;
    document.querySelectorAll('.mode-btn').forEach(function(b){
      var on = b.dataset.mode === mode;
      b.classList.toggle('on', on);
      b.style.background = on ? '#1f6feb' : '#161b22';
      b.style.color = on ? '#fff' : '#c9d1d9';
    });
    var msg = mode === 'askbid' ? 'Ask-Bid: buy/sell imbalance. Yüksək həcm + kiçik qiymət hərəkəti = absorption / trapped traders ola bilər.' :
              mode === 'delta' ? 'Delta: net buy-sell həcm.' :
              mode === 'volume' ? 'Volume: ümumi həcm.' : 'Normal şamlar.';
    console.log('[NCE Mode]', mode, msg);
  };

  // Simple absorption / trapped check using available bar data (bv/sv)
  window.checkAbsorption = function(bar) {
    if (!bar || !bar.v) return null;
    var range = bar.h - bar.l;
    var avgRange = bar.atr || range;
    var imbalance = Math.abs(bar.bv - bar.sv) / (bar.v || 1);
    if (bar.v > (bar.avgVol || bar.v) * 1.8 && range < avgRange * 0.6) {
      return imbalance > 0.6 ? 'possible trapped / absorption' : 'high volume absorption';
    }
    return null;
  };

  document.addEventListener('click', function(e){
    var btn = e.target.closest('.mode-btn');
    if (btn) window.applyChartMode(btn.dataset.mode);
  });

  console.log('NCE chart modes ready (Ask-Bid priority)');
})();
