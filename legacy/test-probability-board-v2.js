const assert = require('assert');

global.localStorage = {
  values: {},
  getItem(key) { return this.values[key] || null; },
  setItem(key, value) { this.values[key] = value; }
};
require('./nce-probability-board-v2.js');

const now = Date.now();
const trades = [];
for (let i = 0; i < 360; i += 1) {
  trades.push({T: now - (359 - i) * 1000, p: String(80000 + i * .1), q: '1', m: i % 5 === 0});
}
const klines = [];
for (let i = 0; i < 60; i += 1) {
  const open = 79900 + i;
  klines.push([now - (59 - i) * 60000, String(open), String(open + 5), String(open - 3), String(open + 2), '10', now, '800000', 100, '6', '500000']);
}
const raw = {
  premium: {markPrice: '80036', lastFundingRate: '0.0001'},
  ticker: {bidPrice: '80035.9', askPrice: '80036.1'},
  depth: {bids: [['80035.9', '20']], asks: [['80036.1', '10']]},
  trades,
  klines,
  oi: [{timestamp: now - 300000, sumOpenInterest: '100000'}, {timestamp: now, sumOpenInterest: '100100'}]
};

const value = global.NCEProbabilityBoardV2.buildFrom(raw);
assert.equal(value.boardVersion, 'NCE_PROBABILITY_BOARD_V2');
assert.deepEqual(value.horizons.map(x => x.horizonMinutes), [5, 10, 30]);
assert.deepEqual(Object.keys(value.timeframeStates), ['1s', '1m', '5m']);
for (const item of value.horizons) {
  const total = Object.values(item.distribution).reduce((a, b) => a + b, 0);
  assert(Math.abs(total - 1) < 1e-9);
  assert.equal(item.probabilityStatus, 'MODEL_ESTIMATE');
  assert.equal(item.zone.frozenAtPrediction, true);
}
assert.equal(value.rules.scoreIsProbability, false);
assert(!String(global.NCEProbabilityBoardV2.render).includes('UNAVAILABLE'));
console.log('PROBABILITY_BOARD_V2_PASS', value.currentPrice, value.horizons.map(x => x.direction).join('/'));
