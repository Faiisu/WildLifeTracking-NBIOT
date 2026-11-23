// Exercise the page script with a minimal DOM; no browser dependencies required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/index.html'), 'utf8')
  .split('<script>')[1].split('</script>')[0];
function node() {
  return {value:'', checked:true, dataset:{}, children:[], attributes:{}, events:{},style:{},
    setAttribute(key, value) { this.attributes[key] = value; },
    addEventListener(name, callback) { this.events[name] = callback; },
    replaceChildren() { this.children = []; },
    append(...children) { this.children.push(...children); }};
}
const elements = new Map();
function el(id) {
  if(!elements.has(id)) elements.set(id, node());
  return elements.get(id);
}
el('metric').value = 'load_v';
el('range').value = '300';
el('average-window').value = '0';
el('zero-baseline').checked=false;
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
assert.equal(paths()[0].attributes.stroke,'#237f81');
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

function average(points, windowMs) {
  context.averagePoints=points;
  context.windowMs=windowMs;
  return JSON.parse(vm.runInContext('JSON.stringify(averageSeries(averagePoints,windowMs).map(p=>p?{time:p.time,value:p.value}:null))',context));
}
for(const seconds of [1,5,60,3600]) {
  const width=seconds*1000, boundary=Math.floor(now/width)*width;
  const points=[{time:boundary+100,value:-10},{time:boundary+350,value:30}];
  assert.deepEqual(average(points,width),[{time:boundary+225,value:10}]);
  assert.deepEqual(average([],width),[]);
  // Points on opposite sides of a boundary belong to different windows.
  assert.equal(average([{time:boundary-100,value:1},{time:boundary,value:3}],width).length,2);
}
assert.deepEqual(average([{time:0,value:1},null,{time:500,value:3}],1000),
  [{time:0,value:1},null,{time:500,value:3}]);
assert.deepEqual(average([{time:0,value:1},{time:4000,value:3}],60000),
  [{time:0,value:1},null,{time:4000,value:3}]);
const raw=[{time:0,value:2},{time:250,value:4}];
assert.deepEqual(average(raw,0),raw);
// Check averaging reaches the actual graph, including unit conversion and CH1.
const windowStart=Math.floor((now-5000)/1000)*1000;
const averagedRows=[
  {timestamp:new Date(windowStart+100).toISOString(),channel0:{load_v:2},channel1:{load_v:20}},
  {timestamp:new Date(windowStart+350).toISOString(),channel0:{load_v:4},channel1:{load_v:40}}
];
el('show1').checked=true;
el('graph-unit').value='mV';
el('graph-unit').events.change();
draw(averagedRows);
for(const seconds of [1,5,60,3600]) {
  el('average-window').value=String(seconds);
  el('average-window').events.change();
  assert.equal(dots().length,2);
  assert.equal(paths().length,0);
  const axis=el('chart').children.filter(child=>child.attributes.x==='66').map(child=>Number(child.textContent));
  assert.equal(axis[0],33240);
  assert.equal(axis[4],-240);
  assert(el('chart-caption').textContent.includes('Window average:'));
}
el('average-window').value='0';
el('average-window').events.change();
assert.equal(paths().length,2);
assert(el('chart-caption').textContent.includes('Raw readings'));
el('graph-unit').value='V';
el('graph-unit').events.change();
el('show1').checked=false;
console.log('Window averages: all durations, boundaries, partial windows, gaps, channels, units, raw mode passed');

