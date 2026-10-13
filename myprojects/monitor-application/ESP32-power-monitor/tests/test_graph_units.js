// Exercise the page script with a minimal DOM; no browser dependencies required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8')
  .split('<script>')[1].split('</script>')[0];
function node() {
  return {value:'', checked:true, dataset:{}, children:[], attributes:{}, events:{},
    setAttribute(key, value) { this.attributes[key] = value; },
    addEventListener(name, callback) { this.events[name] = callback; },
    replaceChildren() { this.children = []; },
    append(child) { this.children.push(child); }};
}
const elements = new Map();
function el(id) {
  if(!elements.has(id)) elements.set(id, node());
  return elements.get(id);
}
el('metric').value = 'load_v';
el('range').value = '300';
const context = vm.createContext({
  document:{getElementById:el, querySelector:node, createElement:node, createElementNS:node},
  location:{hash:''}, window:{addEventListener(){}}, setTimeout(){},
  // Initial polling is left pending so the test controls the data explicitly.
  fetch:() => new Promise(() => {})
});
vm.runInContext(source, context);
const sample = {timestamp:new Date().toISOString(), channel0:{load_v:4, current_ma:-25, power_w:0.1},
  channel1:{load_v:4.2, current_ma:50, power_w:0.2}};
context.sample = sample;
vm.runInContext('history = [sample];', context);
for(const [metric, units, base] of [
  ['load_v', {V:1, mV:1000}, [4,4.2]],
  ['current_ma', {A:0.001, mA:1, 'µA':1000}, [-25,50]],
  ['power_w', {W:1, mW:1000, 'µW':1000000}, [0.1,0.2]]
]) {
  el('metric').value = metric;
  el('metric').events.change();
  assert.deepEqual(el('graph-unit').children.map(option => option.value).sort(), Object.keys(units).sort());
  for(const [unit, factor] of Object.entries(units)) {
    el('graph-unit').value = unit;
    el('graph-unit').events.change();
    const axis = el('chart').children.filter(child => child.attributes['text-anchor'] === 'end'
      && child.attributes.x === '66').map(child => Number(child.textContent));
    const min = Math.min(...base), max = Math.max(...base), pad = (max-min)*0.12;
    assert.equal(axis[0], Number(((max+pad)*factor).toPrecision(4)));
    assert.equal(axis[4], Number(((min-pad)*factor).toPrecision(4)));
    assert(el('chart-caption').textContent.includes(`(${unit})`));
    assert(el('chart').attributes['aria-label'].includes(`(${unit})`));
  }
}
el('metric').value = 'current_ma';
el('metric').events.change();
assert.equal(el('graph-unit').value, 'µA');
assert.deepEqual(sample.channel0, {load_v:4, current_ma:-25, power_w:0.1});
vm.runInContext('history = []; renderChart();', context);
assert(el('chart-caption').textContent.includes('(µA)'));
console.log('Graph units: conversions for both channels, labels, metric switching, empty data passed');

const now = Date.now();
const row = (age, value=1) => ({timestamp:new Date(now-age).toISOString(),
  channel0:{load_v:value}, channel1:{load_v:value*10}});
function draw(rows) {
  context.rows = rows;
  vm.runInContext('history=rows; renderChart();', context);
}
const paths = () => el('chart').children.filter(child => child.attributes.d);
const dots = () => el('chart').children.filter(child => child.attributes.r === '3');
el('metric').value = 'load_v';
el('metric').events.change();
el('graph-unit').value = 'V';
el('graph-unit').events.change();
el('show1').checked = false;
for(const range of [60,300,900,3600]) {
  el('range').value = String(range);
  draw([row(range*1000+1000), row(1000), row(-10000,10000)]);
  assert(el('chart-caption').textContent.includes('1 frames'));
  assert.equal(dots().length,1);
  assert(Number(dots()[0].attributes.cx) >= 76 && Number(dots()[0].attributes.cx) <= 930);
}
draw([row(-10000)]);
assert.equal(dots().length,0);
assert(el('chart-caption').textContent.includes('0 frames'));
// A failed channel splits only that channel; the other channel stays continuous.
el('show1').checked = true;
draw([row(1000), {...row(750), channel0:null}, row(500)]);
assert.equal(dots().length,2);
assert.equal(paths().length,1);
assert.equal(paths()[0].attributes.stroke,'#318a91');
el('show1').checked = false;
for(const invalid of [null, {}, {load_v:NaN}, {load_v:Infinity}]) {
  draw([row(1000), {...row(750), channel0:invalid}, row(500)]);
  assert.equal(paths().length,0);
  assert.equal(dots().length,2);
}
draw([row(5500), row(5250), row(1000), row(750)]);
assert.equal(paths().length,2);
draw([row(5000), row(1000)]);
assert.equal(dots().length,2);
draw([row(1000,0),row(500,0)]);
assert.equal(paths().length,1);
assert(!paths()[0].attributes.d.includes('NaN'));
el('show0').checked = false;
vm.runInContext('renderChart()', context);
assert.equal(paths().length,0);
assert.equal(dots().length,0);
el('show0').checked = true;

async function checkHistoryRequests() {
  const pending = [];
  context.fetch = url => new Promise((resolve,reject) => pending.push({url,resolve,reject}));
  el('range').value = '60';
  const oldRequest = vm.runInContext('refreshHistory()',context);
  el('range').value = '3600';
  const newRequest = vm.runInContext('refreshHistory()',context);
  assert(pending[0].url.endsWith('seconds=60'));
  assert(pending[1].url.endsWith('seconds=3600'));
  pending[1].resolve({ok:true,json:async()=>({samples:[row(1000),row(300000)]})});
  await newRequest;
  const caption = el('chart-caption').textContent;
  pending[0].resolve({ok:true,json:async()=>({samples:[row(1000)]})});
  await oldRequest;
  assert.equal(el('chart-caption').textContent,caption);
  assert(caption.includes('2 frames'));
  const failingOld = vm.runInContext('refreshHistory()',context);
  const successfulNew = vm.runInContext('refreshHistory()',context);
  pending[3].resolve({ok:true,json:async()=>({samples:[row(1000)]})});
  await successfulNew;
  const latestCaption = el('chart-caption').textContent;
  pending[2].reject(new Error('old request failed'));
  await failingOld;
  assert.equal(el('chart-caption').textContent,latestCaption);
  // A response received while a database clear is active must be ignored.
  const beforeClear = vm.runInContext('refreshHistory()',context);
  vm.runInContext('dataVersion++; clearingDatabase=true;',context);
  pending[4].resolve({ok:true,json:async()=>({samples:[row(1000),row(2000)]})});
  await beforeClear;
  assert.equal(el('chart-caption').textContent,latestCaption);
  vm.runInContext('clearingDatabase=false;',context);
  console.log('Graph regressions: range races, stale errors, missing readings, single points, future timestamps passed');
}
checkHistoryRequests().catch(error => {console.error(error); process.exitCode=1;});
