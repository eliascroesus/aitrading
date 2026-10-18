"""Render the dashboard HTML from the collected data (no external dependencies)."""

from __future__ import annotations

import json
from pathlib import Path

from hive.config import Settings, StrategyConfig
from hive.ui.collect import collect

TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HIVE dashboard</title>
<style>
:root{color-scheme:light;
 --page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
 --s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--good:#0ca30c;--warn:#fab219;--serious:#ec835a;--crit:#d03b3b;--up:#006300;
 --ord1:#86b6ef;--ord2:#5598e7;--ord3:#2a78d6;--ord4:#1c5cab;--ord5:#0d366b}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;
 --page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
 --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--up:#0ca30c;--ord1:#184f95;--ord2:#256abf;--ord3:#3987e5;--ord4:#6da7ec;--ord5:#9ec5f4}}
:root[data-theme="dark"]{color-scheme:dark;
 --page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
 --s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--up:#0ca30c;--ord1:#184f95;--ord2:#256abf;--ord3:#3987e5;--ord4:#6da7ec;--ord5:#9ec5f4}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:baseline;justify-content:space-between;padding:20px 16px 8px;max-width:1240px;margin:0 auto}
h1{font-size:20px;margin:0;font-weight:650}
h2{font-size:15px;margin:0 0 10px;font-weight:650}
.sub{color:var(--ink2);font-size:13px}
main{max-width:1240px;margin:0 auto;padding:0 16px 48px;display:grid;gap:16px;grid-template-columns:repeat(12,1fr)}
.card{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 16px;grid-column:span 12}
.c6{grid-column:span 6}.c4{grid-column:span 4}.c8{grid-column:span 8}
@media (max-width:860px){.c6,.c4,.c8{grid-column:span 12}}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;grid-column:span 12}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.tile .label{color:var(--ink2);font-size:12px}
.tile .value{font-size:26px;font-weight:650;line-height:1.15;margin-top:4px}
.tile .delta{font-size:12px;color:var(--ink2);margin-top:2px}
.status{display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid var(--border)}
.status .dot{width:8px;height:8px;border-radius:50%}
.banner{grid-column:span 12;background:var(--surface);border:2px solid var(--crit);border-radius:10px;padding:14px 16px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top;overflow-wrap:anywhere}
.card{min-width:0;overflow:hidden}
code.ov{font-size:11px;color:var(--ink2)}
th{color:var(--ink2);font-weight:600;font-size:12px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.muted{color:var(--muted)}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--ink2);margin:4px 0 6px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.legend i{display:inline-block;width:14px;height:2px;border-radius:2px}
.chart{position:relative;width:100%}
.chart svg{display:block;width:100%;height:auto;overflow:visible}
.tt{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:6px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.12);white-space:nowrap;display:none;z-index:2}
.tools{display:flex;gap:8px;align-items:center;margin-left:auto}
button{font:inherit;font-size:12px;color:var(--ink2);background:transparent;border:1px solid var(--border);border-radius:6px;padding:3px 8px;cursor:pointer}
button:hover{color:var(--ink)}
.tablewrap{display:none;max-height:260px;overflow:auto;margin-top:8px}
.tablewrap.show{display:block}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.ladder{display:flex;gap:6px;flex-wrap:wrap}
.rung{flex:1;min-width:110px;border-radius:8px;padding:8px 10px;color:#fff;font-size:12px;opacity:.55}
.rung.cur{opacity:1;outline:2px solid var(--ink)}
.small{font-size:12px}
.pill{display:inline-block;padding:1px 7px;border-radius:999px;border:1px solid var(--border);font-size:11px;color:var(--ink2)}
footer{max-width:1240px;margin:0 auto;padding:0 16px 24px;color:var(--muted);font-size:12px}
</style>
</head>
<body>
<header>
  <div><h1>HIVE — survival-constrained AI trading study</h1>
    <div class="sub" id="subtitle"></div></div>
  <div class="tools"><button id="theme">theme</button><a class="small" href="data.json">data.json</a></div>
</header>
<main id="main"></main>
<footer id="foot"></footer>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const $ = (s, el=document) => el.querySelector(s);
const fmt$ = v => v==null?'—':('$'+Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2}));
const fmtK = v => v==null?'—':(Math.abs(v)>=1e6?'$'+(v/1e6).toFixed(1)+'M':Math.abs(v)>=1e4?'$'+(v/1e3).toFixed(1)+'k':'$'+Math.round(v));
const pct = v => v==null?'—':((v*100).toFixed(1)+'%');
const num = (v,d=2) => v==null?'—':Number(v).toFixed(d);
const esc = s => String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// ---------- theme toggle ----------
$('#theme').onclick = () => { const r=document.documentElement; const cur=r.getAttribute('data-theme')||(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'); r.setAttribute('data-theme', cur==='dark'?'light':'dark'); render(); };

// ---------- SVG line chart (one axis, crosshair tooltip, table twin) ----------
function lineChart(el, series, opts={}) {
  // series: [{name, color, points:[[x(date str), y]]}]
  el.innerHTML='';
  const longest = Math.max(8, ...series.map(s=>s.name.length));
  const W = Math.max(320, el.clientWidth||800), H = opts.height||260, m={t:12,r:Math.min(170, 14+longest*6.4),b:28,l:52};
  const xs = [...new Set(series.flatMap(s=>s.points.map(p=>p[0])))].sort();
  const xi = new Map(xs.map((x,i)=>[x,i]));
  const ys = series.flatMap(s=>s.points.map(p=>p[1])).filter(v=>v!=null && isFinite(v));
  if(!xs.length||!ys.length){ el.innerHTML='<div class="muted small">no data yet</div>'; return; }
  let y0=Math.min(...ys), y1=Math.max(...ys); if(opts.zero) y0=Math.min(0,y0); if(y1===y0){y1=y0+1}
  const pad=(y1-y0)*0.06, allPos=Math.min(...ys)>=0, allNeg=!allPos&&Math.max(...ys)<=0; y0-=pad; y1+=pad;
  if(allPos) y0=Math.max(0,y0); if(allNeg) y1=Math.min(0,y1);  // never pad an axis across zero
  const X = i => m.l + (xs.length>1? i/(xs.length-1):0.5)*(W-m.l-m.r);
  const Y = v => m.t + (1-(v-y0)/(y1-y0))*(H-m.t-m.b);
  const ns='http://www.w3.org/2000/svg'; const svg=document.createElementNS(ns,'svg'); svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const g=(tag,a)=>{const e=document.createElementNS(ns,tag); for(const k in a) e.setAttribute(k,a[k]); return e;};
  // grid + y ticks
  const ticks=5; for(let i=0;i<=ticks;i++){ const v=y0+(y1-y0)*i/ticks; svg.appendChild(g('line',{x1:m.l,x2:W-m.r,y1:Y(v),y2:Y(v),stroke:css('--grid'),'stroke-width':1}));
    const t=g('text',{x:m.l-6,y:Y(v)+4,'text-anchor':'end','font-size':11,fill:css('--muted')}); t.textContent=opts.fmt?opts.fmt(v):num(v,0); svg.appendChild(t); }
  svg.appendChild(g('line',{x1:m.l,x2:W-m.r,y1:H-m.b,y2:H-m.b,stroke:css('--axis'),'stroke-width':1}));
  // x labels (first, middle, last)
  [0,Math.floor((xs.length-1)/2),xs.length-1].forEach((i,k)=>{ const t=g('text',{x:X(i),y:H-8,'text-anchor':k===0?'start':k===2?'end':'middle','font-size':11,fill:css('--muted')}); t.textContent=xs[i]; svg.appendChild(t);});
  // series (direct end labels are de-collided: sorted by y, pushed >= 13px apart)
  const labels=[];
  series.forEach(s=>{ const pts=s.points.filter(p=>p[1]!=null && isFinite(p[1])); if(!pts.length) return;
    if(s.area){ const d=pts.map((p,i)=>(i?'L':'M')+X(xi.get(p[0]))+','+Y(p[1])).join(' ')+` L${X(xi.get(pts[pts.length-1][0]))},${Y(0)} L${X(xi.get(pts[0][0]))},${Y(0)} Z`; svg.appendChild(g('path',{d,fill:s.color,opacity:.18})); }
    const d=pts.map((p,i)=>(i?'L':'M')+X(xi.get(p[0]))+','+Y(p[1])).join(' ');
    svg.appendChild(g('path',{d,fill:'none',stroke:s.color,'stroke-width':2,'stroke-linejoin':'round'}));
    const last=pts[pts.length-1]; labels.push({x:X(xi.get(last[0]))+6,y:Y(last[1])+4,name:s.name,color:s.color}); });
  labels.sort((a,b)=>a.y-b.y); for(let i=1;i<labels.length;i++){ if(labels[i].y-labels[i-1].y<13) labels[i].y=labels[i-1].y+13; }
  labels.forEach(l=>{ const t=g('text',{x:l.x,y:l.y,'font-size':11,fill:css('--ink2')}); t.textContent=l.name; svg.appendChild(t);
    const sw=g('rect',{x:l.x-5,y:l.y-5,width:3,height:3,fill:l.color}); svg.appendChild(sw); });
  // crosshair + tooltip
  const cross=g('line',{x1:0,x2:0,y1:m.t,y2:H-m.b,stroke:css('--axis'),'stroke-width':1,visibility:'hidden'}); svg.appendChild(cross);
  const tip=document.createElement('div'); tip.className='tt'; el.appendChild(svg); el.appendChild(tip);
  const hit=g('rect',{x:m.l,y:m.t,width:W-m.l-m.r,height:H-m.t-m.b,fill:'transparent'}); svg.appendChild(hit);
  hit.addEventListener('mousemove',ev=>{ const r=svg.getBoundingClientRect(); const px=(ev.clientX-r.left)*W/r.width; const i=Math.round(((px-m.l)/(W-m.l-m.r))*(xs.length-1)); const ii=Math.max(0,Math.min(xs.length-1,i)); const x=xs[ii]; cross.setAttribute('x1',X(ii)); cross.setAttribute('x2',X(ii)); cross.setAttribute('visibility','visible');
    const rows=series.map(s=>{const p=s.points.find(q=>q[0]===x); return p&&p[1]!=null?`<div><i style="display:inline-block;width:10px;height:2px;background:${s.color};vertical-align:middle;margin-right:6px"></i>${esc(s.name)}: <b>${opts.fmt?opts.fmt(p[1]):num(p[1])}</b></div>`:''}).join('');
    tip.innerHTML=`<div class="muted">${esc(x)}</div>${rows}`; tip.style.display='block'; const left=(ev.clientX-r.left)+12; tip.style.left=Math.min(left,r.width-180)+'px'; tip.style.top=(ev.clientY-r.top-10)+'px'; });
  hit.addEventListener('mouseleave',()=>{cross.setAttribute('visibility','hidden'); tip.style.display='none';});
  // table twin
  const tw=document.createElement('div'); tw.className='tablewrap'; const head=series.map(s=>`<th class="num">${esc(s.name)}</th>`).join('');
  const body=xs.map(x=>`<tr><td>${esc(x)}</td>${series.map(s=>{const p=s.points.find(q=>q[0]===x); return `<td class="num">${p&&p[1]!=null?(opts.fmt?opts.fmt(p[1]):num(p[1])):'—'}</td>`}).join('')}</tr>`).join('');
  tw.innerHTML=`<table><thead><tr><th>date</th>${head}</tr></thead><tbody>${body}</tbody></table>`; el.appendChild(tw);
  return tw;
}
function legend(series){ return `<div class="legend">${series.map(s=>`<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join('')}</div>`; }
function card(title, cls, inner, id){ return `<section class="card ${cls||''}"><div class="row"><h2>${esc(title)}</h2><div class="tools">${id?`<button data-table="${id}">table</button>`:''}</div></div>${inner}</section>`; }
function statusPill(txt, col){ return `<span class="status"><span class="dot" style="background:${col}"></span>${esc(txt)}</span>`; }

function render(){
  const S=D.survival, st=S.status||{}, E=D.equity, L=D.latest||{}, P=(L.primary||{}), H=D.hive||{};
  const start=S.starting_bankroll, net=st.equity_net, gross=st.equity_gross;
  const days=E.dates.length, retTot = (net!=null&&start)? net/start-1 : null;
  const burn = S.ai_cost_total||0, hurdle = (days>0&&net)? Math.pow(1+ (burn/days)/net,365)-1 : null;
  $('#subtitle').textContent=`${D.strategy.name} v${D.strategy.version} · ${days} run days · last run ${S.last_date||'—'} · generated ${D.generated_at}`;
  let html='';
  if(S.death){ html+=`<div class="banner"><b>☠ The system died on ${esc(S.death.date)}</b> — ${esc(S.death.reason)}. Final equity ${fmt$(S.death.equity)}. No resurrection.</div>`; }
  const regime=(P.regime||{}).state||'—'; const regCol={green:css('--good'),yellow:css('--warn'),red:css('--crit')}[regime]||css('--muted');
  const tierCol = {hospice:css('--crit'),survival:css('--serious'),base:css('--s1'),growth:css('--s3'),hive:css('--good')}[st.tier]||css('--muted');
  html+=`<div class="tiles">
   <div class="tile"><div class="label">Net equity (after AI costs)</div><div class="value">${fmt$(net)}</div><div class="delta">${retTot==null?'':(retTot>=0?'▲ ':'▼ ')+pct(retTot)+' since start'}</div></div>
   <div class="tile"><div class="label">Gross equity</div><div class="value">${fmt$(gross)}</div><div class="delta">AI costs paid ${fmt$(burn)}</div></div>
   <div class="tile"><div class="label">Tier</div><div class="value">${esc(st.tier||'—')}</div><div class="delta">${statusPill('runway '+(st.runway_days==null?'∞':Math.round(st.runway_days)+' d'), tierCol)}</div></div>
   <div class="tile"><div class="label">AI budget today</div><div class="value">${fmt$(st.budget_today)}</div><div class="delta">spent ${fmt$(st.ai_cost_today)} · hurdle ${hurdle==null?'—':pct(hurdle)}/yr</div></div>
   <div class="tile"><div class="label">Regime</div><div class="value" style="text-transform:capitalize">${esc(regime)}</div><div class="delta">${statusPill(((P.regime||{}).crash_guard?'crash guard ON':'crash guard off'), regCol)}</div></div>
   <div class="tile"><div class="label">Integrity</div><div class="value">${S.chain_ok&&!(D.journal.broken_chains||[]).length?'✓':'✗'}</div><div class="delta">ledger ${S.chain_ok?'ok':'BROKEN'} · journals ${(D.journal.broken_chains||[]).length?'BROKEN':'ok'} · ${D.journal.anomalies_30d} names skipped by the data-quality guard (30d)</div></div>
  </div>`;
  // equity chart (indexed to start)
  const idx=(arr)=>arr.map((v,i)=>[E.dates[i], v==null?null:v/start*100]);
  const series=[{name:'rules + LLM (net)',color:css('--s1'),points:idx(E.primary_net)},
                {name:'rules-only shadow',color:css('--s2'),points:E.dates.map(d=>[d,E.shadow[d]==null?null:E.shadow[d]/start*100])}];
  const bk=Object.entries(D.benchmarks||{}); const bcol=[css('--s3'),css('--s4')];
  bk.slice(0,2).forEach(([k,v],i)=>series.push({name:k,color:bcol[i],points:E.dates.map(d=>[d,v[d]==null?null:v[d]/start*100])}));
  html+=card('Equity, indexed to 100 at start — the three lines the study publishes','',legend(series)+'<div class="chart" id="eq"></div>','eq');
  // drawdown + cost
  const peak=[]; let pk=-Infinity; const dd=E.primary_net.map((v,i)=>{pk=Math.max(pk,v); return [E.dates[i], v/pk-1]});
  html+=card('Drawdown (net)','c6',legend([{name:'drawdown',color:css('--s1')}])+'<div class="chart" id="dd"></div>','dd');
  html+=card('Cumulative AI spend paid from the bankroll','c6',legend([{name:'AI + data cost',color:css('--s1')}])+'<div class="chart" id="cost"></div>','cost');
  // positions
  const pos=D.positions.primary||[]; const posRows=pos.map(p=>`<tr><td><b>${esc(p.symbol)}</b> <span class="pill">${esc(p.asset_class)}</span></td><td class="num">${num(p.qty,4)}</td><td class="num">${fmt$(p.entry)}</td><td class="num">${fmt$(p.stop)}</td><td class="num">${p.entry?pct(p.stop/p.entry-1):'—'}</td><td class="num">${p.bars_held}</td><td>${esc(p.entry_date)}</td><td>${p.fill_pending?'<span class="pill">fill pending</span>':(p.stop_armed?'✓ stop':'<span class="pill" style="color:var(--crit)">no stop</span>')}</td><td class="muted">${esc(p.invalidation)}</td></tr>`).join('');
  html+=card(`Open positions (primary) — ${pos.length}`,'c8',`<table><thead><tr><th>symbol</th><th class="num">qty</th><th class="num">entry</th><th class="num">stop</th><th class="num">stop dist</th><th class="num">bars</th><th>entered</th><th>status</th><th>invalidation</th></tr></thead><tbody>${posRows||'<tr><td colspan="9" class="muted">none</td></tr>'}</tbody></table>`);
  // tier ladder
  const tiers=S.tiers||[]; const ord=['--ord1','--ord2','--ord3','--ord4','--ord5'];
  html+=card('Survival tiers (unlocks as equity grows)','c4',`<div class="ladder">${tiers.map((t,i)=>`<div class="rung ${t.name===st.tier?'cur':''}" style="background:${css(ord[i]||'--ord5')}"><b>${esc(t.name)}</b><br>≥ ${fmt$(t.min_equity_usd)}<br>risk ×${t.risk_multiplier} · children ${t.max_children}${t.close_only?' · close-only':''}</div>`).join('')}</div><div class="small muted" style="margin-top:8px">death floor ${fmt$(S.death_floor)} or −75% from peak · halts: ${(S.halts||[]).length} · tier changes: ${(S.tier_changes||[]).map(t=>esc(t.to)).join(' → ')||'—'}</div>`);
  // latest decisions
  const dec=(P.decisions||[]); const decRows=dec.map(d=>`<tr><td><b>${esc(d.symbol)}</b></td><td>${esc(d.action)}</td><td class="muted">${esc(d.invalidation)}</td><td class="num">${d.confidence==null?'—':num(d.confidence,2)}</td><td>${esc(d.rationale)}</td><td class="num">${fmt$(d.cost_usd)}</td></tr>`).join('');
  const orders=(P.orders||[]).map(o=>`<li>${esc(o.side)} <b>${esc(o.symbol)}</b> ${fmt$(o.notional)} — ${o.ok?'ok':'FAILED'} ${esc(o.message)}</li>`).join('');
  const exits=(P.exits||[]).map(x=>`<li><b>${esc(x.symbol)}</b> ${esc(x.reason)} ${x.ok?'':'(FAILED)'}</li>`).join('');
  html+=card(`Latest run — ${esc(P.run_date||'—')}`,'c8',`<div class="small muted" style="margin-bottom:6px">${(P.notes||[]).map(esc).join(' · ')||'no notes'}</div><table><thead><tr><th>candidate</th><th>action</th><th>invalidation</th><th class="num">conf.</th><th>rationale</th><th class="num">cost</th></tr></thead><tbody>${decRows||'<tr><td colspan="6" class="muted">no candidates</td></tr>'}</tbody></table><div class="row" style="margin-top:8px"><div style="flex:1"><b class="small">Orders</b><ul class="small">${orders||'<li class="muted">none</li>'}</ul></div><div style="flex:1"><b class="small">Exits</b><ul class="small">${exits||'<li class="muted">none</li>'}</ul></div></div>`);
  // hive children / iterations
  const ch=(H.children||[]); const ovs=o=>Object.entries(o||{}).map(([k,v])=>`${k.replace(/^stocks\./,'')}=${v}`).join(', ');
  const chRows=ch.map(c=>`<tr><td><b>${esc(c.name)}</b> <span class="pill">${esc(c.sleeve)}</span><br><code class="ov">${esc(ovs(c.overrides)||'parent config')}</code></td><td>${esc(c.status)}<br><span class="muted small">${c.shadow_days} d</span></td><td class="num">${fmt$(c.equity)}<br><span class="small ${c.voi>0?'':'muted'}">VOI ${c.voi==null?'—':(c.voi>=0?'+':'')+pct(c.voi)}</span></td></tr>`).join('');
  const it=(H.iterations||[]).slice(-12).reverse().map(i=>`<li style="margin:0 0 8px"><span class="muted small">${esc(i.date)}</span> <b>${esc(i.proposal||'')}</b> <code class="ov">${esc(ovs(i.overrides))}</code><br><span class="small">IS ${i.is_cagr==null?'—':pct(i.is_cagr)} · OOS ${i.oos_cagr==null?'—':pct(i.oos_cagr)}${i.parent_oos_cagr==null?'':' (parent '+pct(i.parent_oos_cagr)+')'} — ${esc(i.verdict||'')}</span></li>`).join('');
  html+=card(`Hive — children ${ch.length} (tier allows ${(tiers.find(t=>t.name===st.tier)||{}).max_children??'—'})`,'c4',`<table><thead><tr><th>child</th><th>status</th><th class="num">equity</th></tr></thead><tbody>${chRows||'<tr><td colspan="3" class="muted">no children yet — spawned by the operator when the tier allows and shadow results earn it</td></tr>'}</tbody></table><h2 style="margin-top:12px">Operator iterations</h2><ul style="padding-left:16px;margin:0">${it||'<li class="muted">none yet</li>'}</ul>`);
  // backtests
  const bt=D.backtests||[]; const btRows=bt.map(b=>`<tr><td><b>${esc(b.name)}</b> <span class="pill">v${esc(b.config_version||'')}</span></td><td class="num">${pct(b.cagr)}</td><td class="num">${num(b.sharpe)}</td><td class="num">${pct(b.max_drawdown)}</td><td class="num">${b.n_trades}</td><td class="num">${pct(b.win_rate)}</td><td class="num">${num(b.profit_factor)}</td><td class="num">${num(b.avg_hold_days,0)}d</td><td class="num">${pct(b.avg_exposure)}</td><td class="num muted">${pct(b.benchmark.cagr)} / ${pct(b.benchmark.max_drawdown)}</td></tr>`).join('');
  html+=card('Backtests (cleaned data, next-open fills, costs) — benchmark = buy & hold','',`<table><thead><tr><th>run</th><th class="num">CAGR</th><th class="num">Sharpe</th><th class="num">max DD</th><th class="num">trades</th><th class="num">win</th><th class="num">PF</th><th class="num">hold</th><th class="num">exposure</th><th class="num">bench CAGR / DD</th></tr></thead><tbody>${btRows||'<tr><td colspan="10" class="muted">none</td></tr>'}</tbody></table>`);
  bt.filter(b=>b.curve&&b.curve.dates.length).slice(0,3).forEach((b,i)=>{ html+=card(`Backtest equity — ${b.name} (log scale)`,'c6',legend([{name:'strategy',color:css('--s1')},{name:'buy & hold',color:css('--s3')}])+`<div class="chart" id="bt${i}"></div>`,`bt${i}`); });
  // experiments
  const ex=D.experiments||{}; const exKeys=Object.keys(ex);
  if(exKeys.length){ html+=card('Experiments (robustness grid, null tests, walk-forward) — every cell, not just the winner','',exKeys.map(k=>{const rows=ex[k]; const cols=Object.keys(rows[0]||{}).filter(c=>!['extra'].includes(c)).slice(0,12); return `<details><summary class="small">${esc(k)} (${rows.length} rows)</summary><div style="overflow:auto"><table><thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td class="num">${esc(isNaN(parseFloat(r[c]))?r[c]:Number(r[c]).toFixed(4))}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`}).join('')); }
  // llm calls
  const calls=D.journal.llm_calls||[]; const byRole={}; calls.forEach(c=>{byRole[c.role]=byRole[c.role]||{n:0,cost:0,fail:0}; byRole[c.role].n++; byRole[c.role].cost+=c.cost_usd||0; byRole[c.role].fail+=c.ok?0:1;});
  html+=card('LLM calls (last 30 run days)','c6',`<table><thead><tr><th>role</th><th class="num">calls</th><th class="num">failed</th><th class="num">cost</th></tr></thead><tbody>${Object.entries(byRole).map(([r,v])=>`<tr><td>${esc(r)}</td><td class="num">${v.n}</td><td class="num">${v.fail}</td><td class="num">${fmt$(v.cost)}</td></tr>`).join('')||'<tr><td colspan="4" class="muted">no LLM calls yet (rules-only)</td></tr>'}</tbody></table>`);
  // recent activity
  const rec=(D.journal.recent||[]).slice(-40).reverse().map(r=>`<tr><td>${esc(r.date)}</td><td>${esc(r.kind)}</td><td><b>${esc(r.symbol)}</b></td><td>${esc(r.action||r.side||r.reason||'')}</td><td class="muted">${esc(r.rationale||r.message||'')}</td></tr>`).join('');
  html+=card('Recent activity (decisions, orders, exits)','c6',`<div style="max-height:300px;overflow:auto"><table><thead><tr><th>date</th><th>kind</th><th>symbol</th><th>what</th><th>note</th></tr></thead><tbody>${rec||'<tr><td colspan="5" class="muted">none</td></tr>'}</tbody></table></div>`);
  $('#main').innerHTML=html;
  $('#foot').innerHTML=`Not investment advice. Every number above is read from the hash-chained ledger and journals in <code>state/</code>; the strategy rules are pre-registered in <code>config/strategy.toml</code>.`;
  // charts
  const tables={};
  tables.eq=lineChart($('#eq'),series,{fmt:v=>num(v,1)});
  tables.dd=lineChart($('#dd'),[{name:'drawdown',color:css('--s1'),points:dd,area:true}],{fmt:v=>pct(v),zero:true});
  tables.cost=lineChart($('#cost'),[{name:'AI + data cost',color:css('--s1'),points:E.dates.map((d,i)=>[d,E.cum_ai_cost[i]])}],{fmt:v=>fmt$(v),zero:true});
  bt.filter(b=>b.curve&&b.curve.dates.length).slice(0,3).forEach((b,i)=>{ const lg=v=>Math.log10(v); tables['bt'+i]=lineChart($('#bt'+i),[{name:'strategy',color:css('--s1'),points:b.curve.dates.map((d,j)=>[d,lg(b.curve.equity[j])])},{name:'buy & hold',color:css('--s3'),points:b.curve.dates.map((d,j)=>[d,b.curve.benchmark[j]==null?null:lg(b.curve.benchmark[j])])}],{fmt:v=>fmtK(Math.pow(10,v))}); });
  document.querySelectorAll('button[data-table]').forEach(b=>b.onclick=()=>{const t=tables[b.dataset.table]; if(t) t.classList.toggle('show');});
}
render(); addEventListener('resize',()=>{clearTimeout(window.__r); window.__r=setTimeout(render,150);});
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
    (out_dir / "data.json").write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    (out_dir / ".nojekyll").write_text("")
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
    print(f"HIVE dashboard on http://127.0.0.1:{port}/  (Ctrl-C to stop)")
    http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
