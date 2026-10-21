"""Render the live hub (one self-contained HTML page, no external dependencies).

The page carries the collected data inline and then keeps itself current: clocks and
countdowns tick every second, and every thirty seconds it checks ``version.json`` and,
when a newer build has been published (the lab publishes one after every round),
re-renders in place from ``data.json``. Anything that moves on the page moves because the
record says so: an agent pulses only if it acted in the last twelve minutes, and the live
badge turns amber or red when the build is old.
"""

from __future__ import annotations

import json
from pathlib import Path

from hive.config import Settings, StrategyConfig
from hive.ui.collect import collect

TEMPLATE = r"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HIVE hub</title>
<style>
:root{color-scheme:light;
 --page:#f4f4f1;--surface:#fcfcfb;--surface2:#f0f0ec;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
 --s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--good:#0ca30c;--warn:#fab219;--serious:#ec835a;--crit:#d03b3b;--up:#006300;
 --ord1:#86b6ef;--ord2:#5598e7;--ord3:#2a78d6;--ord4:#1c5cab;--ord5:#0d366b;--glow:rgba(42,120,214,.18)}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;
 --page:#08090b;--surface:#121418;--surface2:#181b20;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#24272c;--axis:#363a40;--border:rgba(255,255,255,.09);
 --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--up:#0ca30c;--ord1:#184f95;--ord2:#256abf;--ord3:#3987e5;--ord4:#6da7ec;--ord5:#9ec5f4;--glow:rgba(57,135,229,.22)}}
:root[data-theme="dark"]{color-scheme:dark;
 --page:#08090b;--surface:#121418;--surface2:#181b20;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#24272c;--axis:#363a40;--border:rgba(255,255,255,.09);
 --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--up:#0ca30c;--ord1:#184f95;--ord2:#256abf;--ord3:#3987e5;--ord4:#6da7ec;--ord5:#9ec5f4;--glow:rgba(57,135,229,.22)}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:110px}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
a{color:var(--s1)}
.top{position:sticky;top:0;z-index:20;background:color-mix(in srgb,var(--page) 86%,transparent);backdrop-filter:blur(10px);border-bottom:1px solid var(--border)}
.topin{max-width:1320px;margin:0 auto;padding:10px 16px 8px;display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center}
.brand{display:flex;align-items:baseline;gap:10px}
.brand b{font-size:19px;letter-spacing:.14em}
.brand span{color:var(--ink2);font-size:12px}
.livepill{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:3px 9px;border-radius:999px;border:1px solid var(--border)}
.livepill .dot{width:8px;height:8px;border-radius:50%}
.clock{font-variant-numeric:tabular-nums;color:var(--ink2);font-size:12px}
.nav{display:flex;gap:4px;overflow-x:auto;margin-left:auto;scrollbar-width:none}
.nav a{color:var(--ink2);text-decoration:none;font-size:12px;padding:4px 9px;border-radius:6px;white-space:nowrap}
.nav a:hover{color:var(--ink);background:var(--surface2)}
.tools{display:flex;gap:8px;align-items:center;margin-left:auto}
button{font:inherit;font-size:12px;color:var(--ink2);background:transparent;border:1px solid var(--border);border-radius:6px;padding:3px 8px;cursor:pointer}
button:hover,button.on{color:var(--ink);border-color:var(--axis)}
main{max-width:1320px;margin:0 auto;padding:14px 16px 48px;display:grid;gap:14px;grid-template-columns:repeat(12,minmax(0,1fr))}
.sec{grid-column:span 12;display:flex;align-items:baseline;gap:12px;margin:14px 0 -4px}
.sec h2{font-size:13px;letter-spacing:.12em;text-transform:uppercase;color:var(--ink2);margin:0}
.sec .line{flex:1;height:1px;background:var(--border)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px;grid-column:span 12;min-width:0;overflow:hidden}
.card>.row:first-child h3{font-size:14px;margin:0;font-weight:650}
.c3{grid-column:span 3}.c4{grid-column:span 4}.c5{grid-column:span 5}.c6{grid-column:span 6}.c7{grid-column:span 7}.c8{grid-column:span 8}
@media (max-width:1000px){.c3{grid-column:span 6}.c4,.c5,.c7,.c8{grid-column:span 12}}
@media (max-width:760px){.c3,.c6{grid-column:span 12}}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.now{grid-column:span 12;display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.nowc{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px;position:relative;overflow:hidden}
.nowc .k{font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink2);display:flex;align-items:center;gap:6px}
.nowc .v{font-size:19px;font-weight:650;margin-top:4px;font-variant-numeric:tabular-nums}
.nowc .d{font-size:12px;color:var(--ink2);margin-top:2px;overflow-wrap:anywhere}
.bar{height:4px;border-radius:2px;background:var(--grid);margin-top:8px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--s1);border-radius:2px;transition:width 1s linear}
.checks{list-style:none;padding:0;margin:10px 0 0}.checks li{font-size:12px;padding:3px 0;line-height:1.35}
.checks li b{display:inline-block;width:1.2em}.checks li.ok b{color:var(--good)}.checks li.no b{color:var(--crit)}
.meter{display:flex;justify-content:space-between;font-size:12px;margin-top:8px}
.tiles{grid-column:span 12;display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.tile .label{color:var(--ink2);font-size:12px}
.tile .value{font-size:24px;font-weight:650;line-height:1.15;margin-top:4px;font-variant-numeric:tabular-nums}
.tile .delta{font-size:12px;color:var(--ink2);margin-top:2px}
.status{display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--border)}
.status .dot{width:8px;height:8px;border-radius:50%}
.banner{grid-column:span 12;background:var(--surface);border:2px solid var(--crit);border-radius:12px;padding:14px 16px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top;overflow-wrap:anywhere}
th{color:var(--ink2);font-weight:600;font-size:12px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
#roster td:first-child{min-width:190px}
td.nw,th.nw{white-space:nowrap}
code.ov{font-size:11px;color:var(--ink2)}
.muted{color:var(--muted)}.small{font-size:12px}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--ink2);margin:4px 0 6px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{display:inline-block;width:14px;height:2px;border-radius:2px}
.legend i.sq{width:10px;height:10px;border-radius:2px}
.chart{position:relative;width:100%}
.chart svg{display:block;width:100%;height:auto;overflow:visible}
.tt{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.25);white-space:nowrap;display:none;z-index:5}
.tablewrap{display:none;max-height:260px;overflow:auto;margin-top:8px}.tablewrap.show{display:block}
.pill{display:inline-block;padding:1px 7px;border-radius:999px;border:1px solid var(--border);font-size:11px;color:var(--ink2);white-space:nowrap}
.pill.good{border-color:var(--good);color:var(--good)}.pill.warn{border-color:var(--warn);color:var(--ink2)}.pill.bad{border-color:var(--crit);color:var(--crit)}
.pill.lab{border-color:var(--s3);color:var(--s3)}.pill.owner{border-color:var(--s4);color:var(--ink2)}.pill.code{border-color:var(--axis);color:var(--muted)}
.ladder{display:flex;gap:6px;flex-wrap:wrap}
.rung{flex:1;min-width:110px;border-radius:8px;padding:8px 10px;color:var(--ink);font-size:12px;opacity:.6;border-left:4px solid var(--rc);background:color-mix(in srgb,var(--rc) 22%,var(--surface))}
.rung.cur{opacity:1;outline:2px solid var(--ink)}
.agents{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px}
.agent{border:1px solid var(--border);border-radius:10px;padding:10px 12px;min-width:0;overflow-wrap:anywhere;background:var(--surface2)}
.agent .head{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:6px}
.spark{width:100%;height:36px;display:block;margin:6px 0}
.net{position:relative}
.net svg{display:block;width:100%;height:auto}
.net .lbl{font-size:11px;fill:var(--ink2)}
.net .lbl2{font-size:10px;fill:var(--muted)}
.net .edge{stroke:var(--axis);stroke-width:1.2;fill:none}
.net .edge.live{stroke:var(--good);stroke-width:1.6;stroke-dasharray:3 7;animation:flow 1.2s linear infinite}
.net .node{cursor:pointer}
.net .ring{fill:none;stroke:var(--good);stroke-width:2;opacity:0;transform-box:fill-box;transform-origin:center}
.net .working .ring{animation:pulse 2s ease-out infinite}
@keyframes flow{to{stroke-dashoffset:-20}}
@keyframes pulse{0%{opacity:.75;transform:scale(1)}100%{opacity:0;transform:scale(2.1)}}
@keyframes flash{0%{background:var(--glow)}100%{background:transparent}}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.35}}
.live .dot{animation:blink 2s infinite}
.wire{max-height:440px;overflow:auto;margin:0;padding:0;list-style:none}
.wire li{display:grid;grid-template-columns:62px 1fr;gap:2px 10px;padding:6px 6px;border-bottom:1px solid var(--grid);border-radius:6px}
.wire li.new{animation:flash 4s ease-out}
.wire .t{color:var(--muted);font-size:12px;font-variant-numeric:tabular-nums}
.wire .w{font-size:12px}
.wire .w b{color:var(--ink)}
.state{display:inline-flex;align-items:center;gap:6px;font-size:12px;white-space:nowrap}
.state .dot{width:8px;height:8px;border-radius:50%}
.state.working .dot{animation:blink 1.4s infinite}
tr.hl td{animation:flash 2.5s ease-out}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(168px,1fr));gap:10px}
.gt{border:1px solid var(--border);border-radius:10px;padding:8px 9px;cursor:pointer;background:var(--surface2);min-width:0}
.gt:hover{border-color:var(--axis)}.gt.sel{outline:2px solid var(--s1)}
.gt .n{font-size:11px;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.gt .m{display:flex;justify-content:space-between;align-items:baseline;gap:6px;margin-top:2px}
.gt .m b{font-size:15px;font-variant-numeric:tabular-nums}
.gt svg{display:block;width:100%;height:44px;margin-top:4px}
tr.labrow{cursor:pointer}tr.labrow.sel td{background:var(--glow)}tr.labrow:hover td{background:color-mix(in srgb,var(--glow) 50%,transparent)}
input.filter{font:inherit;font-size:12px;padding:5px 9px;border:1px solid var(--border);border-radius:6px;background:transparent;color:var(--ink);margin-bottom:8px;width:300px;max-width:100%}
blockquote{margin:0;padding:10px 12px;border-left:3px solid var(--s1);background:var(--surface2);border-radius:0 8px 8px 0;font-size:13px;color:var(--ink2)}
ul.plain{margin:0;padding-left:18px}ul.plain li{margin:0 0 5px}
footer{max-width:1320px;margin:0 auto;padding:0 16px 24px;color:var(--muted);font-size:12px}
.ticker{border-top:1px solid var(--border);overflow:hidden;white-space:nowrap;font-size:12px;color:var(--ink2);mask-image:linear-gradient(90deg,transparent,#000 4%,#000 96%,transparent)}
.ticker .track{display:inline-flex;gap:28px;padding:5px 0;animation:tick 90s linear infinite}
.ticker:hover .track{animation-play-state:paused}
.ticker b{color:var(--ink);font-weight:600}
@keyframes tick{from{transform:translateX(0)}to{transform:translateX(-50%)}}
.funnel{display:grid;gap:6px;margin:4px 0 10px}
.funnel .fr{display:grid;grid-template-columns:170px 1fr 44px;gap:8px;align-items:center;font-size:12px}
.funnel .fb{height:14px;border-radius:0 4px 4px 0;background:var(--s1);min-width:2px}
.knobs{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.knob{border:1px solid var(--border);border-radius:10px;padding:8px 10px;background:var(--surface2)}
.knob .kh{display:flex;justify-content:space-between;gap:6px;font-size:12px}
.knob svg{display:block;width:100%;height:auto;margin-top:4px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--border);background:var(--surface2)}
.chip b{font-variant-numeric:tabular-nums}
@media (max-width:720px){.net{display:none}.nav{margin-left:0;width:100%}.brand span{display:none}.topin{gap:6px 10px}}
@media (prefers-reduced-motion: reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<div class="top"><div class="topin">
  <div class="brand"><b>HIVE</b><span>autonomous trading firm · live hub</span></div>
  <span class="livepill" id="livepill"></span>
  <span class="clock"><span id="clock"></span> · <span id="built"></span></span>
  <nav class="nav"><a href="#now">Now</a><a href="#prop">Prop</a><a href="#agents">Firm</a><a href="#lab">Lab</a><a href="#trading">Trading</a><a href="#scouts">Scouts</a><a href="#rules">Rules</a><a href="#record">Record</a></nav>
  <div class="tools"><button id="theme">theme</button></div>
</div><div class="ticker" id="ticker"></div></div>
<main id="main"></main>
<footer id="foot"></footer>
<script id="data" type="application/json">__DATA__</script>
<script>
let D = JSON.parse(document.getElementById('data').textContent);
const $ = (s, el=document) => el.querySelector(s);
const $$ = (s, el=document) => [...el.querySelectorAll(s)];
const fmt$ = v => v==null?'—':('$'+Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}));
const fmtK = v => v==null?'—':(Math.abs(v)>=1e6?'$'+(v/1e6).toFixed(1)+'M':Math.abs(v)>=1e4?'$'+(v/1e3).toFixed(1)+'k':'$'+Math.round(v));
const pct = v => v==null||!isFinite(v)?'—':((v*100).toFixed(1)+'%');
const spct = v => v==null||!isFinite(v)?'—':((v>=0?'+':'')+(v*100).toFixed(1)+'%');
const num = (v,d=2) => v==null||!isFinite(v)?'—':Number(v).toFixed(d);
const esc = s => String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const TZ = (Intl.DateTimeFormat().resolvedOptions().timeZone)||'local';
// timestamps in the record are UTC ("2026-10-19T21:49:00+00:00", "2026-10-19 21:49" or "2026-10-19")
function parseTs(s){ if(!s) return null; let t=String(s).trim(); if(/^\d{4}-\d{2}-\d{2}$/.test(t)) t+='T00:00'; if(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(t)) t=t.replace(' ','T'); if(!/[zZ]|[+-]\d{2}:\d{2}$/.test(t)) t+='Z'; const d=new Date(t); return isNaN(d)?null:d; }
const lt=(s,withDate=true)=>{ const d=parseTs(s); if(!d) return '—'; const o={hour:'2-digit',minute:'2-digit',hour12:false}; if(withDate) Object.assign(o,{month:'short',day:'numeric'}); return d.toLocaleString(undefined,o); };
function agoTxt(s){ const d=parseTs(s); if(!d) return 'never'; const sec=Math.max(0,(Date.now()-d)/1000); if(sec<45) return 'just now'; if(sec<3600) return Math.round(sec/60)+' min ago'; if(sec<86400*2) return (sec/3600).toFixed(sec<36000?1:0)+' h ago'; return Math.round(sec/86400)+' d ago'; }
function cdTxt(s){ const d=parseTs(s); if(!d) return '—'; let sec=Math.round((d-Date.now())/1000); if(sec<=0) return 'due now'; const h=Math.floor(sec/3600), m=Math.floor(sec%3600/60), ss=sec%60; return 'in '+(h?h+'h '+m+'m':m?m+'m '+ss+'s':ss+'s'); }
const nextOf = arr => (arr||[]).find(t=>{const d=parseTs(t); return d&&d>Date.now();})||null;
const ovs = o => Object.entries(o||{}).map(([k,v])=>`${k.replace(/^(stocks|crypto|macro)\./,'')}=${v}`).join(', ');
const STATE = {working:'--good',recent:'--s1',idle:'--muted',retired:'--axis',promoted:'--s3'};
const stateDot = (st,label) => `<span class="state ${esc(st)}"><span class="dot" style="background:${css(STATE[st]||'--muted')}"></span>${esc(label||st)}</span>`;
const UI = window.__ui || (window.__ui = {labSel:null, labWin:'outofsample', filter:'', seen:null, lazy:{}, lazyCals:{}, chunks:{}});

// ---------- theme: dark by default, remembered per viewer ----------
(()=>{ let t=null; try{ t=localStorage.getItem('hive-theme'); }catch(e){} document.documentElement.setAttribute('data-theme', t||'dark'); })();
$('#theme').onclick = () => { const r=document.documentElement; const n=r.getAttribute('data-theme')==='dark'?'light':'dark'; r.setAttribute('data-theme', n); try{ localStorage.setItem('hive-theme', n); }catch(e){} render(); };

// ---------- SVG line chart (one axis, crosshair tooltip, table twin) ----------
function lineChart(el, series, opts={}) {
  if(!el) return null; el.innerHTML='';
  const longest = Math.max(8, ...series.map(s=>s.name.length));
  const W = Math.max(320, el.clientWidth||800), H = opts.height||260, m={t:12,r:Math.min(170, 14+longest*6.4),b:28,l:56};
  const xs = [...new Set(series.flatMap(s=>s.points.map(p=>p[0])))].sort();
  const xi = new Map(xs.map((x,i)=>[x,i]));
  const ys = series.flatMap(s=>s.points.map(p=>p[1])).filter(v=>v!=null && isFinite(v));
  if(!xs.length||!ys.length){ el.innerHTML='<div class="muted small">no data yet</div>'; return null; }
  let y0=Math.min(...ys), y1=Math.max(...ys); if(opts.zero) y0=Math.min(0,y0); if(y1===y0){y1=y0+1}
  const pad=(y1-y0)*0.06, allPos=Math.min(...ys)>=0, allNeg=!allPos&&Math.max(...ys)<=0; y0-=pad; y1+=pad;
  if(allPos) y0=Math.max(0,y0); if(allNeg) y1=Math.min(0,y1);
  const X = i => m.l + (xs.length>1? i/(xs.length-1):0.5)*(W-m.l-m.r);
  const Y = v => m.t + (1-(v-y0)/(y1-y0))*(H-m.t-m.b);
  const ns='http://www.w3.org/2000/svg'; const svg=document.createElementNS(ns,'svg'); svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const g=(tag,a)=>{const e=document.createElementNS(ns,tag); for(const k in a) e.setAttribute(k,a[k]); return e;};
  for(let i=0;i<=5;i++){ const v=y0+(y1-y0)*i/5; svg.appendChild(g('line',{x1:m.l,x2:W-m.r,y1:Y(v),y2:Y(v),stroke:css('--grid'),'stroke-width':1}));
    const t=g('text',{x:m.l-6,y:Y(v)+4,'text-anchor':'end','font-size':11,fill:css('--muted')}); t.textContent=opts.fmt?opts.fmt(v):num(v,0); svg.appendChild(t); }
  svg.appendChild(g('line',{x1:m.l,x2:W-m.r,y1:H-m.b,y2:H-m.b,stroke:css('--axis'),'stroke-width':1}));
  const xl = opts.xfmt || (x=>x);
  [0,Math.floor((xs.length-1)/2),xs.length-1].forEach((i,k)=>{ const t=g('text',{x:X(i),y:H-8,'text-anchor':k===0?'start':k===2?'end':'middle','font-size':11,fill:css('--muted')}); t.textContent=xl(xs[i]); svg.appendChild(t);});
  const labels=[];
  series.forEach(s=>{ const pts=s.points.filter(p=>p[1]!=null && isFinite(p[1])); if(!pts.length) return;
    if(s.area){ const d=pts.map((p,i)=>(i?'L':'M')+X(xi.get(p[0]))+','+Y(p[1])).join(' ')+` L${X(xi.get(pts[pts.length-1][0]))},${Y(0)} L${X(xi.get(pts[0][0]))},${Y(0)} Z`; svg.appendChild(g('path',{d,fill:s.color,opacity:.18})); }
    const d=pts.map((p,i)=>(i?'L':'M')+X(xi.get(p[0]))+','+Y(p[1])).join(' ');
    svg.appendChild(g('path',{d,fill:'none',stroke:s.color,'stroke-width':2,'stroke-linejoin':'round','stroke-dasharray':s.dash||''}));
    const last=pts[pts.length-1]; labels.push({x:X(xi.get(last[0]))+6,y:Y(last[1])+4,name:s.name,color:s.color}); });
  labels.sort((a,b)=>a.y-b.y); for(let i=1;i<labels.length;i++){ if(labels[i].y-labels[i-1].y<13) labels[i].y=labels[i-1].y+13; }
  labels.forEach(l=>{ const t=g('text',{x:l.x,y:l.y,'font-size':11,fill:css('--ink2')}); t.textContent=l.name; svg.appendChild(t); });
  const cross=g('line',{x1:0,x2:0,y1:m.t,y2:H-m.b,stroke:css('--axis'),'stroke-width':1,visibility:'hidden'}); svg.appendChild(cross);
  const tip=document.createElement('div'); tip.className='tt'; el.appendChild(svg); el.appendChild(tip);
  const hit=g('rect',{x:m.l,y:m.t,width:W-m.l-m.r,height:H-m.t-m.b,fill:'transparent'}); svg.appendChild(hit);
  hit.addEventListener('mousemove',ev=>{ const r=svg.getBoundingClientRect(); const px=(ev.clientX-r.left)*W/r.width; const ii=Math.max(0,Math.min(xs.length-1,Math.round(((px-m.l)/(W-m.l-m.r))*(xs.length-1)))); const x=xs[ii]; cross.setAttribute('x1',X(ii)); cross.setAttribute('x2',X(ii)); cross.setAttribute('visibility','visible');
    const rows=series.map(s=>{const p=s.points.find(q=>q[0]===x); return p&&p[1]!=null?`<div><i style="display:inline-block;width:10px;height:2px;background:${s.color};vertical-align:middle;margin-right:6px"></i>${esc(s.name)}: <b>${opts.fmt?opts.fmt(p[1]):num(p[1])}</b></div>`:''}).join('');
    tip.innerHTML=`<div class="muted">${esc(xl(x))}</div>${rows}`; tip.style.display='block'; tip.style.left=Math.min((ev.clientX-r.left)+12,r.width-190)+'px'; tip.style.top=(ev.clientY-r.top-10)+'px'; });
  hit.addEventListener('mouseleave',()=>{cross.setAttribute('visibility','hidden'); tip.style.display='none';});
  const tw=document.createElement('div'); tw.className='tablewrap';
  tw.innerHTML=`<table><thead><tr><th>${esc(opts.xname||'date')}</th>${series.map(s=>`<th class="num">${esc(s.name)}</th>`).join('')}</tr></thead><tbody>${xs.map(x=>`<tr><td>${esc(xl(x))}</td>${series.map(s=>{const p=s.points.find(q=>q[0]===x); return `<td class="num">${p&&p[1]!=null?(opts.fmt?opts.fmt(p[1]):num(p[1])):'—'}</td>`}).join('')}</tr>`).join('')}</tbody></table>`; el.appendChild(tw);
  return tw;
}

// ---------- SVG scatter (one mark per test; hover tooltip; click selects) ----------
function scatterChart(el, pts, opts={}){
  if(!el) return; el.innerHTML='';
  const W=Math.max(320, el.clientWidth||600), H=opts.height||270, m={t:14,r:16,b:30,l:52};
  const vals=pts.filter(p=>p.x!=null&&p.y!=null&&isFinite(p.x)&&isFinite(p.y));
  if(!vals.length){ el.innerHTML='<div class="muted small">no tests yet</div>'; return; }
  let x0=Math.min(0,...vals.map(p=>p.x)), x1=Math.max(0,...vals.map(p=>p.x)), y0=Math.min(0,...vals.map(p=>p.y)), y1=Math.max(0,...vals.map(p=>p.y));
  if(x1===x0) x1=x0+0.1; if(y1===y0) y1=y0+0.1;
  const px=(x1-x0)*0.08, py=(y1-y0)*0.1; x0-=px; x1+=px; y0-=py; y1+=py;
  const X=v=>m.l+(v-x0)/(x1-x0)*(W-m.l-m.r), Y=v=>m.t+(1-(v-y0)/(y1-y0))*(H-m.t-m.b);
  const ns='http://www.w3.org/2000/svg'; const svg=document.createElementNS(ns,'svg'); svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const g=(tag,a)=>{const e=document.createElementNS(ns,tag); for(const k in a) e.setAttribute(k,a[k]); return e;};
  for(let i=0;i<=4;i++){ const v=y0+(y1-y0)*i/4; svg.appendChild(g('line',{x1:m.l,x2:W-m.r,y1:Y(v),y2:Y(v),stroke:css('--grid'),'stroke-width':1})); const t=g('text',{x:m.l-6,y:Y(v)+4,'text-anchor':'end','font-size':11,fill:css('--muted')}); t.textContent=pct(v); svg.appendChild(t); }
  for(let i=0;i<=4;i++){ const v=x0+(x1-x0)*i/4; const t=g('text',{x:X(v),y:H-8,'text-anchor':i===0?'start':i===4?'end':'middle','font-size':11,fill:css('--muted')}); t.textContent=pct(v); svg.appendChild(t); }
  if(y0<0&&y1>0) svg.appendChild(g('line',{x1:m.l,x2:W-m.r,y1:Y(0),y2:Y(0),stroke:css('--axis'),'stroke-width':1}));
  svg.appendChild(g('line',{x1:m.l,x2:W-m.r,y1:H-m.b,y2:H-m.b,stroke:css('--axis'),'stroke-width':1}));
  const xl=g('text',{x:W-m.r,y:H-m.b-6,'text-anchor':'end','font-size':11,fill:css('--ink2')}); xl.textContent=opts.xlabel||''; svg.appendChild(xl);
  const yl=g('text',{x:m.l+6,y:m.t+10,'font-size':11,fill:css('--ink2')}); yl.textContent=opts.ylabel||''; svg.appendChild(yl);
  const tip=document.createElement('div'); tip.className='tt';
  vals.forEach(p=>{ let mk; const cx=X(p.x), cy=Y(p.y);
    if(p.shape==='diamond'){ const r=7; mk=g('path',{d:`M${cx},${cy-r} L${cx+r},${cy} L${cx},${cy+r} L${cx-r},${cy} Z`,fill:p.color,stroke:css('--surface'),'stroke-width':2}); }
    else { mk=g('circle',{cx,cy,r:p.selected?6.5:4.5,fill:p.filled?p.color:css('--surface'),stroke:p.selected?css('--ink'):p.color,'stroke-width':2}); }
    mk.style.cursor=p.onclick?'pointer':'default';
    mk.addEventListener('mousemove',ev=>{ const r=svg.getBoundingClientRect(); tip.innerHTML=p.tip||''; tip.style.display='block'; tip.style.left=Math.max(0,Math.min(ev.clientX-r.left+12, r.width-250))+'px'; tip.style.top=(ev.clientY-r.top-10)+'px'; });
    mk.addEventListener('mouseleave',()=>{tip.style.display='none';});
    if(p.onclick) mk.addEventListener('click',p.onclick);
    svg.appendChild(mk); });
  el.appendChild(svg); el.appendChild(tip);
}

// ---------- stacked bars (two series, 2px surface gap, per-bar tooltip) ----------
function barChart(el, rows, series, opts={}){
  if(!el) return; el.innerHTML='';
  if(!rows.length){ el.innerHTML='<div class="muted small">no data yet</div>'; return; }
  const W=Math.max(320, el.clientWidth||600), H=opts.height||200, m={t:10,r:10,b:28,l:40};
  const tot=rows.map(r=>series.reduce((a,_,i)=>a+(r[i+1]||0),0)); const y1=Math.max(1,...tot)*1.08;
  const bw=(W-m.l-m.r)/rows.length; const X=i=>m.l+i*bw; const Y=v=>m.t+(1-v/y1)*(H-m.t-m.b);
  const ns='http://www.w3.org/2000/svg'; const svg=document.createElementNS(ns,'svg'); svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const g=(tag,a)=>{const e=document.createElementNS(ns,tag); for(const k in a) e.setAttribute(k,a[k]); return e;};
  for(let i=0;i<=4;i++){ const v=y1*i/4; svg.appendChild(g('line',{x1:m.l,x2:W-m.r,y1:Y(v),y2:Y(v),stroke:css('--grid'),'stroke-width':1})); const t=g('text',{x:m.l-6,y:Y(v)+4,'text-anchor':'end','font-size':11,fill:css('--muted')}); t.textContent=Math.round(v); svg.appendChild(t); }
  const tip=document.createElement('div'); tip.className='tt';
  rows.forEach((r,i)=>{ let base=0; series.forEach((s,k)=>{ const v=r[k+1]||0; if(!v) return; const y=Y(base+v), h=Y(base)-Y(base+v); svg.appendChild(g('rect',{x:X(i)+1,y:y+1,width:Math.max(1,bw-2),height:Math.max(0,h-2),rx:Math.min(4,bw/4),fill:s.color})); base+=v; });
    const hit=g('rect',{x:X(i),y:m.t,width:bw,height:H-m.t-m.b,fill:'transparent'}); hit.addEventListener('mousemove',ev=>{ const rr=svg.getBoundingClientRect(); tip.innerHTML=`<div class="muted">${esc(opts.xfmt?opts.xfmt(r[0]):r[0])}</div>`+series.map((s,k)=>`<div><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${s.color};margin-right:6px;vertical-align:middle"></i>${esc(s.name)}: <b>${r[k+1]||0}</b></div>`).join(''); tip.style.display='block'; tip.style.left=Math.min(ev.clientX-rr.left+12, rr.width-160)+'px'; tip.style.top=(ev.clientY-rr.top-10)+'px'; }); hit.addEventListener('mouseleave',()=>tip.style.display='none'); svg.appendChild(hit); });
  svg.appendChild(g('line',{x1:m.l,x2:W-m.r,y1:H-m.b,y2:H-m.b,stroke:css('--axis'),'stroke-width':1}));
  [0,rows.length-1].forEach((i,k)=>{ const t=g('text',{x:k?W-m.r:m.l,y:H-8,'text-anchor':k?'end':'start','font-size':11,fill:css('--muted')}); t.textContent=opts.xfmt?opts.xfmt(rows[i][0]):rows[i][0]; svg.appendChild(t); });
  el.appendChild(svg); el.appendChild(tip);
}
const legend = series => `<div class="legend">${series.map(s=>`<span><i class="${s.sq?'sq':''}" style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>`;
const card = (title, cls, inner, id, extra='') => `<section class="card ${cls||''}" ${extra}><div class="row" style="margin-bottom:8px"><h3>${title}</h3><div class="tools">${id?`<button data-table="${id}">table</button>`:''}</div></div>${inner}</section>`;
const sec = (id, title, sub='') => `<div class="sec" id="${id}"><h2>${esc(title)}</h2><span class="small muted">${esc(sub)}</span><span class="line"></span></div>`;
const statusPill = (txt, col) => `<span class="status"><span class="dot" style="background:${col}"></span>${esc(txt)}</span>`;
// ---------- lab curves: inline (compact, shared calendars) or fetched on demand ----------
function curveOf(id, win){
  const LB=D.lab||{}; const cv=((LB.curves||{})[id]||UI.lazy[id]||{})[win]; if(!cv) return null;
  if(cv.dates) return cv;
  const cal=(LB.cals||{})[cv.cal]||UI.lazyCals[cv.cal]; if(!cal) return null;
  return {dates:cal.dates||[], equity:cv.equity||[], benchmark:cal.benchmark||[]};
}
async function fetchCurve(t){
  const key=(t.ts||'').slice(0,13).replace(/-/g,'').replace('T','');
  if(!(key in UI.chunks)){ try{ const r=await fetch(`lab/h-${key}.json`,{cache:'no-store'}); UI.chunks[key]=r.ok?await r.json():{}; }catch(e){ UI.chunks[key]={}; } }
  const cv=UI.chunks[key][t.id]; if(!cv) return false;
  UI.lazy[t.id]=cv;
  for(const w of Object.values(cv)){ if(w&&w.cal&&!((D.lab||{}).cals||{})[w.cal]&&!UI.lazyCals[w.cal]){ try{ const r=await fetch(`lab/cal/${w.cal}.json`,{cache:'no-store'}); if(r.ok) UI.lazyCals[w.cal]=await r.json(); }catch(e){} } }
  return true;
}
const parentCurve = (sleeve, win) => ((((D.lab||{}).parents||{})[sleeve]||{}).curves||{})[win]||null;
function miniSvg(cv, par){
  if(!cv||!cv.dates||cv.dates.length<2) return '<svg viewBox="0 0 160 44"></svg>';
  const t0=Date.parse(cv.dates[0]), t1=Date.parse(cv.dates[cv.dates.length-1]); const lg=v=>v>0?Math.log10(v):null;
  const all=[...cv.equity.map(lg), ...(par?par.equity.map(lg):[])].filter(v=>v!=null); const lo=Math.min(...all), hi=Math.max(...all);
  const pts=(dates,eq)=>dates.map((d,i)=>{ const x=(Date.parse(d)-t0)/((t1-t0)||1)*160; const v=lg(eq[i]); return (x<0||x>160||v==null)?null:`${x.toFixed(1)},${(42-(hi===lo?20:(v-lo)/(hi-lo)*40)).toFixed(1)}`; }).filter(Boolean).join(' ');
  return `<svg viewBox="0 0 160 44" preserveAspectRatio="none">${par?`<polyline fill="none" stroke="${css('--axis')}" stroke-width="1.5" points="${pts(par.dates,par.equity)}"/>`:''}<polyline fill="none" stroke="${css('--s1')}" stroke-width="2" points="${pts(cv.dates,cv.equity)}"/></svg>`;
}

// ---------- the firm as a network: honest liveness from the record ----------
function drawNetwork(el, agents){
  if(!el) return; el.innerHTML='';
  const W=900, H=470, cx=450, cy=188; const ns='http://www.w3.org/2000/svg';
  const svg=document.createElementNS(ns,'svg'); svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const g=(tag,a,parent)=>{const e=document.createElementNS(ns,tag); for(const k in a) e.setAttribute(k,a[k]); (parent||svg).appendChild(e); return e;};
  const core=agents.find(a=>a.id==='operator'); const ring1=agents.filter(a=>a.id!=='operator'&&a.group!=='sub-agent'); const ring2=agents.filter(a=>a.group==='sub-agent');
  const pos={}; pos.operator=[cx,cy];
  ring1.forEach((a,i)=>{ const ang=-Math.PI/2+i/ring1.length*Math.PI*2; pos[a.id]=[cx+310*Math.cos(ang), cy+128*Math.sin(ang)]; });
  ring2.forEach((a,i)=>{ pos[a.id]=[ring2.length>1?70+i*(760/(ring2.length-1)):cx, 412]; });   // sub-agents: their own row
  const edges=g('g',{});
  ring1.forEach(a=>g('path',{d:`M${cx},${cy} L${pos[a.id][0]},${pos[a.id][1]}`,class:'edge'+(a.state==='working'?' live':'')},edges));
  const labId=ring1.find(a=>a.id==='lab')?'lab':'operator';
  ring2.forEach(a=>{ const [x0,y0]=pos[labId], [x1,y1]=pos[a.id]; g('path',{d:`M${x0},${y0} C${x0},${(y0+y1)/2+60} ${x1},${(y0+y1)/2} ${x1},${y1}`,class:'edge'+(a.state==='working'?' live':'')},edges); });
  const tip=document.createElement('div'); tip.className='tt';
  [core,...ring1,...ring2].filter(Boolean).forEach(a=>{ const [x,y]=pos[a.id]; const r=a.id==='operator'?26:a.group==='sub-agent'?13:16;
    const col=css(STATE[a.state]||'--muted'); const node=g('g',{class:'node '+a.state,transform:`translate(${x},${y})`});
    g('circle',{r:r,class:'ring',stroke:col},node);
    g('circle',{r:r,fill:css('--surface'),stroke:col,'stroke-width':a.state==='idle'?1.5:3,'stroke-dasharray':a.state==='retired'?'3 3':''},node);
    g('circle',{r:a.id==='operator'?7:4,fill:col},node);
    const nm=g('text',{y:r+14,'text-anchor':'middle',class:'lbl'},node); const lim=a.group==='sub-agent'?17:22; nm.textContent=(a.name||'').length>lim?(a.name||'').slice(0,lim-1)+'…':a.name;
    const ag=g('text',{y:r+26,'text-anchor':'middle',class:'lbl2','data-ago':a.last_ts||''},node); ag.textContent=a.last_ts?agoTxt(a.last_ts):'never';
    node.addEventListener('mousemove',ev=>{ const rr=svg.getBoundingClientRect(); tip.innerHTML=`<div><b>${esc(a.name)}</b> · ${esc(a.state)}</div><div class="muted">${esc(a.role)}</div><div>${esc(a.doing||'')}</div><div class="muted">last ${esc(agoTxt(a.last_ts))}${a.next?` · next ${esc(cdTxt(a.next))}`:''}</div>`; tip.style.display='block'; tip.style.whiteSpace='normal'; tip.style.maxWidth='320px'; tip.style.left=Math.min(ev.clientX-rr.left+14, rr.width-330)+'px'; tip.style.top=(ev.clientY-rr.top+10)+'px'; });
    node.addEventListener('mouseleave',()=>tip.style.display='none');
    node.addEventListener('click',()=>{ const row=document.getElementById('ag-'+a.id); if(row){ row.scrollIntoView({block:'center'}); row.classList.remove('hl'); void row.offsetWidth; row.classList.add('hl'); } });
  });
  el.appendChild(svg); el.appendChild(tip);
}
function render(){
  const y=scrollY;
  const S=D.survival||{}, st=S.status||{}, E=D.equity||{dates:[],primary_net:[],shadow:{},cum_ai_cost:[]}, L=D.latest||{}, P=(L.primary||{}), H=D.hive||{};
  const LB=D.lab||{tests:[],curves:{},cals:{},parents:{},queue:[]}; const LT=LB.tests||[]; const C=D.config||{}; const OP=C.operator||{};
  const HB=D.heartbeat||{}; const AG=D.agents||[]; const SC=D.schedule||{}; const LS=LB.status||{}; const CDx=D.children_detail||{};
  const tiers=S.tiers||[]; const tier=tiers.find(t=>t.name===st.tier)||{};
  const start=S.starting_bankroll, net=st.equity_net, gross=st.equity_gross; const days=(E.dates||[]).length;
  const retTot=(net!=null&&start)? net/start-1 : null; const burn=S.ai_cost_total||0;
  const byId=Object.fromEntries(LT.map(t=>[t.id,t]));
  // header: build age is the honest liveness signal
  const age=(Date.now()-(Date.parse(D.generated_at)||Date.now()))/60000;
  const live=age<15?['LIVE','--good']:age<75?['DELAYED','--warn']:['STALE','--crit'];
  $('#livepill').innerHTML=`<span class="dot" style="background:${css(live[1])}"></span>${live[0]}`; $('#livepill').className='livepill'+(live[0]==='LIVE'?' live':'');
  $('#built').innerHTML=`build <span data-ago="${esc(D.generated_at)}">${agoTxt(D.generated_at)}</span> · ${esc(TZ)}`;
  const TKS=(D.timeline||[]).slice(-24).reverse();
  if(TKS.length){ const items=TKS.map(e=>`<span><span class="muted">${esc(lt(e.ts,false))}</span> <b>${esc(e.who)}</b> ${esc(String(e.text||'').slice(0,110))}</span>`).join(''); $('#ticker').innerHTML=`<div class="track">${items}${items}</div>`; }
  let html='';
  if(S.death){ html+=`<div class="banner"><b>The firm died on ${esc(S.death.date)}</b>: ${esc(S.death.reason)}. Final equity ${fmt$(S.death.equity)}. No restart.</div>`; }

  // ---------- NOW ----------
  const running=LS.state==='running';
  const roundTxt=LS.round&&LS.rounds?`round ${LS.round} of ${LS.rounds}`:'';
  const lastT=LT.find(t=>t.by!=='parent'&&t.by!=='seat');
  const trA=AG.find(a=>a.id==='trench-scout'||a.name==='Trench scout');
  const nextDaily=nextOf(SC.daily), nextWeekly=nextOf(SC.weekly);
  html+=sec('now','Right now','what every part of the firm is doing, from the record');
  html+=`<div class="now">
   <div class="nowc"><div class="k">${stateDot(running?'working':(LB.last_ts&&(Date.now()-parseTs(LB.last_ts))<15*60000?'working':'recent'),'')}Strategy lab</div>
     <div class="v">${running?`Backtesting · ${esc(roundTxt)}`:esc(roundTxt?`${roundTxt} done`:'between sessions')}</div>
     <div class="d">${LB.n_today||0} backtests today · ${LB.n_tests||0} on record · ${LB.n_passed||0} beat the parent</div>
     <div class="d">${lastT?`last: <b>${esc((ovs(lastT.overrides)||lastT.name||'').slice(0,80))}${(ovs(lastT.overrides)||'').length>80?'…':''}</b> → ${lastT.oos_cagr==null?'no result':spct(lastT.oos_cagr)} <span data-ago="${esc(lastT.ts)}">${agoTxt(lastT.ts)}</span>`:'no test yet'}</div>
     <div class="bar"><i data-prog-start="${esc(running?LS.started||'':'')}" data-prog-min="${esc(LS.minutes||7)}" style="width:${running?0:100}%"></i></div></div>
   <div class="nowc"><div class="k">${stateDot('recent','')}Trading cycle</div>
     <div class="v"><span data-cd="${esc(nextDaily||'')}">${cdTxt(nextDaily)}</span></div>
     <div class="d">next cycle ${esc(lt(nextDaily))} · last ${esc(P.run_date||'—')}: ${(P.decisions||[]).length} decisions, ${(P.orders||[]).length} orders, ${(P.exits||[]).length} exits</div>
     <div class="d">desks: news, numbers, sentiment, debate, portfolio manager</div></div>
   <div class="nowc"><div class="k">${stateDot(HB.ts&&(Date.now()-parseTs(HB.ts))<15*60000?'working':'recent','')}Live book (${esc(HB.broker||'broker')})</div>
     <div class="v">${fmt$(HB.equity)}</div>
     <div class="d">${(HB.positions||[]).length} positions · cash ${fmt$(HB.cash)} · market ${HB.market_open?'open':'closed'}</div>
     <div class="d">heartbeat <span data-ago="${esc(HB.ts||'')}">${agoTxt(HB.ts)}</span></div></div>
   <div class="nowc"><div class="k">${stateDot(trA?trA.state:'idle','')}Trenches (paper)</div>
     <div class="v">${trA?esc(trA.doing.split(',').slice(-1)[0].trim()):'starts with the next lab round'}</div>
     <div class="d">${trA?esc(trA.doing.split(',').slice(0,3).join(',')):''}</div>
     <div class="d">${trA&&trA.last_ts?`scanned <span data-ago="${esc(trA.last_ts)}">${agoTxt(trA.last_ts)}</span>`:''}</div></div>
   <div class="nowc"><div class="k">${stateDot((AG.find(a=>a.id==='social')||{}).state||'idle','')}Social pulse</div>
     <div class="v">${(()=>{const c=((D.social||{}).cross||[]).slice(0,4).map(r=>esc(r.symbol)); return c.length?c.join(' · '):'—';})()}</div>
     <div class="d">talked about on two or more sources · ${((D.social||{}).stocktwits||[]).length} trending on StockTwits · X ${esc(String((D.social||{}).x_status||'not connected').split(':')[0])}</div>
     <div class="d">${(D.social||{}).ts?`<span data-ago="${esc(D.social.ts)}">${agoTxt(D.social.ts)}</span>`:'first pulse at the next lab round'}</div></div>
   <div class="nowc"><div class="k">${stateDot((AG.find(a=>a.id==='hyperliquid-desk')||{}).state||'idle','')}Hyperliquid desk (paper)</div>
     <div class="v">${(()=>{const l=((CDx['hyperliquid-desk']||{}).latest)||{}; return l.equity!=null?fmt$(l.equity):'first cycle tonight';})()}</div>
     <div class="d">${(()=>{const l=((CDx['hyperliquid-desk']||{}).latest)||{}; return l.ts?`${l.n_tradable||0} of ${l.n_listed||0} perps tradable · ${(l.positions||[]).length} open · funding ${fmt$(l.funding_paid_total||0)}`:'public prices only: no account, no keys, no orders';})()}</div></div>
   <div class="nowc"><div class="k">${stateDot('idle','')}Operator, auditor, allocator</div>
     <div class="v"><span data-cd="${esc(nextWeekly||'')}">${cdTxt(nextWeekly)}</span></div>
     <div class="d">weekly research, review and capital allocation · ${esc(lt(nextWeekly))}</div></div>
  </div>`;

  // ---------- tiles ----------
  const regime=(P.regime||{}).state||'—'; const regCol={green:css('--good'),yellow:css('--warn'),red:css('--crit')}[regime]||css('--muted');
  const tierCol={hospice:css('--crit'),survival:css('--serious'),base:css('--s1'),growth:css('--s3'),hive:css('--good')}[st.tier]||css('--muted');
  const activeAgents=(H.children||[]).filter(c=>c.status==='shadow'||c.status==='live');
  const seatsUsed=activeAgents.filter(c=>c.kind!=='scout'&&c.kind!=='baseline').length;
  html+=`<div class="tiles">
   <div class="tile"><div class="label">Net equity (after AI costs)</div><div class="value">${fmt$(net)}</div><div class="delta">${retTot==null?'':spct(retTot)+' since start'} · ${esc(D.mode||'paper')}</div></div>
   <div class="tile"><div class="label">AI costs paid from the bankroll</div><div class="value">${fmt$(burn)}</div><div class="delta">today ${fmt$(st.ai_cost_today)} of ${fmt$(st.budget_today)}</div></div>
   <div class="tile"><div class="label">Tier</div><div class="value">${esc(st.tier||'—')}</div><div class="delta">${statusPill('runway '+(st.runway_days==null?'∞':Math.round(st.runway_days)+' d'), tierCol)}</div></div>
   <div class="tile"><div class="label">Backtests</div><div class="value">${LB.n_tests||0}</div><div class="delta">${LB.n_today||0} today · ${LB.n_passed||0} beat the parent</div></div>
   <div class="tile"><div class="label">Sub-agents</div><div class="value">${seatsUsed} / ${tier.max_children??'—'}</div><div class="delta">seats used · ${(LB.waiting||[]).length} waiting · ${activeAgents.length-seatsUsed} scouts/baseline</div></div>
   <div class="tile"><div class="label">Regime</div><div class="value" style="text-transform:capitalize">${esc(regime)}</div><div class="delta">${statusPill(((P.regime||{}).crash_guard?'crash guard on':'crash guard off'), regCol)}</div></div>
   <div class="tile"><div class="label">Prop-firm readiness</div><div class="value">${(()=>{const b=((D.prop||{}).books||[]).find(b=>b.kind==='primary'); return (D.prop||{}).ready&&(D.prop.ready||[]).length?'READY':b?`${b.n_ok} / ${(b.checks||[]).length}`:'—';})()}</div><div class="delta">${(D.prop||{}).ready&&(D.prop.ready||[]).length?`${D.prop.ready.length} book(s) cleared every check`:'checks cleared by the primary book'}</div></div>
   <div class="tile"><div class="label">Integrity</div><div class="value">${S.chain_ok&&!((D.journal||{}).broken_chains||[]).length?'✓ intact':'✗ broken'}</div><div class="delta">hash-chained ledger and journals · ${(D.journal||{}).anomalies_30d||0} bad prints cut (30 d)</div></div>
  </div>`;

  // ---------- PROP ----------
  const PR=D.prop||{}, PRr=PR.rules||{}, books=PR.books||[];
  const pc0=v=>v==null||!isFinite(v)?'—':Math.round(v*100)+'%';
  html+=sec('prop','Prop-firm readiness',`what the lab optimises for: pass a ${pc0(PRr.profit_target_pct)} target without a ${pc0(PRr.max_daily_loss_pct)} day or a ${pc0(PRr.max_total_loss_pct)} total loss · checked every daily cycle · a GitHub issue opens the first time a book clears every check`);
  const meter=(label,v,max,col)=>`<div class="meter"><span>${label}</span><b class="num">${v}</b></div><div class="bar" style="margin-top:3px"><i style="width:${Math.max(0,Math.min(100,max*100)).toFixed(1)}%;background:${col}"></i></div>`;
  const bookCard=b=>{ const f=b.forward||{}; const tgt=PRr.profit_target_pct||0.1, dl=PRr.max_daily_loss_pct||0.05, tl=PRr.max_total_loss_pct||0.1;
    const bt=b.backtest_by_sleeve&&Object.keys(b.backtest_by_sleeve).length?Object.entries(b.backtest_by_sleeve).map(([k,v])=>`${esc(k)} ${pc0(v)}`).join(' · '):pc0(b.backtest_pass_rate);
    return card(`${b.ready?'<span class="pill good">READY</span> ':''}${esc(b.name)}`,'c4',
      `<div class="small muted">${esc(b.kind)} · ${esc(b.sleeve)} · paper record since ${esc(f.start||b.since||'—')} · ${f.days||0} trading days · ${b.n_ok} of ${(b.checks||[]).length} checks</div>
       ${meter(`toward the ${pc0(tgt)} target`,spct(f.ret),Math.max(f.ret||0,0)/tgt,css('--good'))}
       ${meter(`deepest loss (limit ${pc0(tl)})`,pct(f.max_loss),-(f.max_loss||0)/tl,css('--serious'))}
       ${meter(`worst day (limit ${pc0(dl)})`,pct(f.worst_day),-(f.worst_day||0)/dl,css('--warn'))}
       ${meter('days of record',`${f.days||0} / ${PRr.ready_min_forward_days||30}`,(f.days||0)/(PRr.ready_min_forward_days||30),css('--s1'))}
       <div class="small muted" style="margin-top:6px">simulated out-of-sample pass rate: <b>${bt}</b></div>
       <ul class="checks">${(b.checks||[]).map(c=>`<li class="${c.ok?'ok':'no'}"><b>${c.ok?'✓':'✗'}</b>${esc(c.check)} <span class="muted">(${esc(c.value)})</span></li>`).join('')}</ul>`); };
  html+=books.length?books.slice(0,6).map(bookCard).join(''):card('No readiness check yet','',`<div class="small">The first check runs with the next daily cycle (${esc(lt(nextDaily))}). Each book's paper record is read as a prop-firm evaluation; a book is ready when it has ${PRr.ready_min_forward_days||30} trading days, never broke a loss limit, reached the target, and its strategy passes at least ${pc0(PRr.ready_min_backtest_pass_rate)} of simulated evaluations out of sample.</div>`);

  // ---------- THE FIRM ----------
  html+=sec('agents','The firm, live','a node pulses only when the record shows it acted in the last twelve minutes');
  const working=AG.filter(a=>a.state==='working').length, recent=AG.filter(a=>a.state==='recent').length;
  html+=card(`${AG.length} agents · ${working} working now · ${recent} active in the last day`,'c7',`<div class="legend"><span>${stateDot('working')}</span><span>${stateDot('recent','active in the last 26 h')}</span><span>${stateDot('idle')}</span><span>${stateDot('retired')}</span></div><div class="net" id="net"></div><div style="max-height:300px;overflow:auto;margin-top:6px"><table id="roster"><thead><tr><th>agent</th><th>state</th><th class="nw">last active</th><th>doing / last did</th><th class="nw">next</th></tr></thead><tbody>${AG.map(a=>`<tr id="ag-${esc(a.id)}"><td><b>${esc(a.name)}</b><div class="small muted">${esc(a.role)}</div></td><td>${stateDot(a.state)}</td><td class="nw small"><span data-ago="${esc(a.last_ts||'')}">${agoTxt(a.last_ts)}</span></td><td class="small">${esc(a.doing||'')}</td><td class="nw small">${a.next&&parseTs(a.next)?`<span data-cd="${esc(a.next)}">${cdTxt(a.next)}</span>`:esc(a.next||a.cadence||'')}</td></tr>`).join('')}</tbody></table></div>`);
  const TL=(D.timeline||[]).slice().reverse().slice(0,60);
  const keyOf=e=>`${e.ts}|${e.who}|${e.text}`; const first=UI.seen===null; const seen=UI.seen||new Set();
  html+=card('Live wire','c5',`<ul class="wire">${TL.map(e=>{ const k=keyOf(e); const isNew=!first&&!seen.has(k); return `<li class="${isNew?'new':''}"><span class="t">${esc(lt(e.ts,false))}</span><span class="w"><b>${esc(e.who)}</b> <span class="pill">${esc(e.kind)}</span> ${esc(e.text)}</span></li>`; }).join('')||'<li class="muted">nothing yet</li>'}</ul>`);
  UI.seen=new Set(TL.map(keyOf));

  // ---------- THE LAB ----------
  html+=sec('lab','Strategy lab','every backtest the firm has run, newest first');
  if(!UI.labSel||!byId[UI.labSel]) UI.labSel=(LT.find(t=>t.by!=='parent'&&t.by!=='seat'&&(LB.curves||{})[t.id])||{}).id||null;
  const stg=LB.by_stage||{}; const lq=LB.queue||[];
  html+=card(`${LB.n_tests||0} backtests · ${LB.n_passed||0} beat the parent · ${LB.n_today||0} today`,'',`<div class="small muted" style="margin-bottom:6px">The lab backtests the next cell of the strategy space against the parent on the same data with the same costs: the grid first, then refinements around the best cells, then mutations of the best cells, which never run out. In-sample ${esc(OP.insample_start||'')} to ${esc(OP.insample_end||'')}, out-of-sample from ${esc(OP.outofsample_start||'')}. A cell that beats the parent takes a paper seat and must then out-grow it live on paper before promotion. No LLM; it costs the firm nothing.</div>
   <div class="row small"><b>Status:</b> ${running?`${stateDot('working','running')} ${esc(roundTxt)} since ${esc(lt(LS.started))}`:LS.state?`${esc(roundTxt||'round')} finished ${esc(lt(LS.finished))} · ${LS.cells??0} cells`:'no session yet'} <span class="muted">· queued: ${stg.rescore?stg.rescore+' prop rescores, ':''}${stg.sweep||0} grid, ${stg.refine||0} refinements, ${stg.evolve||0} mutations (regenerated every round)</span></div>
   <div class="small" style="margin-top:4px"><b>Next up:</b> ${lq.slice(0,5).map(esc).join(' · ')||'—'}</div>
   ${(LB.waiting||[]).length?`<div class="small" style="margin-top:4px"><b>Waiting for a seat</b> (best first): ${LB.waiting.slice(0,6).map(w=>`${esc(w.name)} (${w.oos_prop!=null?Math.round(w.oos_prop*100)+'% prop pass, ':''}${spct(w.oos_cagr)})`).join(' · ')}</div>`:''}`);
  const leaderTable=(sl)=>{ const ids=((LB.leaders||{})[sl])||[]; const rows=ids.map(id=>byId[id]).filter(Boolean); const par=((LB.parents||{})[sl]||{}).outofsample||{};
    return `<table><thead><tr><th>#</th><th>cell</th><th class="num" title="prop score out of sample: share of simulated evaluations passed, minus half the share that broke a loss limit">prop score</th><th class="num" title="passed / broke a loss limit / median days to pass">pass · breach · days</th><th class="num">OOS CAGR</th><th class="num">OOS DD</th><th class="num">IS CAGR</th><th>result</th></tr></thead><tbody>${rows.map((t,i)=>`<tr class="labrow ${t.id===UI.labSel?'sel':''}" data-id="${esc(t.id)}"><td class="muted">${i+1}</td><td><code class="ov">${esc(ovs(t.overrides)||t.name)}</code>${(LB.equivalents||{})[t.id]>1?` <span class="pill" title="other settings produced exactly the same result: those knobs do nothing here">×${(LB.equivalents||{})[t.id]} equivalent settings</span>`:''}</td><td class="num"><b>${t.oos_score==null?'—':(t.oos_score>=0?'+':'')+Number(t.oos_score).toFixed(2)}</b></td><td class="num small">${t.oos_prop==null?'—':Math.round(t.oos_prop*100)+'%'} · ${t.oos_breach==null?'—':Math.round(t.oos_breach*100)+'%'} · ${t.oos_prop_days?Math.round(t.oos_prop_days)+'d':'—'}</td><td class="num">${spct(t.oos_cagr)}</td><td class="num">${pct(t.oos_dd)}</td><td class="num">${spct(t.is_cagr)}</td><td>${t.passed?'<span class="pill good">beat parent</span>':'<span class="pill">rejected</span>'}</td></tr>`).join('')||'<tr><td colspan="8" class="muted">none yet</td></tr>'}</tbody></table><div class="small muted" style="margin-top:4px">parent: prop score ${par.prop_score==null?'—':Number(par.prop_score).toFixed(2)}, pass ${par.prop_pass_rate==null?'—':Math.round(par.prop_pass_rate*100)+'%'}, OOS ${spct(par.cagr)}, drawdown ${pct(par.max_drawdown)} · ranked by ${esc(OP.objective==='prop'?'prop score, then the faster pass, then OOS CAGR':'OOS CAGR')}</div>`; };
  html+=card('Leaderboard · stocks','c6',leaderTable('stocks'));
  html+=card('Leaderboard · crypto','c6',leaderTable('crypto'));
  html+=card('Leaderboard · forex, gold &amp; indices','c6',leaderTable('macro'));
  html+=card('Map: every test, out-of-sample return vs drawdown','c6',legend([{name:'stocks',color:css('--s1')},{name:'crypto',color:css('--s2')},{name:'forex & gold',color:css('--s4')},{name:'parent',color:css('--s3')}])+'<div class="small muted">filled = beat the parent · hollow = rejected · ◆ = parent · click a mark</div><div class="chart" id="labmap"></div>');
  html+=card('Selected test vs parent and buy & hold (log scale)','c6',`<div class="row" style="margin-bottom:6px"><span class="small" id="labtitle"></span><div class="tools"><button data-win="insample">in-sample</button><button data-win="outofsample">out-of-sample</button></div></div><div class="chart" id="labcurve"></div>`,'labcurve');
  const best=(LB.progress||{}).best||{};
  html+=card('Best out-of-sample growth found so far','c6',legend([{name:'stocks',color:css('--s1')},{name:'crypto',color:css('--s2')},{name:'forex & gold',color:css('--s4')}])+'<div class="chart" id="bestchart"></div>','bestchart');
  html+=card('Backtests per hour','c6',legend([{name:'stocks',color:css('--s1'),sq:true},{name:'crypto',color:css('--s2'),sq:true},{name:'forex & gold',color:css('--s4'),sq:true}])+'<div class="chart" id="rate"></div>');
  const knobSvg=(k,sl)=>{ const P=k.points||[]; const par=(((LB.parents||{})[sl]||{}).outofsample||{}).cagr; const ys=P.map(p=>p[1]).concat(par!=null?[par]:[]); let y0=Math.min(0,...ys), y1=Math.max(...ys,0.01); const py=(y1-y0)*0.1; y0-=py; y1+=py;
    const cats=k.categorical?[...new Set(P.map(p=>String(p[0])))]:null; const xsN=k.categorical?null:P.map(p=>Number(p[0])); const x0=k.categorical?0:Math.min(...xsN), x1=k.categorical?Math.max(1,cats.length-1):Math.max(...xsN);
    const W=230,H=120,m={l:34,r:6,t:6,b:k.categorical?30:18}; const X=v=>m.l+(x1===x0?0.5:(v-x0)/(x1-x0))*(W-m.l-m.r); const Y=v=>m.t+(1-(v-y0)/(y1-y0))*(H-m.t-m.b);
    const col=sl==='stocks'?css('--s1'):css('--s2');
    const dots=P.map(p=>{ const xv=k.categorical?cats.indexOf(String(p[0])):Number(p[0]); const jit=k.categorical?((p[1]*997)%1-0.5)*0.25:0; return `<circle cx="${X(xv+jit).toFixed(1)}" cy="${Y(p[1]).toFixed(1)}" r="3" fill="${p[2]?col:css('--surface')}" stroke="${col}" stroke-width="1.5"><title>${esc(String(p[0]))} → ${spct(p[1])}${p[2]?' (beat the parent)':''}</title></circle>`; }).join('');
    const xl=k.categorical?cats.map((c,i)=>`<text x="${X(i)}" y="${H-16}" font-size="9" text-anchor="middle" fill="${css('--muted')}">${esc(c.replace('_breakout','').replace('_threshold','').slice(0,10))}</text>`).join(''):`<text x="${m.l}" y="${H-4}" font-size="9" fill="${css('--muted')}">${esc(String(x0))}</text><text x="${W-m.r}" y="${H-4}" font-size="9" text-anchor="end" fill="${css('--muted')}">${esc(String(x1))}</text>`;
    return `<svg viewBox="0 0 ${W} ${H}"><line x1="${m.l}" x2="${W-m.r}" y1="${Y(0)}" y2="${Y(0)}" stroke="${css('--axis')}"/>${par!=null?`<line x1="${m.l}" x2="${W-m.r}" y1="${Y(par)}" y2="${Y(par)}" stroke="${css('--s3')}" stroke-dasharray="4 4"/>`:''}<text x="${m.l-4}" y="${Y(y1)+8}" font-size="9" text-anchor="end" fill="${css('--muted')}">${pct(y1)}</text><text x="${m.l-4}" y="${Y(y0)}" font-size="9" text-anchor="end" fill="${css('--muted')}">${pct(y0)}</text>${dots}${xl}</svg>`; };
  const KN=LB.knobs||{};
  html+=card('What the lab is learning: each setting it has varied against the out-of-sample result','',`<div class="legend"><span><i style="background:${css('--s1')}"></i>stocks</span><span><i style="background:${css('--s2')}"></i>crypto</span><span><i style="background:${css('--s3')}"></i>parent (dashed)</span><span class="muted">filled = beat the parent · ranked by how much the result moves with the setting</span></div><div class="knobs">${['crypto','stocks'].flatMap(sl=>(KN[sl]||[]).map(k=>`<div class="knob"><div class="kh"><b>${esc(sl)} · ${esc(k.knob.replace(/^(stocks|crypto)\./,''))}</b><span class="muted">${k.n} tests</span></div><div class="small muted">results move ${pct(k.spread)} across its values</div>${knobSvg(k,sl)}</div>`)).join('')||'<div class="muted small">needs a few tests per setting</div>'}</div>`);
  const gal=LT.filter(t=>t.by!=='parent'&&t.by!=='seat'&&(LB.curves||{})[t.id]).slice(0,60);
  html+=card(`Every recent backtest, charted (${gal.length} newest; click any for its full curve)`,'',`<div class="legend"><span><i style="background:${css('--s1')}"></i>the cell (out of sample)</span><span><i style="background:${css('--axis')}"></i>parent</span></div><div class="gallery">${gal.map(t=>`<div class="gt ${t.id===UI.labSel?'sel':''}" data-id="${esc(t.id)}"><div class="n" title="${esc(ovs(t.overrides))}">${esc(t.by)} · ${esc(t.sleeve)} · ${esc(ovs(t.overrides)||t.name)}</div><div class="m"><b>${spct(t.oos_cagr)}</b>${t.passed?'<span class="pill good">beat</span>':'<span class="pill">rejected</span>'}</div>${miniSvg(curveOf(t.id,'outofsample'), parentCurve(t.sleeve,'outofsample'))}<div class="n muted">${esc(lt(t.ts))}</div></div>`).join('')||'<div class="muted small">no curves yet</div>'}</div>`);
  const stPill=t=>t.by==='parent'?'<span class="pill">reference</span>':t.by==='seat'?'<span class="pill good">seated</span>':t.passed?`<span class="pill good">${t.child_id?('seated · '+esc(t.child_status||'shadow')):'beat the parent'}</span>`:(t.verdict||'').startsWith('rejected: backtest failed')?'<span class="pill bad">failed</span>':'<span class="pill">rejected</span>';
  html+=card(`Lab log (newest ${LT.length}; the whole record is in reports/lab)`,'',`<input class="filter" id="labfilter" placeholder="filter: sleeve, knob, verdict, stage…" value="${esc(UI.filter)}"><div style="max-height:440px;overflow:auto"><table id="labtable"><thead><tr><th class="nw">when</th><th class="nw">stage</th><th class="nw">sleeve</th><th>cell</th><th class="num nw">IS CAGR</th><th class="num nw">OOS CAGR</th><th class="num nw">OOS DD</th><th class="num nw" title="simulated prop-firm evaluations passed, out of sample">prop</th><th class="num nw">Sharpe</th><th class="num nw">trades</th><th>result</th></tr></thead><tbody>${LT.map(t=>`<tr class="labrow ${t.id===UI.labSel?'sel':''}" data-id="${esc(t.id)}"><td class="muted small nw">${esc(lt(t.ts))}</td><td class="nw">${esc(t.by)}</td><td class="nw">${esc(t.sleeve)}</td><td><code class="ov">${esc(ovs(t.overrides)||t.name||'')}</code></td><td class="num">${spct(t.is_cagr)}</td><td class="num">${t.oos_cagr!=null&&t.parent_oos_cagr!=null&&t.oos_cagr>t.parent_oos_cagr?'<b>'+spct(t.oos_cagr)+'</b>':spct(t.oos_cagr)}</td><td class="num">${pct(t.oos_dd)}</td><td class="num">${t.oos_prop==null?'—':Math.round(t.oos_prop*100)+'%'}</td><td class="num">${num((t.oos||{}).sharpe)}</td><td class="num">${(t.oos||{}).n_trades==null?'—':Math.round(t.oos.n_trades)}</td><td>${stPill(t)} <span class="small muted">${esc((t.verdict||'').replace(/^rejected: /,''))}</span></td></tr>`).join('')||'<tr><td colspan="11" class="muted">no backtests yet</td></tr>'}</tbody></table></div>`);
  // ---------- TRADING ----------
  html+=sec('trading','Trading','the primary book, the rules-only shadow and the benchmarks');
  const idx=(arr)=>(arr||[]).map((v,i)=>[E.dates[i], v==null?null:v/start*100]);
  const series=[{name:'rules + LLM (net)',color:css('--s1'),points:idx(E.primary_net)},
                {name:'rules-only shadow',color:css('--s2'),points:(E.dates||[]).map(d=>[d,(E.shadow||{})[d]==null?null:E.shadow[d]/start*100])}];
  Object.entries(D.benchmarks||{}).slice(0,2).forEach(([k,v],i)=>series.push({name:k,color:[css('--s3'),css('--s4')][i],points:(E.dates||[]).map(d=>[d,v[d]==null?null:v[d]/start*100])}));
  html+=card('Equity, indexed to 100 at start','c8',legend(series)+'<div class="chart" id="eq"></div>','eq');
  const hbRows=(HB.positions||[]).map(p=>`<tr><td><b>${esc(p.symbol)}</b> <span class="pill">${esc(p.asset_class)}</span></td><td class="num">${fmt$(p.market_value)}</td><td class="num">${p.unrealised_pct==null?'—':spct(p.unrealised_pct)}</td></tr>`).join('');
  html+=card(`Live book · ${esc(HB.broker||'')}`,'c4',`<div class="small muted" style="margin-bottom:6px">equity ${fmt$(HB.equity)} · cash ${fmt$(HB.cash)} · <span data-ago="${esc(HB.ts||'')}">${agoTxt(HB.ts)}</span></div><table><thead><tr><th>symbol</th><th class="num">value</th><th class="num">unrealised</th></tr></thead><tbody>${hbRows||'<tr><td colspan="3" class="muted">no open positions</td></tr>'}</tbody></table>`);
  let pk=-Infinity; const dd=(E.primary_net||[]).map((v,i)=>{pk=Math.max(pk,v); return [E.dates[i], v/pk-1]});
  html+=card('Drawdown (net)','c6',legend([{name:'drawdown',color:css('--s1')}])+'<div class="chart" id="dd"></div>','dd');
  html+=card('Cumulative AI spend paid from the bankroll','c6',legend([{name:'AI + data cost',color:css('--s1')}])+'<div class="chart" id="cost"></div>','cost');
  const pos=(D.positions||{}).primary||[];
  html+=card(`Open positions (primary) · ${pos.length}`,'c7',`<table><thead><tr><th>symbol</th><th class="num">qty</th><th class="num">entry</th><th class="num">stop</th><th class="num">stop dist</th><th class="num">bars</th><th>status</th></tr></thead><tbody>${pos.map(p=>`<tr><td><b>${esc(p.symbol)}</b> <span class="pill">${esc(p.asset_class)}</span></td><td class="num">${num(p.qty,4)}</td><td class="num">${fmt$(p.entry)}</td><td class="num">${fmt$(p.stop)}</td><td class="num">${p.entry?pct(p.stop/p.entry-1):'—'}</td><td class="num">${p.bars_held}</td><td>${p.fill_pending?'<span class="pill">fill pending</span>':(p.stop_armed?'<span class="pill good">stop armed</span>':'<span class="pill bad">no stop</span>')}</td></tr>`).join('')||'<tr><td colspan="7" class="muted">none</td></tr>'}</tbody></table>`);
  const ordc=['--ord1','--ord2','--ord3','--ord4','--ord5'];
  html+=card('Survival tiers','c5',`<div class="ladder">${tiers.map((t,i)=>`<div class="rung ${t.name===st.tier?'cur':''}" style="--rc:${css(ordc[i]||'--ord5')}"><b>${esc(t.name)}</b><br>≥ ${fmt$(t.min_equity_usd)}<br>risk ×${t.risk_multiplier} · seats ${t.max_children}${t.close_only?' · close-only':''}</div>`).join('')}</div><div class="small muted" style="margin-top:8px">death at ${fmt$(S.death_floor)} or −75% from peak · halts ${(S.halts||[]).length}${(D.allocation&&D.allocation.crypto_sleeve_pct!=null)?` · crypto sleeve ${Math.round(D.allocation.crypto_sleeve_pct*100)}%`:''}</div>`);
  const DK=D.desks||{}; const fmtS=v=>v==null?'—':((v>0?'+':'')+Number(v).toFixed(2));
  const nrows=(DK.notes||[]).map(n=>{const nw=n.news||{}; return `<tr><td><b>${esc(n.symbol)}</b></td><td class="small">${nw.material_negative?'<b>material negative</b> · ':''}${nw.attention_spike?'attention spike · ':''}sent ${fmtS(nw.net_sentiment)}</td><td class="num">${fmtS((n.fundamental||{}).signal)}</td><td class="num">${fmtS((n.technical||{}).signal)}</td><td class="num">${fmtS((n.sentiment||{}).signal)}</td><td class="num">${fmtS((n.debate||{}).net_signal)}</td><td>${esc(n.action)}${n.confidence!=null?` (${Number(n.confidence).toFixed(2)})`:''}</td></tr>`;}).join('');
  html+=card(`The desks${DK.date?` · ${esc(DK.date)}`:''}`,'c7',`${DK.portfolio_note?`<blockquote>Portfolio manager: ${esc(DK.portfolio_note)}</blockquote>`:''}<table style="margin-top:6px"><thead><tr><th>candidate</th><th>news desk</th><th class="num">fund.</th><th class="num">tech.</th><th class="num">sent.</th><th class="num">debate</th><th>PM</th></tr></thead><tbody>${nrows||'<tr><td colspan="7" class="muted">no candidates reached the desks on the last cycle</td></tr>'}</tbody></table>`);
  const dec=(P.decisions||[]);
  html+=card(`Latest cycle · ${esc(P.run_date||'—')}`,'c5',`<div class="small muted" style="margin-bottom:6px">${(P.notes||[]).map(esc).join(' · ')||'no notes'}</div><ul class="plain small">${dec.map(d=>`<li><b>${esc(d.symbol)}</b> ${esc(d.action)}${d.confidence!=null?` (${num(d.confidence,2)})`:''}: ${esc(d.rationale||'')}</li>`).join('')||'<li class="muted">no decisions</li>'}${(P.orders||[]).map(o=>`<li>order ${esc(o.side)} <b>${esc(o.symbol)}</b> ${fmt$(o.notional)} ${o.ok?'':'(failed)'}</li>`).join('')}${(P.exits||[]).map(x=>`<li>exit <b>${esc(x.symbol)}</b> ${esc(x.reason)}</li>`).join('')}</ul>`);

  // ---------- SUB-AGENTS AND SCOUTS ----------
  html+=sec('scouts','Sub-agents and scouts','each trades its own paper book; the record decides who lives');
  const SCO=D.scouts||{}; const tr=SCO['trench-scout']; const trL=((CDx['trench-scout']||{}).latest)||{};
  if(tr){ const F=tr.funnel||[]; const fmax=Math.max(1,...F.map(f=>f[1]));
    html+=card(`Trenches · paper book ${fmt$(trL.equity)} (started $100)`,'c7',`<div class="small muted" style="margin-bottom:6px">brand-new Solana launches, scanned every lab round · last scan <span data-ago="${esc(trL.ts||'')}">${agoTxt(trL.ts)}</span> · ${trL.n_open||0} open · cash ${fmt$(trL.cash)}</div><b class="small">Last scan</b><div class="funnel">${F.map((f,i)=>`<div class="fr"><span>${esc(f[0])}</span><span><span class="fb" style="display:block;width:${Math.max(0.5,f[1]/fmax*100).toFixed(1)}%;opacity:${1-i*0.12}"></span></span><b class="num">${f[1]}</b></div>`).join('')}</div><div class="small muted" style="margin-bottom:6px">rejected for: ${Object.entries(trL.rejects||{}).sort((a,b)=>b[1]-a[1]).slice(0,6).map(([k,v])=>`${esc(k)} ${v}`).join(' · ')||'—'}</div>${legend([{name:'trench paper equity',color:css('--s2')}])}<div class="chart" id="trencheq"></div>`,'trencheq');
    html+=card('Trench trades (paper)','c5',`<div style="max-height:430px;overflow:auto"><table><thead><tr><th class="nw">when</th><th>event</th><th>token</th><th class="num">USD</th><th class="num">P&amp;L</th></tr></thead><tbody>${(tr.trades||[]).map(x=>`<tr><td class="small muted nw">${esc(lt(x.ts))}</td><td>${x.kind==='entry'?'<span class="pill">bought</span>':x.kind==='take_half'?'<span class="pill good">took half</span>':`<span class="pill ${String(x.reason||'').startsWith('rugged')||String(x.reason||'').startsWith('vanished')?'bad':''}">sold</span> <span class="small muted">${esc(x.reason||'')}</span>`}</td><td><b>${esc(x.symbol||'?')}</b></td><td class="num">${fmt$(x.usd)}</td><td class="num">${x.pnl==null?'—':fmt$(x.pnl)}</td></tr>`).join('')||'<tr><td colspan="5" class="muted">no trades yet</td></tr>'}</tbody></table></div><div class="small muted" style="margin-top:6px">${(trL.open||[]).length?'open: '+trL.open.map(p=>`${esc(p.symbol)} ${fmt$(p.value)}`).join(' · '):'nothing open'}</div>`); }
  const hl=((CDx['hyperliquid-desk']||{}).latest)||{}; const hlS=SCO['hyperliquid-desk'];
  html+=card(`Hyperliquid desk (paper) · ${hl.equity!=null?fmt$(hl.equity):'first cycle tonight'}`,'c6',`<div class="small muted" style="margin-bottom:6px">the firm's crypto rules on Hyperliquid's most traded perps, priced from its public data: no account, no keys, no orders; taker fee and hourly funding charged; own $100 paper book</div>${hl.ts?`<div class="chips" style="margin-bottom:8px"><span class="chip">${hl.n_tradable||0} of ${hl.n_listed||0} perps with a year of history</span><span class="chip">regime <b>${esc((hl.regime||{}).state||'—')}</b></span><span class="chip">funding paid <b>${fmt$(hl.funding_paid_total||0)}</b></span><span class="chip">orders on ${esc(hl.run_date||'')}: <b>${hl.orders||0}</b></span></div><table><thead><tr><th>perp</th><th class="num">value</th><th class="num">avg</th><th class="num">last</th></tr></thead><tbody>${(hl.positions||[]).map(p=>`<tr><td><b>${esc(p.symbol)}</b></td><td class="num">${fmt$(p.value)}</td><td class="num">${Number(p.avg).toPrecision(5)}</td><td class="num">${Number(p.last).toPrecision(5)}</td></tr>`).join('')||'<tr><td colspan="4" class="muted">no open positions</td></tr>'}</tbody></table><div class="small muted" style="margin-top:6px">most traded: ${(hl.universe||[]).slice(0,10).map(u=>`${esc(u.coin)} (${spct((u.funding_hourly||0)*24*365)}/yr funding)`).join(' · ')}</div>`:'<div class="small">joins the firm on the next daily cycle (00:37 UTC) and trades from the cycle after it has a year of daily bars per coin.</div>'}${hlS&&hlS.equity&&hlS.equity.length>1?'<div class="chart" id="hleq" style="margin-top:8px"></div>':''}`);
  const mdId=(((D.hive||{}).children||[]).find(c=>c.sleeve==='macro'&&c.kind==='scout')||{}).id;
  const md=((CDx[mdId]||{}).latest)||{}; const mdS=SCO[mdId];
  html+=card(`Forex &amp; gold desk (paper) · ${md.equity!=null?fmt$(md.equity):'first cycle tonight'}`,'c6',`<div class="small muted" style="margin-bottom:6px">the firm's macro rules on the major forex pairs, gold, silver and three index futures, long and short, no leverage, on the lab's own simulator (next-open fills, stops on the day's range, CFD-like costs); own $100 paper book since ${esc(md.since||'its first cycle')}</div>${md.ts?`<div class="chips" style="margin-bottom:8px"><span class="chip">${md.markets||0} markets</span><span class="chip">${(md.positions||[]).length} open · ${(md.positions||[]).filter(p=>p.side==='short').length} short</span><span class="chip">${md.n_trades||0} closed trades</span></div><table><thead><tr><th>market</th><th>side</th><th class="num">value</th><th class="num">entry</th><th class="num">last</th><th class="num">stop</th></tr></thead><tbody>${(md.positions||[]).map(p=>`<tr><td><b>${esc(p.symbol)}</b></td><td>${p.side==='short'?'<span class="pill bad">short</span>':'<span class="pill good">long</span>'}</td><td class="num">${fmt$(p.exposure)}</td><td class="num">${Number(p.entry).toPrecision(5)}</td><td class="num">${Number(p.last).toPrecision(5)}</td><td class="num">${Number(p.stop).toPrecision(5)}</td></tr>`).join('')||'<tr><td colspan="6" class="muted">no open positions</td></tr>'}</tbody></table>`:'<div class="small">joins the firm on the next daily cycle once forex and gold prices are in the cache.</div>'}${mdS&&mdS.equity&&mdS.equity.length>1?'<div class="chart" id="mdeq" style="margin-top:8px"></div>':''}`);
  const SO=D.social||{};
  html+=card(`Social pulse${SO.ts?` · <span class="small muted" data-ago="${esc(SO.ts)}">${agoTxt(SO.ts)}</span>`:''}`,'c6',`<div class="small muted" style="margin-bottom:6px">every lab round; symbols and counts only (no post text is kept or passed to any model) · X: ${esc(SO.x_status||'not connected: add a paid X API bearer token as the X_BEARER_TOKEN secret')}</div><b class="small">On two or more sources</b><div class="chips" style="margin:4px 0 10px">${(SO.cross||[]).map(r=>`<span class="chip"><b>${esc(r.symbol)}</b> ${esc(r.sources.join('+'))}${r.reddit_mentions?` · ${r.reddit_mentions} posts`:''}${r.x_posts_24h?` · ${r.x_posts_24h} on X`:''}</span>`).join('')||'<span class="muted small">none yet</span>'}</div><div class="row" style="align-items:flex-start;gap:16px"><div style="flex:1;min-width:150px"><b class="small">Trending on StockTwits</b><div class="chips" style="margin-top:4px">${(SO.stocktwits||[]).slice(0,14).map(r=>`<span class="chip">${esc(r.symbol)}${r.crypto?' ·c':''}</span>`).join('')||'<span class="muted small">—</span>'}</div></div><div style="flex:1;min-width:150px"><b class="small">Cashtags on Reddit (${SO.reddit_posts_read||0} hot posts)</b><div class="chips" style="margin-top:4px">${(SO.reddit||[]).slice(0,14).map(r=>`<span class="chip">${esc(r.symbol)} <b>${r.mentions}</b></span>`).join('')||'<span class="muted small">—</span>'}</div></div></div><div class="small muted" style="margin-top:8px">DexScreener boosts (paid promotion, not a signal): ${(SO.dex_boosts||[]).length} tokens</div>`);
  const CD=D.children_detail||{}; const readyIds=new Set(H.ready_ids||[]);
  const spark=(marks)=>{ if(!marks||marks.length<2) return ''; const vs=marks.map(m=>m[1]); const lo=Math.min(...vs), hi=Math.max(...vs); const W=160,Hh=36; const pts=vs.map((v,i)=>`${(i/(vs.length-1)*W).toFixed(1)},${(Hh-2-(hi===lo?Hh/2:(v-lo)/(hi-lo)*(Hh-4))).toFixed(1)}`).join(' '); return `<svg class="spark" viewBox="0 0 ${W} ${Hh}" preserveAspectRatio="none"><polyline fill="none" stroke="${css('--s1')}" stroke-width="2" points="${pts}"/></svg>`; };
  const agentCards=(H.children||[]).filter(c=>!['trench','perps'].includes(c.sleeve)).slice().sort((a,b)=>((b.status==='shadow'||b.status==='live')-(a.status==='shadow'||a.status==='live'))).map(c=>{const d=CD[c.id]||{}; const active=c.status==='shadow'||c.status==='live';
    const a=AG.find(x=>x.id===c.id)||{};
    const head=`<div class="head"><b>${esc(c.name)}</b><span class="pill">${esc(c.kind)}</span><span class="pill">${esc(c.sleeve)}</span>${stateDot(a.state||(active?'recent':c.status), active?(a.state||'recent'):c.status)}${readyIds.has(c.id)?'<span class="pill good">ready to promote</span>':''}<span class="muted small">${c.shadow_days||0} d</span></div>`;
    let body='';
    if(c.sleeve==='trench'){ const L=d.latest||{}; body=`<div class="small">paper equity <b>${fmt$(L.equity)}</b> (started $100) · cash ${fmt$(L.cash)} · ${L.n_open||0} open · last scan <span data-ago="${esc(L.ts||'')}">${agoTxt(L.ts)}</span></div><div class="small muted">${L.scanned||0} launches scanned, ${L.passed||0} passed, ${L.rugchecked||0} rug-checked; rejected for: ${Object.entries(L.rejects||{}).sort((x,y)=>y[1]-x[1]).slice(0,4).map(([k,v])=>`${esc(k)} ${v}`).join(' · ')||'—'}</div><table style="margin-top:6px"><thead><tr><th>token</th><th class="num">entry</th><th class="num">last</th><th class="num">value</th></tr></thead><tbody>${(L.open||[]).map(p=>`<tr><td><b>${esc(p.symbol)}</b></td><td class="num">${Number(p.entry).toPrecision(3)}</td><td class="num">${Number(p.last).toPrecision(3)}</td><td class="num">${fmt$(p.value)}</td></tr>`).join('')||'<tr><td colspan="4" class="muted">nothing open</td></tr>'}</tbody></table>`; }
    else if(c.kind==='monitor'){ const Lm=d.latest||{}; body=`<div class="small muted">watching: ${(c.watch||[]).map(esc).join(' · ')||'—'}</div><ul class="plain small" style="margin-top:4px">${(Lm.reports||[]).map(r=>`<li><b>${esc(r.query)}</b>: ${r.n_items||0} items${r.net_sentiment!=null?` · sent ${fmtS(r.net_sentiment)}`:''}${r.material_negative?' · <b>material negative</b>':''}${r.attention_spike?' · attention spike':''}</li>`).join('')||'<li class="muted">no report yet</li>'}</ul>`; }
    else if(c.kind==='scout'){ const sc=DK.scout||{}; const mb=d.meme_book||{}; body=`<div class="small">${sc.date?`${esc(sc.date)}: scanned ${sc.scanned} · passed ${sc.n_candidates} · open ${sc.n_open} · paper ${fmt$(sc.equity)}`:'no scan yet'}</div><div class="small muted">open: ${Object.keys(mb.positions||{}).map(esc).join(', ')||'none'}</div>`; }
    else { const k=(d.today&&d.today.kinds)||{}; const acts=['decisions','order','exit','fill_confirmed','stop_ratchet'].filter(x=>k[x]).map(x=>`${k[x]} ${x.replace('_',' ')}`).join(' · '); body=`${spark(d.marks)}<div class="small">equity ${fmt$(c.equity)} · VOI ${c.voi==null?'—':spct(c.voi)} · open ${(d.positions||[]).length}</div><code class="ov">${esc(ovs(c.overrides)||'parent config')}</code>${d.today?`<div class="small muted">${esc(d.today.date)}: ${acts||'quiet day'}</div>`:''}`; }
    const last=(c.notes||[]).slice(-1)[0]; return `<div class="agent">${head}${body}${last?`<div class="small muted" style="margin-top:4px">${esc(last)}</div>`:''}</div>`; }).join('');
  html+=card(`${activeAgents.length} active · ${(H.children||[]).length-activeAgents.length} retired or promoted`,'',`<div class="agents">${agentCards||'<div class="muted small">none yet</div>'}</div>${D.promoted&&Object.keys(D.promoted.overrides||{}).length?`<div class="small" style="margin-top:8px">Promoted into the live configuration: <code class="ov">${esc(ovs(D.promoted.overrides))}</code></div>`:''}`);

  // ---------- RULES ----------
  const R=D.rules||{};
  html+=sec('rules','Rules in force','read from the configuration and the code that applies them; nothing here is hand-written');
  const who=w=>w==='lab'?'<span class="pill lab">lab moves it</span>':w==='owner'?'<span class="pill owner">owner edits config</span>':'<span class="pill code">code</span>';
  const rtab=rows=>`<table><thead><tr><th>rule</th><th>value</th><th>who can change it</th></tr></thead><tbody>${(rows||[]).map(r=>`<tr><td>${esc(r.what)}<div class="small muted mono">${esc(r.key)}</div></td>${typeof r.value==='number'?`<td class="num">${esc(String(r.value))}</td>`:`<td class="small" style="min-width:140px">${String(r.value??'').split(' · ').map(esc).join('<br>')}</td>`}<td>${who(r.who)}${r.range?`<div class="small muted">range ${esc(r.range)}</div>`:''}</td></tr>`).join('')}</tbody></table>`;
  html+=card('What every model is told (verbatim)','c6',`<blockquote>${esc(R.objective||'')}</blockquote>`);
  html+=card('Survival: life, death and what equity unlocks','c6',rtab(R.survival));
  html+=card('Sizing and risk','c6',rtab(R.risk));
  html+=card('Promotion and punishment','c6',rtab(R.search));
  html+=card('Prop-firm evaluation: the objective and the readiness bar','c6',rtab(R.prop));
  html+=card('Enforced in code (no setting changes these)','c6',`<ul class="plain small">${(R.code||[]).map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`);
  html+=card('Money and venues','c6',rtab(R.venues));

  // ---------- RECORD ----------
  html+=sec('record','The record','what the operator learned, what the auditor found, every iteration');
  const RL=D.research_log||[];
  html+=card("Operator's notes",'c6',`<ul class="plain small">${RL.slice(-8).reverse().map(r=>`<li><span class="muted">${esc(r.date)}</span> ${esc(r.note||'(no note)')}</li>`).join('')||'<li class="muted">none yet</li>'}</ul>`);
  const RV=D.review||{};
  html+=card(`Auditor · ${RV.date?esc(RV.date):'no review yet'}`,'c6',`<ul class="plain small">${(RV.findings||[]).map(f=>`<li><span class="pill ${f.severity==='critical'?'bad':''}">${esc(f.severity)}</span> <b>${esc(f.area)}</b>: ${esc(f.finding)}</li>`).join('')||'<li class="muted">no findings yet</li>'}</ul>${RV.assessment?`<div class="small muted" style="margin-top:6px">${esc(RV.assessment)}</div>`:''}`);
  const bt=D.backtests||[];
  html+=card('Reference backtests (cleaned data, next-open fills, costs)','c6',`<table><thead><tr><th>run</th><th class="num">CAGR</th><th class="num">Sharpe</th><th class="num">max DD</th><th class="num">trades</th><th class="num">bench CAGR</th></tr></thead><tbody>${bt.map(b=>`<tr><td><b>${esc(b.name)}</b> <span class="pill">v${esc(b.config_version||'')}</span></td><td class="num">${pct(b.cagr)}</td><td class="num">${num(b.sharpe)}</td><td class="num">${pct(b.max_drawdown)}</td><td class="num">${b.n_trades}</td><td class="num">${pct((b.benchmark||{}).cagr)}</td></tr>`).join('')||'<tr><td colspan="6" class="muted">none</td></tr>'}</tbody></table>`);
  const calls=(D.journal||{}).llm_calls||[]; const byRole={}; calls.forEach(c=>{byRole[c.role]=byRole[c.role]||{n:0,cost:0,fail:0}; byRole[c.role].n++; byRole[c.role].cost+=c.cost_usd||0; byRole[c.role].fail+=c.ok?0:1;});
  html+=card('LLM calls (last 30 run days)','c6',`<table><thead><tr><th>role</th><th class="num">calls</th><th class="num">failed</th><th class="num">cost</th></tr></thead><tbody>${Object.entries(byRole).map(([r,v])=>`<tr><td>${esc(r)}</td><td class="num">${v.n}</td><td class="num">${v.fail}</td><td class="num">${fmt$(v.cost)}</td></tr>`).join('')||'<tr><td colspan="4" class="muted">no LLM calls yet</td></tr>'}</tbody></table>`);
  const ex=D.experiments||{}; const exKeys=Object.keys(ex);
  if(exKeys.length){ html+=card('Experiments (robustness grid, null tests, walk-forward)','',exKeys.map(k=>{const rows=ex[k]; const cols=Object.keys(rows[0]||{}).filter(c=>c!=='extra').slice(0,12); return `<details><summary class="small">${esc(k)} (${rows.length} rows)</summary><div style="overflow:auto"><table><thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td class="num">${esc(isNaN(parseFloat(r[c]))?r[c]:Number(r[c]).toFixed(4))}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`}).join('')); }

  $('#main').innerHTML=html;
  $('#foot').innerHTML=`Not investment advice. Every number on this page is read from the hash-chained ledger, journals and lab records in the repository; the page rebuilds after every lab round and updates itself in place. <a href="data.json">data.json</a>`;

  // ---------- draw ----------
  const tables={};
  drawNetwork($('#net'), AG);
  tables.eq=lineChart($('#eq'),series,{fmt:v=>num(v,1)});
  tables.dd=lineChart($('#dd'),[{name:'drawdown',color:css('--s1'),points:dd,area:true}],{fmt:v=>pct(v),zero:true});
  tables.cost=lineChart($('#cost'),[{name:'AI + data cost',color:css('--s1'),points:(E.dates||[]).map((d,i)=>[d,E.cum_ai_cost[i]])}],{fmt:v=>fmt$(v),zero:true});
  const step=arr=>{ const out=[]; (arr||[]).forEach((p,i)=>{ const x=(p[0]||'').slice(0,16).replace('T',' '); if(i) out.push([x,arr[i-1][1]]); out.push([x,p[1]]); }); if(out.length){ const nowX=new Date().toISOString().slice(0,16).replace('T',' '); out.push([nowX,out[out.length-1][1]]); } return out; };
  tables.bestchart=lineChart($('#bestchart'),[{name:'stocks',color:css('--s1'),points:step(best.stocks)},{name:'crypto',color:css('--s2'),points:step(best.crypto)},{name:'forex & gold',color:css('--s4'),points:step(best.macro)}],{fmt:v=>pct(v),xfmt:x=>lt(x),xname:'time',height:220});
  if(tr&&tr.equity&&tr.equity.length) tables.trencheq=lineChart($('#trencheq'),[{name:'trench paper equity',color:css('--s2'),points:tr.equity}],{fmt:v=>fmt$(v),xfmt:x=>lt(x),xname:'time',height:200});
  if(hlS&&hlS.equity&&hlS.equity.length>1) lineChart($('#hleq'),[{name:'desk paper equity',color:css('--s1'),points:hlS.equity}],{fmt:v=>fmt$(v),xfmt:x=>lt(x),height:160});
  if(mdS&&mdS.equity&&mdS.equity.length>1) lineChart($('#mdeq'),[{name:'desk paper equity',color:css('--s4'),points:mdS.equity}],{fmt:v=>fmt$(v),xfmt:x=>lt(x),height:160});
  barChart($('#rate'),((LB.progress||{}).hours||[]),[{name:'stocks',color:css('--s1')},{name:'crypto',color:css('--s2')},{name:'forex & gold',color:css('--s4')}],{xfmt:h=>lt(h+':00'),height:220});
  const byCol={stocks:css('--s1'),crypto:css('--s2'),macro:css('--s4')};
  function drawLab(){
    const sel=byId[UI.labSel]||null; const win=UI.labWin;
    const pts=[];
    LT.slice().reverse().forEach(t=>{ if(t.by==='parent'||t.by==='seat'||t.oos_cagr==null) return;
      pts.push({x:t.oos_dd,y:t.oos_cagr,color:byCol[t.sleeve]||css('--s4'),filled:!!t.passed,selected:t.id===UI.labSel,
        tip:`<div><b>${esc(ovs(t.overrides)||t.name)}</b></div><div class="muted">${esc(t.by)} · ${esc(t.sleeve)} · ${esc(lt(t.ts))}</div><div>IS ${spct(t.is_cagr)} · OOS ${spct(t.oos_cagr)} · DD ${pct(t.oos_dd)}</div><div class="muted">${esc((t.verdict||'').slice(0,90))}</div>`,
        onclick:()=>select(t.id)}); });
    Object.entries(LB.parents||{}).forEach(([sl,p])=>{ const o=p.outofsample||{}; if(o.cagr==null) return; pts.push({x:o.max_drawdown,y:o.cagr,color:css('--s3'),shape:'diamond',tip:`<div><b>parent · ${esc(sl)}</b></div><div>OOS ${spct(o.cagr)} · DD ${pct(o.max_drawdown)}</div>`}); });
    scatterChart($('#labmap'),pts,{xlabel:'OOS max drawdown →',ylabel:'OOS CAGR'});
    const el=$('#labcurve'), title=$('#labtitle'); if(!el) return;
    $$('button[data-win]').forEach(b=>b.classList.toggle('on', b.dataset.win===win));
    if(!sel){ el.innerHTML='<div class="muted small">no test selected</div>'; title.textContent=''; return; }
    title.innerHTML=`<b>${esc(ovs(sel.overrides)||sel.name)}</b> · ${win==='insample'?'in-sample':'out-of-sample'} · ${esc(sel.sleeve)}${sel.oos_cagr!=null?` · OOS ${spct(sel.oos_cagr)} vs parent ${spct(sel.parent_oos_cagr)}`:''}`;
    const cv=curveOf(sel.id,win); const par=parentCurve(sel.sleeve,win);
    if(!cv){ el.innerHTML='<div class="muted small">loading this curve…</div>'; fetchCurve(sel).then(ok=>{ if(UI.labSel!==sel.id) return; if(ok&&curveOf(sel.id,win)) drawLab(); else el.innerHTML=`<div class="muted small">${win==='insample'?'rejected cells keep their out-of-sample curve only':'this curve is older than a day; it is in the repository (reports/lab/days)'}</div>`; }); return; }
    const lg=v=>(v!=null&&v>0)?Math.log10(v):null;
    const ser=[{name:'this cell',color:css('--s1'),points:cv.dates.map((d,i)=>[d,lg(cv.equity[i])])}];
    if(par&&par.dates) ser.push({name:'parent',color:css('--s2'),points:par.dates.map((d,i)=>[d,lg(par.equity[i])])});
    if(cv.benchmark&&cv.benchmark.some(v=>v!=null)) ser.push({name:'buy & hold',color:css('--s3'),points:cv.dates.map((d,i)=>[d,cv.benchmark[i]==null?null:lg(cv.benchmark[i])])});
    tables.labcurve=lineChart(el,ser,{fmt:v=>fmtK(Math.pow(10,v))});
  }
  function select(id){ UI.labSel=id; $$('tr.labrow,.gt').forEach(r=>r.classList.toggle('sel', r.dataset.id===id)); drawLab(); }
  $$('tr.labrow,.gt').forEach(r=>r.onclick=()=>select(r.dataset.id));
  $$('button[data-win]').forEach(b=>b.onclick=()=>{ UI.labWin=b.dataset.win; drawLab(); });
  const lf=$('#labfilter'); const applyFilter=()=>{ const q=UI.filter.toLowerCase(); $$('#labtable tr.labrow').forEach(r=>{ r.style.display=!q||r.textContent.toLowerCase().includes(q)?'':'none'; }); };
  if(lf){ lf.oninput=()=>{ UI.filter=lf.value; applyFilter(); }; applyFilter(); }
  drawLab();
  $$('button[data-table]').forEach(b=>b.onclick=()=>{const t=tables[b.dataset.table]; if(t) t.classList.toggle('show');});
  scrollTo(0,y);
}

// ---------- live: clocks tick every second; a newer build re-renders in place ----------
function tick(){
  $$('[data-ago]').forEach(e=>{ if(e.dataset.ago) e.textContent=agoTxt(e.dataset.ago); });
  $$('[data-cd]').forEach(e=>{ if(e.dataset.cd) e.textContent=cdTxt(e.dataset.cd); });
  $$('[data-prog-start]').forEach(e=>{ const s=parseTs(e.dataset.progStart); if(!s) return; const f=Math.min(1,(Date.now()-s)/(Number(e.dataset.progMin||7)*60000)); e.style.width=(f*100).toFixed(1)+'%'; });
  const c=$('#clock'); if(c) c.textContent=new Date().toLocaleTimeString(undefined,{hour12:false});
}
async function refresh(){
  try{
    const v=await fetch('version.json?t='+Date.now(),{cache:'no-store'}); if(!v.ok) return;
    const ver=await v.json(); if(!ver||ver.generated_at===D.generated_at) return;
    const r=await fetch('data.json?t='+Date.now(),{cache:'no-store'}); if(!r.ok) return;
    const d=await r.json(); if(d&&d.generated_at){ D=d; render(); }
  }catch(e){}
}
render(); tick();
setInterval(tick, 1000);
setInterval(refresh, 30*1000);
document.addEventListener('visibilitychange',()=>{ if(!document.hidden) refresh(); });
addEventListener('resize',()=>{ clearTimeout(window.__r); window.__r=setTimeout(render,200); });
</script>
</body>
</html>
"""


def build(settings: Settings, cfg: StrategyConfig, out_dir: Path | None = None) -> dict[str, Path]:
    out_dir = Path(out_dir or (settings.hive_reports_dir / "dashboard"))
    out_dir.mkdir(parents=True, exist_ok=True)
    data = collect(settings, cfg)
    payload = json.dumps(data, default=str).replace("</", "<\\/")
    html = TEMPLATE.replace("__DATA__", payload)
    (out_dir / "index.html").write_text(html, encoding="utf-8")
    (out_dir / "data.json").write_text(json.dumps(data, default=str), encoding="utf-8")
    (out_dir / "version.json").write_text(json.dumps({"generated_at": data["generated_at"]}), encoding="utf-8")
    (out_dir / ".nojekyll").write_text("")
    try:
        from hive.operator.lab import export_recent_curves

        export_recent_curves(settings.hive_reports_dir / "lab", out_dir, hours=24)
    except Exception:  # the page still works without the on-demand curves
        pass
    return {"html": out_dir / "index.html", "data": out_dir / "data.json"}


def serve(settings: Settings, cfg: StrategyConfig, port: int = 8787) -> None:  # pragma: no cover - manual
    import functools
    import http.server

    out_dir = settings.hive_reports_dir / "dashboard"
    build(settings, cfg, out_dir)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):  # rebuild on every page load so the view is always current
            if self.path in ("/", "/index.html"):
                build(settings, cfg, out_dir)
            super().do_GET()

    handler = functools.partial(Handler, directory=str(out_dir))
    print(f"HIVE hub on http://127.0.0.1:{port}/  (Ctrl-C to stop)")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
