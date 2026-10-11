// NCE Chart Modes - Ask-Bid priority
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
    var msg = mode === 'askbid' ? 'Ask-Bid rejimi aktiv: buy/sell imbalance və trapped/absorption zonaları (bv/sv əsasında təxmini)' :
              mode === 'delta' ? 'Delta rejimi: net buy-sell həcm' :
              mode === 'volume' ? 'Volume rejimi: ümumi həcm' : 'Normal şamlar';
    console.log(msg);
    // Full footprint overlay using bar.bv / bar.sv can be added here
  };
  document.addEventListener('click', function(e){
    var btn = e.target.closest('.mode-btn');
    if (btn) window.applyChartMode(btn.dataset.mode);
  });
  console.log('NCE chart modes ready');
})();