async function checkHistoryRequests() {
  const pending = [];
  context.fetch = url => new Promise((resolve,reject) => pending.push({url,resolve,reject}));
  el('range').value = '60';
  const oldRequest = vm.runInContext('refreshHistory()',context);
  el('range').value = '3600';
  const newRequest = vm.runInContext('refreshHistory()',context);
  const bounds=url=>new URL(url,'http://localhost').searchParams;
  assert.equal(Number(bounds(pending[0].url).get('end_ms'))-Number(bounds(pending[0].url).get('start_ms')),60000);
  assert.equal(Number(bounds(pending[1].url).get('end_ms'))-Number(bounds(pending[1].url).get('start_ms')),3600000);
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
async function checkDashboard() {
  await checkHistoryRequests();
  context.fetch=()=>new Promise(()=>{});
  vm.runInContext(`changeView({start:${now-60000},end:${now}})`,context);
  el('time-back').onclick();
  assert.equal(vm.runInContext('fixedRange.end',context),now-60000);
  el('time-forward').onclick();
  assert.equal(vm.runInContext('fixedRange.end',context),now);
  el('time-from').value='2026-01-01T00:00';el('time-to').value='2026-01-03T00:00';
  el('time-apply').onclick();
  assert(el('time-message').textContent.includes('at most 24 hours'));
  el('chart').getBoundingClientRect=()=>({left:0,width:960});
  el('chart').setPointerCapture=()=>{};
  el('chart').events.pointerdown({button:0,clientX:76,pointerId:1});
  el('chart').events.pointerup({clientX:503});
  assert.equal(vm.runInContext('labelInterval.end-labelInterval.start',context),30000);
  context.labelText='<script>alert("test")</script>';
  vm.runInContext(`annotations=[{start_ms:${now-90000},end_ms:${now-30000},text:labelText}];renderChart()`,context);
  const groups=el('chart').children.filter(n=>n.children.some(c=>c.attributes.class==='annotation-band'));
  assert.equal(groups.length,1);
  const band=groups[0].children[0];
  assert.equal(Number(band.attributes.x),76);
  assert.equal(Number(band.attributes.width),427);
  assert.equal(groups[0].children[2].textContent,context.labelText);
  // Historical requests are rejected after changing the view, even at the same duration.
  const pending=[];
  context.fetch=url=>new Promise(resolve=>pending.push({url,resolve}));
  const stale=vm.runInContext('refreshHistory()',context);
  el('time-back').onclick();
  pending[0].resolve({ok:true,json:async()=>({samples:[row(1000)],annotations:[]})});
  await stale;
  assert.equal(vm.runInContext('history.length',context),0);
  pending[1].resolve({ok:true,json:async()=>({samples:[],annotations:[]})});
  await new Promise(resolve=>setImmediate(resolve));
  el('label-range').onclick();
  let posted;
  context.fetch=async(url,options)=>{
    if(options.method==='POST'){posted=JSON.parse(options.body);return {ok:true};}
    return {ok:true,json:async()=>({samples:[],annotations:[{id:'test',start_ms:posted.start_ms,end_ms:posted.end_ms,text:posted.text}]})};
  };
  el('annotation-text').value='GPS finding';
  await el('label-save').onclick();
  assert.equal(posted.text,'GPS finding');
  assert.equal(posted.end_ms-posted.start_ms,60000);
  assert(el('label-message').textContent.includes('saved'));
  context.fetch=async()=>({ok:false,status:503,text:async()=> 'database offline'});
  el('annotation-text').value='LTE init';
  await el('label-save').onclick();
  assert.equal(el('annotation-text').value,'LTE init');
  assert(el('label-message').textContent.includes('503'));
  el('time-live').onclick();
  assert.equal(vm.runInContext('fixedRange',context),null);
  console.log('Dashboard: time navigation, bounds, drag selection, clipped labels, stale views, label saving and failure passed');
}
async function checkAnalysis() {
  await checkDashboard();
  context.fetch=()=>new Promise(()=>{});
  el('chart').getBoundingClientRect=()=>({left:0,width:960});
  const base=Math.floor((now-60000)/60000)*60000+100;
  const frame=(offset,v,i,p)=>({timestamp:new Date(base+offset).toISOString(),uptime_ms:offset,
    channel0:v===null?null:{load_v:v,current_ma:i,power_w:p},channel1:{load_v:0,current_ma:0,power_w:0}});
  context.analysisRows=[frame(0,1,-10,1),frame(1000,3,10,3),frame(2000,null,null,null),frame(3000,5,20,2),frame(7000,7,40,4),frame(8000,9,60,6)];
  vm.runInContext(`fixedRange={start:${base},end:${base+10000}};labelInterval=null;history=analysisRows;renderChart();`,context);
  el('show0').checked=true;el('show1').checked=true;
  el('average-window').value='0';el('graph-unit').value='V';
  vm.runInContext('renderChart()',context);
  const stats=JSON.parse(vm.runInContext('JSON.stringify(rawStatistics(analysisRows,0,"load_v"))',context));
  assert.deepEqual(stats,{count:5,min:1,max:9,mean:5,latest:9});
  const energy=JSON.parse(vm.runInContext('JSON.stringify(integrate(analysisRows,0,"power_w"))',context));
  assert.deepEqual(energy,{value:7,duration:2});
  const charge=JSON.parse(vm.runInContext('JSON.stringify(integrate(analysisRows,0,"current_ma"))',context));
  assert.deepEqual(charge,{value:50,duration:2});
  // A zero-current channel integrates to zero rather than "no data".
  assert.equal(vm.runInContext('integrate(analysisRows,1,"current_ma").value',context),0);
  vm.runInContext(`inspectAt(${base+1000})`,context);
  assert(el('chart-tooltip').textContent.includes('CH0  3 V'));
  assert(el('chart-tooltip').textContent.includes('CH1  0 V'));
  vm.runInContext(`inspectAt(${base+2000})`,context);
  assert(el('chart-tooltip').textContent.includes('CH0  No reading'));
  vm.runInContext(`inspectAt(${base+5000})`,context);
  assert(el('chart-tooltip').textContent.includes('CH0  No reading'));
  el('graph-unit').value='mV';el('graph-unit').events.change();
  vm.runInContext(`inspectAt(${base+1000})`,context);
  assert(el('chart-tooltip').textContent.includes('CH0  3,000 mV'));
  // Average hover uses the actual averaged segment, never a neighbouring null segment.
  el('average-window').value='60';el('average-window').events.change();
  vm.runInContext(`inspectAt(${base+1000})`,context);
  assert(el('chart-tooltip').textContent.includes('CH0  2,000 mV'));
  assert(el('chart-tooltip').textContent.includes('2 samples'));
  assert.equal(el('statistics-body').children[3].children[1].textContent,'5,000');
  el('average-window').value='0';el('average-window').events.change();
  el('chart').events.keydown({key:'Home',preventDefault(){}});
  assert(el('chart-tooltip').textContent.includes('CH0  1,000 mV'));
  el('chart').events.keydown({key:'ArrowRight',preventDefault(){}});
  assert(el('chart-tooltip').textContent.includes('CH0  3,000 mV'));
  el('chart').events.keydown({key:'Escape'});
  assert.equal(el('chart-tooltip').hidden,true);
  vm.runInContext(`selectLabel(${base},${base+1000})`,context);
  assert.equal(el('analysis-duration').textContent,'1.00 s');
  assert.equal(el('statistics-body').children[0].children[1].textContent,'2');
  el('zoom-selection').onclick();
  assert.equal(vm.runInContext('fixedRange.end-fixedRange.start',context),1000);
  el('zoom-reset').onclick();
  assert.equal(vm.runInContext('fixedRange.end-fixedRange.start',context),10000);
  el('zoom-in').onclick();
  assert.equal(vm.runInContext('fixedRange.end-fixedRange.start',context),5000);
  el('zoom-out').onclick();
  assert.equal(vm.runInContext('fixedRange.end-fixedRange.start',context),10000);
  el('time-live').onclick();
  el('auto-refresh').checked=false;el('auto-refresh').events.change();
  assert.notEqual(vm.runInContext('fixedRange',context),null);
  let requests=0;context.fetch=()=>{requests++;return new Promise(()=>{});};
  await vm.runInContext('pollHistory()',context);
  assert.equal(requests,0);
  el('refresh-history').onclick();assert.equal(requests,1);
  const csv=vm.runInContext('measurementCSV(analysisRows)',context);
  assert(csv.includes('"channel0_current_ma"'));
  assert(csv.includes('"-10"'));
  context.labels=[{id:'test',text:'=HYPERLINK("danger")',start_ms:base,end_ms:base+1000}];
  const labelCSV=vm.runInContext('annotationCSV(labels)',context);
  assert(labelCSV.includes('"\'=HYPERLINK(""danger"")"'));
  // Dense graphs retain extreme spikes and explicit breaks for plotting.
  context.dense=Array.from({length:10000},(_,i)=>({time:i,value:i===5123?1000:1}));
  context.dense[6000]=null;
  const compressed=JSON.parse(vm.runInContext('JSON.stringify(envelopePoints(dense,0,10000))',context));
  assert(compressed.some(p=>p&&p.value===1000));assert(compressed.includes(null));
  assert(compressed.length<4000);
  context.longGap=Array.from({length:10000},(_,i)=>({time:i+(i>=5000?10000:0),value:1}));
  const gaps=JSON.parse(vm.runInContext('JSON.stringify(envelopePoints(longGap,0,20000))',context));
  assert(gaps.includes(null));
  assert.equal(gaps.filter(p=>p===null).length,1);
  context.labels=[{id:'a'.repeat(32),text:'GPS finding',start_ms:base,end_ms:base+1000}];
  vm.runInContext(`fixedRange={start:${base},end:${base+10000}};history=analysisRows;annotations=labels;renderChart()`,context);
  const count=vm.runInContext('history.length',context);
  context.window.confirm=()=>false;
  requests=0;
  await vm.runInContext('deleteLabel(labels[0])',context);
  assert.equal(requests,0);assert.equal(vm.runInContext('annotations.length',context),1);
  context.window.confirm=()=>true;
  let staleResolve,deleteOptions;
  context.fetch=async(url,options)=>{
    if(options.method==='DELETE'){deleteOptions=options;return {ok:true};}
    return new Promise(resolve=>{staleResolve=resolve;});
  };
  const beforeDeletion=vm.runInContext('refreshHistory()',context);
  await vm.runInContext('deleteLabel(labels[0])',context);
  assert.equal(deleteOptions.headers['X-Confirm-Label'],'delete');
  assert.equal(vm.runInContext('annotations.length',context),0);
  staleResolve({ok:true,json:async()=>({samples:context.analysisRows,annotations:context.labels})});
  await beforeDeletion;
  assert.equal(vm.runInContext('annotations.length',context),0);
  assert.equal(vm.runInContext('history.length',context),count);
  vm.runInContext('annotations=labels',context);
  context.fetch=async()=>({ok:false,status:503,text:async()=> 'offline'});
  await vm.runInContext('deleteLabel(labels[0])',context);
  assert.equal(vm.runInContext('annotations.length',context),1);
  assert(el('label-message').textContent.includes('Could not delete label'));
  console.log('Label deletion: cancellation, confirmed request, no stale resurrection, readings preserved, failure preserves label passed');
  console.log('Analysis: hover, missing readings, averages, raw statistics, gap-safe integrals, zoom, keyboard, pause, CSV safety and dense trace passed');
}
checkAnalysis().catch(error=>{console.error(error);process.exitCode=1;});
