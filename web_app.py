#!/usr/bin/env python
"""Real-time web dashboard for the World Cup 2026 live edge engine.

Serves a single, phone-and-desktop-native page over ``data/processed/dashboard_state.json``
and pushes every change to the browser instantly with Server-Sent Events (no polling lag).
The state file is produced by the always-on ``live_engine.py`` (the single writer); this
process is a read-only reader plus an SSE fan-out, so it's safe to run behind a reverse
proxy (Caddy/nginx) or `tailscale serve`.

    python web_app.py --host 127.0.0.1 --port 8000     # behind a proxy / tailscale serve
    python web_app.py --host 0.0.0.0  --port 8000      # direct LAN access

Endpoints:
    GET  /            -> the dashboard
    GET  /state.json  -> current state snapshot
    GET  /events      -> text/event-stream, pushes state on every change (SSE)
    POST /trigger     -> ask the engine to re-simulate / refresh now (writes engine_control.json)
    GET  /healthz     -> "ok"

Design: 2026 cyber-brutalist + liquid glass, light mode. Pure stdlib server.
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "processed" / "dashboard_state.json"
CONTROL = ROOT / "data" / "processed" / "engine_control.json"

INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#f4f1e6">
<title>WORLD CUP '26 · EDGE ENGINE</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,500;12..96,700;12..96,800&family=Martian+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root{
    --paper:#f4f1e6; --paper2:#eee9da; --ink:#15160f; --ink2:#3a3b30;
    --glass:rgba(255,255,255,.74); --glass2:rgba(255,255,255,.84);  /* opaque enough to drop per-tile blur (perf) */
    --line:#15160f;            /* hard 1px borders, brutalist */
    --lime:#c8ff00;            /* cyber-neon green accent (fills only) */
    --lime-d:#0a8f4f;          /* readable green for +text */
    --blue:#1f3bff;            /* electric blue (interactive) */
    --blue-soft:rgba(31,59,255,.10);
    --red:#ff2e2e; --red-d:#cf1b1b; --amber:#ff7a00;
    --dim:#5f6154; --faint:#8c8e7e;
    --disp:"Bricolage Grotesque",sans-serif;
    --mono:"Martian Mono",ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  }
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  html{scroll-behavior:smooth}
  body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--disp);font-weight:500;
    -webkit-font-smoothing:antialiased;overflow-x:hidden;position:relative;min-height:100vh;}
  /* drifting neon blobs behind the glass */
  .blob{position:fixed;border-radius:50%;filter:blur(60px);opacity:.5;z-index:0;pointer-events:none}
  .b1{width:46vw;height:46vw;left:-10vw;top:-12vw;background:radial-gradient(circle,var(--lime),transparent 65%);animation:drift1 26s ease-in-out infinite}
  .b2{width:42vw;height:42vw;right:-12vw;top:8vh;background:radial-gradient(circle,#7d8bff,transparent 64%);animation:drift2 31s ease-in-out infinite}
  .b3{width:38vw;height:38vw;left:18vw;bottom:-16vw;background:radial-gradient(circle,#9affd6,transparent 66%);animation:drift1 37s ease-in-out infinite reverse}
  @keyframes drift1{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(7vw,5vh) scale(1.12)}}
  @keyframes drift2{0%,100%{transform:translate(0,0) scale(1)}50%{transform:translate(-6vw,7vh) scale(1.08)}}
  /* Respect reduced-motion: freeze the drifting blobs, ticker tape and pulses (perf + a11y). */
  @media (prefers-reduced-motion: reduce){
    .blob,.tt,.led.on,.lg .pulse,.tag.live .led,.job .jled.running{animation:none!important}
    *{scroll-behavior:auto!important}
  }
  /* global grain overlay */
  .grain{position:fixed;inset:0;z-index:60;pointer-events:none;opacity:.5;mix-blend-mode:multiply;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='220' height='220'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.86' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.32'/%3E%3C/svg%3E");}
  .mono{font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.02em}
  .pos{color:var(--lime-d)} .neg{color:var(--red-d)} .muted{color:var(--dim)}

  /* ---------- header ---------- */
  header{position:sticky;top:0;z-index:40;background:rgba(244,241,230,.82);
    backdrop-filter:blur(10px) saturate(150%);-webkit-backdrop-filter:blur(10px) saturate(150%);
    border-bottom:1px solid var(--line);}
  .bar{display:flex;align-items:center;gap:13px;max-width:1320px;margin:0 auto;
    padding:calc(10px + env(safe-area-inset-top)) 18px 9px;}
  .mark{display:flex;align-items:center;gap:10px;min-width:0}
  .badge{width:34px;height:34px;border:1px solid var(--line);background:var(--lime);display:grid;place-items:center;
    font-size:18px;flex:0 0 auto}
  .wordmark{font-family:var(--disp);font-weight:800;font-size:16px;letter-spacing:-.01em;line-height:1;text-transform:uppercase;white-space:nowrap}
  .wordmark .slash{color:var(--blue)}
  .substat{font-family:var(--mono);font-size:10px;color:var(--dim);margin-top:3px;letter-spacing:0;white-space:nowrap;text-transform:uppercase}
  .grow{flex:1}
  .conn{display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:10.5px;text-transform:uppercase;
    border:1px solid var(--line);padding:6px 11px;background:var(--glass2);white-space:nowrap;letter-spacing:.02em}
  .led{width:8px;height:8px;flex:0 0 auto;background:var(--faint)}
  .led.on{background:var(--lime);box-shadow:0 0 0 0 rgba(200,255,0,.9);animation:ping 1.7s infinite}
  .led.off{background:var(--amber)}
  @keyframes ping{0%{box-shadow:0 0 0 0 rgba(168,214,0,.7)}70%{box-shadow:0 0 0 7px rgba(168,214,0,0)}100%{box-shadow:0 0 0 0 rgba(168,214,0,0)}}
  .ibtn{appearance:none;border:1px solid var(--line);background:var(--ink);color:var(--paper);width:36px;height:36px;
    font-size:15px;cursor:pointer;display:grid;place-items:center;flex:0 0 auto;transition:.12s}
  .ibtn:hover{background:var(--blue)} .ibtn:active{transform:translate(1px,1px)} .ibtn.spin{animation:spin .8s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}

  /* ---------- ticker tape ---------- */
  .ticker{position:relative;border-bottom:1px solid var(--line);background:var(--ink);color:var(--paper);overflow:hidden;height:34px}
  .ticker .grainstrip{position:absolute;inset:0;opacity:.35;mix-blend-mode:overlay;pointer-events:none;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='m'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23m)'/%3E%3C/svg%3E");}
  .tt{display:inline-flex;align-items:center;height:34px;white-space:nowrap;will-change:transform;animation:tape 42s linear infinite}
  .ticker:hover .tt{animation-play-state:paused}
  @keyframes tape{from{transform:translateX(0)}to{transform:translateX(-50%)}}
  .ti{display:inline-flex;align-items:center;gap:8px;font-family:var(--mono);font-size:11.5px;padding:0 20px;text-transform:uppercase;letter-spacing:.02em}
  .ti b{color:var(--lime);font-weight:700} .ti .up{color:var(--lime)} .ti .dn{color:#ff7b7b}
  .ti.livetick b{color:#fff} .ti.livetick{background:rgba(255,46,46,.35)}
  .ti::after{content:"";width:5px;height:5px;background:var(--blue);margin-left:18px}

  /* live-now banner under the ticker */
  .livebanner{display:none;border-bottom:1px solid var(--line);background:var(--lime)}
  .livebanner.on{display:flex;gap:0;overflow-x:auto;scrollbar-width:none}
  .livebanner::-webkit-scrollbar{display:none}
  .lg{display:flex;align-items:center;gap:9px;flex:0 0 auto;padding:8px 16px;border-right:1px solid var(--line);
    font-family:var(--mono);font-size:12px;white-space:nowrap;text-transform:uppercase;letter-spacing:.02em}
  .lg .pulse{width:8px;height:8px;background:var(--red);border:1px solid var(--ink);animation:ping 1.1s infinite;flex:0 0 auto}
  .lg .sc{font-weight:700} .lg .min{color:var(--ink2)} .lg .pk{color:var(--ink2);font-size:10.5px}

  nav{display:flex;gap:0;overflow-x:auto;max-width:1320px;margin:0 auto;padding:11px 18px 0;scrollbar-width:none}
  nav::-webkit-scrollbar{display:none}
  nav button{flex:0 0 auto;border:1px solid var(--line);border-right:0;background:var(--glass);color:var(--ink2);
    font-family:var(--mono);font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;padding:9px 15px;cursor:pointer;transition:.12s}
  nav button:last-child{border-right:1px solid var(--line)}
  nav button:hover{background:var(--glass2)}
  nav button.on{background:var(--blue);color:#fff;border-color:var(--line)}

  /* ---------- kinetic hero ---------- */
  .hero{max-width:1320px;margin:0 auto;padding:22px 18px 4px;overflow:hidden}
  .kinetic{font-family:var(--disp);font-weight:800;text-transform:uppercase;line-height:.86;
    font-size:clamp(34px,8.5vw,104px);letter-spacing:-.03em;will-change:transform;margin:0;color:var(--ink)}
  .kinetic .out{-webkit-text-stroke:1.4px var(--ink);color:transparent}
  .kinetic .fill{background:var(--lime);padding:0 .08em;color:var(--ink)}
  .kinetic .bl{color:var(--blue)}
  .heroline{font-family:var(--mono);font-size:11px;text-transform:uppercase;color:var(--dim);margin-top:8px;letter-spacing:.05em}

  /* ---------- layout ---------- */
  main{max-width:1320px;margin:0 auto;padding:8px 18px 92px}
  section{display:none}
  section.on{display:block}
  .bento{display:grid;grid-template-columns:repeat(12,1fr);grid-auto-rows:minmax(92px,auto);gap:14px;grid-auto-flow:row dense}
  .c2{grid-column:span 2}.c3{grid-column:span 3}.c4{grid-column:span 4}.c5{grid-column:span 5}
  .c6{grid-column:span 6}.c7{grid-column:span 7}.c8{grid-column:span 8}.c12{grid-column:span 12}
  .r2{grid-row:span 2}.r3{grid-row:span 3}
  @media(max-width:860px){.c2,.c3,.c4,.c5,.c6,.c7,.c8{grid-column:span 6}}
  @media(max-width:560px){.c2,.c3,.c4,.c5,.c6,.c7,.c8{grid-column:span 12}.r2,.r3{grid-row:span 1}}

  /* ---------- panels (liquid glass + hard border, NO shadow) ---------- */
  /* No per-tile backdrop-filter: the blobs are already blurred, so re-blurring the backdrop
     behind every bento tile each frame (while the blobs drift) was the main scroll-lag source.
     A near-opaque glass keeps the frosted look at a fraction of the GPU cost. */
  .p{position:relative;background:var(--glass);border:1px solid var(--line);padding:15px 16px;
    overflow:hidden;transition:transform .18s cubic-bezier(.2,.8,.2,1),background .18s,border-color .18s;
    animation:rise .5s cubic-bezier(.2,.8,.2,1) backwards}
  .p:hover{transform:translateY(-3px);background:var(--glass2);border-color:var(--blue)}
  @keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}
  .p .corner{position:absolute;top:-1px;right:-1px;width:0;height:0;border-top:14px solid var(--lime);border-left:14px solid transparent}
  .lab{font-family:var(--mono);font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);display:flex;align-items:center;gap:7px}
  .lab .tag{margin-left:auto}
  .stat{font-family:var(--disp);font-weight:800;letter-spacing:-.03em;line-height:.95;margin-top:9px;font-size:clamp(26px,3.6vw,40px)}
  .stat.sm{font-size:clamp(19px,2.4vw,24px)}
  .sub{font-family:var(--mono);font-size:10.5px;color:var(--dim);margin-top:7px;text-transform:uppercase;letter-spacing:.02em}

  .sectitle{font-family:var(--disp);font-weight:800;text-transform:uppercase;font-size:13px;letter-spacing:.02em;
    color:var(--ink);margin:26px 2px 13px;display:flex;align-items:center;gap:10px}
  .sectitle::before{content:"";width:11px;height:11px;background:var(--lime);border:1px solid var(--line);flex:0 0 auto}
  .sectitle .tag{margin-left:auto}

  .tag{font-family:var(--mono);font-size:9.5px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
    border:1px solid var(--line);padding:3px 8px;background:var(--glass2);color:var(--ink2);display:inline-flex;align-items:center;gap:5px;white-space:nowrap}
  .tag.live{background:var(--lime);color:var(--ink)} .tag.live .led{width:6px;height:6px;background:var(--ink);animation:ping 1.7s infinite}
  .tag.buy{background:var(--lime);color:var(--ink)} .tag.no{background:var(--amber);color:var(--ink)}
  .tag.act{background:var(--blue);color:#fff} .tag.win{background:var(--lime);color:var(--ink)} .tag.loss{background:var(--red);color:#fff}

  .row{display:flex;flex-wrap:wrap;align-items:center;gap:8px;border:1px solid var(--line);background:var(--glass);
    padding:11px 13px;margin-bottom:9px;transition:.13s;cursor:default}
  .row:hover{background:var(--lime);border-color:var(--line);transform:translateX(3px)}
  .nm{font-family:var(--disp);font-weight:700;font-size:14.5px;letter-spacing:-.01em}
  .big{margin-left:auto;font-family:var(--mono);font-weight:700;font-size:16px}
  .meta{display:flex;flex-wrap:wrap;gap:3px 14px;font-family:var(--mono);font-size:10.5px;color:var(--dim);margin-top:5px;width:100%;text-transform:uppercase;letter-spacing:.01em}
  .meta b{color:var(--ink);font-weight:600}

  /* hover-responsive bars */
  .barwrap{margin-bottom:10px;cursor:crosshair}
  .barhead{display:flex;align-items:baseline;gap:8px}
  .barhead .nm{font-size:13.5px}.barhead .v{margin-left:auto;font-family:var(--mono);font-weight:700;font-size:13px}
  .track{height:8px;border:1px solid var(--line);background:rgba(255,255,255,.4);margin-top:7px;overflow:hidden}
  .fill{height:100%;background:repeating-linear-gradient(90deg,var(--blue),var(--blue) 6px,#3a52ff 6px,#3a52ff 12px);
    width:0;transition:width .7s cubic-bezier(.2,.8,.2,1),background .15s}
  .barwrap:hover .fill{background:var(--lime)}
  .barwrap:hover .track{height:13px;transition:height .15s}
  .barwrap .extra{max-height:0;overflow:hidden;transition:max-height .2s;font-family:var(--mono);font-size:10px;color:var(--dim);text-transform:uppercase}
  .barwrap:hover .extra{max-height:24px;margin-top:5px}

  /* interactive equity chart */
  .chart{position:relative;margin-top:12px;cursor:crosshair}
  .chart svg{display:block;width:100%;height:120px}
  .chart .cross{position:absolute;top:0;bottom:0;width:1px;background:var(--blue);opacity:0;pointer-events:none}
  .chart .tip{position:absolute;transform:translate(-50%,-130%);background:var(--ink);color:var(--paper);
    font-family:var(--mono);font-size:10px;padding:3px 7px;white-space:nowrap;opacity:0;pointer-events:none;border:1px solid var(--lime)}
  .chart:hover .cross,.chart:hover .tip{opacity:1}

  .deny{font-family:var(--mono);font-size:10px;text-transform:uppercase;letter-spacing:.04em;border:1px solid var(--line);
    background:var(--glass2);color:var(--ink2);padding:4px 10px;cursor:pointer;transition:.12s}
  .deny:hover{background:var(--red);color:#fff;border-color:var(--line)}
  .deny[disabled]{opacity:.5;cursor:default;background:var(--glass2);color:var(--ink2)}
  .note{border:1px solid var(--line);border-left:4px solid var(--blue);background:var(--glass);
    padding:11px 13px;margin-bottom:9px;font-size:13px;line-height:1.55;color:var(--ink2)}
  .note b{color:var(--ink)}
  .empty{font-family:var(--mono);font-size:11.5px;text-transform:uppercase;color:var(--faint);text-align:center;padding:30px 10px;letter-spacing:.04em}

  /* flash on change (light mode) */
  @keyframes fu{0%{background:var(--lime)}100%{background:transparent}}
  @keyframes fd{0%{background:#ffb3b3}100%{background:transparent}}
  .fu{animation:fu 1s ease-out}.fd{animation:fd 1s ease-out}

  /* engine bar */
  .engine{position:fixed;left:0;right:0;bottom:0;z-index:45;background:var(--ink);color:var(--paper);
    border-top:1px solid var(--line);padding:7px max(18px,env(safe-area-inset-left)) calc(7px + env(safe-area-inset-bottom))}
  .engine-in{max-width:1320px;margin:0 auto;display:flex;align-items:center;gap:6px 18px;overflow-x:auto;scrollbar-width:none}
  .engine-in::-webkit-scrollbar{display:none}
  .job{display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:10.5px;text-transform:uppercase;white-space:nowrap;flex:0 0 auto;letter-spacing:.02em}
  .job .jn{font-weight:600}.job .jled{width:7px;height:7px;background:var(--faint)}
  .job .jled.ok{background:var(--lime)}.job .jled.running{background:var(--blue);animation:ping 1.3s infinite}
  .job .jled.error{background:var(--red)}.job .jled.warn{background:var(--amber)}
  .job .ct{color:#b9bba8}
  .errb{color:#ff9b9b;margin-left:auto;flex:0 0 auto;white-space:nowrap;font-family:var(--mono);font-size:10px}

  .overlay{position:fixed;inset:0;display:grid;place-items:center;background:var(--paper);z-index:80;text-align:center;padding:28px}
  .overlay .badge{width:46px;height:46px;font-size:24px;margin:0 auto 18px;animation:spin 2.4s linear infinite}
  .overlay h3{font-family:var(--disp);font-weight:800;text-transform:uppercase;font-size:20px;margin:0 0 8px}
  .overlay p{font-family:var(--mono);color:var(--dim);font-size:12px;max-width:440px;line-height:1.7;text-transform:uppercase}
</style>
</head>
<body>
<div class="blob b1"></div><div class="blob b2"></div><div class="blob b3"></div>

<header>
  <div class="bar">
    <div class="mark">
      <div class="badge">⚽</div>
      <div style="min-width:0">
        <div class="wordmark">World&nbsp;Cup&nbsp;'26<span class="slash"> / </span>Edge&nbsp;Engine</div>
        <div class="substat" id="sub">connecting…</div>
      </div>
    </div>
    <div class="grow"></div>
    <div class="conn"><span class="led off" id="led"></span><span id="conntxt">offline</span></div>
    <button class="ibtn" id="refresh" title="Re-simulate &amp; refresh">↻</button>
  </div>
  <nav id="nav"></nav>
</header>

<div class="ticker"><div class="grainstrip"></div><div class="tt" id="tape"></div></div>
<div class="livebanner" id="livebanner"></div>

<div class="hero">
  <h1 class="kinetic" id="kinetic"><span class="fill">EDGE</span> <span class="out">OVER</span> <span class="bl">THE</span> <span class="out">FIELD</span></h1>
  <div class="heroline" id="heroline">monte-carlo priced · executable polymarket edges · paper book live</div>
</div>

<main>
  <div class="bento" id="kpis"></div>
  <section id="sec-overview"></section>
  <section id="sec-account"></section>
  <section id="sec-trades"></section>
  <section id="sec-book"></section>
  <section id="sec-odds"></section>
  <section id="sec-tracker"></section>
  <section id="sec-model"></section>
  <section id="sec-elo"></section>
  <section id="sec-ops"></section>
  <section id="sec-notes"></section>
</main>

<div class="engine"><div class="engine-in" id="engine"></div></div>
<div class="grain"></div>

<div class="overlay" id="overlay">
  <div><div class="badge">⚽</div><h3>Waiting for the engine</h3>
  <p>No live state yet. Start it with python&nbsp;live_engine.py — this page wakes up the moment it writes its first snapshot.</p></div>
</div>

<script>
const TABS=[["overview","Overview"],["account","Account"],["trades","Trades"],["book","Book"],
  ["odds","Odds"],["tracker","Tracker"],["model","Model"],["elo","Elo"],["ops","Ops·AI"],["notes","Notes"]];
let STATE=null, active=localStorage.getItem("wc_tab")||"overview", recvAt=0, jobsBase=null, jobsAt=0, kpiBuilt=false;
const $=id=>document.getElementById(id);
const esc=s=>String(s==null?"":s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const pct=x=>x==null?"—":(x*100).toFixed(1)+"%";
const money=x=>{if(x==null||isNaN(x))return"—";const n=Number(x);return(n<0?"-$":"$")+Math.abs(n).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});};
const sgn=x=>x>0?"pos":x<0?"neg":"";
const num=(x,d=2)=>x==null||isNaN(x)?"—":Number(x).toFixed(d);
function rel(iso){if(!iso)return"—";const t=Date.parse(iso.replace(" ","T").replace(" UTC","Z"));if(isNaN(t))return"—";
  const s=Math.max(0,Math.round((Date.now()-t)/1000));if(s<2)return"just now";if(s<60)return s+"s ago";
  if(s<3600)return Math.floor(s/60)+"m"+(s%60)+"s ago";return Math.floor(s/3600)+"h ago";}
function dur(s){s=Math.max(0,Math.round(s));if(s<60)return s+"s";if(s<3600)return Math.floor(s/60)+"m"+(s%60<10?"0":"")+(s%60)+"s";return Math.floor(s/3600)+"h"+Math.floor((s%3600)/60)+"m";}

function nav(){$("nav").innerHTML=TABS.map(([k,l])=>`<button data-k="${k}" class="${k===active?'on':''}">${l}</button>`).join("");
  $("nav").querySelectorAll("button").forEach(b=>b.onclick=()=>{active=b.dataset.k;localStorage.setItem("wc_tab",active);show();renderActive();});}
function show(){TABS.forEach(([k])=>$("sec-"+k).classList.toggle("on",k===active));
  $("nav").querySelectorAll("button").forEach(b=>b.classList.toggle("on",b.dataset.k===active));}

/* ---------- ticker tape (live) ---------- */
function renderTicker(){
  const a=(STATE.paper_account||{}).summary||{}, m=STATE.markets||{};
  const subs=(STATE.simulation||{}).submarkets||[];
  const items=[];
  items.push(`EQUITY <b>${money(a.equity)}</b> <span class="${a.total_pnl>=0?'up':'dn'}">${a.total_pnl>=0?'▲':'▼'} ${money(a.total_pnl)}</span>`);
  items.push(`EXP·VALUE <b>${money(a.expected_value_usd)}</b>`);
  subs.slice().sort((x,y)=>y.p_champion-x.p_champion).slice(0,6).forEach(s=>items.push(`${esc(s.team)} <b>${pct(s.p_champion)}</b>`));
  (m.slate||[]).slice().sort((x,y)=>(y.edge_pp||0)-(x.edge_pp||0)).filter(r=>r.actionable).slice(0,6)
    .forEach(r=>items.push(`${esc(r.action||r.side)} ${esc(r.team)} <span class="up">+${num(r.edge_pp,1)}PP</span>`));
  ((STATE.tracker||{}).predictions||[]).filter(p=>p.status==="completed").slice(-5).reverse()
    .forEach(p=>items.push(`${esc(p.home)} ${esc(p.score||'')} ${esc(p.away)} ${p.correct?'<span class="up">✓</span>':'<span class="dn">✗</span>'}`));
  const live=((STATE.tracker||{}).live||{}).games||[];
  const liveSpans=live.map(g=>`<span class="ti livetick">⚡ LIVE <b>${esc(g.home)} ${g.home_score}-${g.away_score} ${esc(g.away)}</b> ${esc(g.minute||g.status)}</span>`);
  const html=[...liveSpans,...items.map(t=>`<span class="ti">${t}</span>`)].join("");
  $("tape").innerHTML=html+html;   // duplicate for seamless loop
}

function renderLive(){const g=((STATE.tracker||{}).live||{}).games||[];const b=$("livebanner");
  if(!g.length){b.className="livebanner";b.innerHTML="";return;}
  b.className="livebanner on";
  b.innerHTML=g.map(x=>`<div class="lg"><span class="pulse"></span>
    <span>${esc(x.home)} <span class="sc">${x.home_score}-${x.away_score}</span> ${esc(x.away)}</span>
    <span class="min">${esc(x.minute||x.status)}</span>${x.pick?`<span class="pk">pick ${esc(x.pick)} ${x.pick_prob!=null?pct(x.pick_prob):''}</span>`:''}</div>`).join("");
}

/* ---------- KPI bento (in place + flash) ---------- */
function setStat(id,text,raw,cls,sm){const el=$(id);if(!el)return;const base="stat mono"+(sm?" sm":"")+(cls?" "+cls:"");
  if(el.dataset.v!==undefined&&el.dataset.v!==String(text)){let dir="u";const pr=el.dataset.raw;
    if(pr!==undefined&&raw!=null&&!isNaN(raw)&&!isNaN(pr))dir=Number(raw)<Number(pr)?"d":"u";
    el.className=base;void el.offsetWidth;el.classList.add(dir==="d"?"fd":"fu");}else el.className=base;
  el.dataset.v=String(text);if(raw!=null)el.dataset.raw=String(raw);el.textContent=text;}

function renderKpis(){
  const a=(STATE.paper_account||{}).summary||{},m=STATE.markets||{},rs=m.recommendation_summary||{};
  const fav=((STATE.simulation||{}).submarkets||[]).slice().sort((x,y)=>y.p_champion-x.p_champion)[0]||{};
  const cal=(STATE.model||{}).calibration||{};
  const act=rs.actionable_count!=null?rs.actionable_count:(m.edges_above_threshold!=null?m.edges_above_threshold:null);
  const cards=[
    {id:"equity",c:"c5 r2",lab:"Equity",text:money(a.equity),raw:a.equity,chart:true,
      sub:a.total_pnl!=null?`<span class="${sgn(a.total_pnl)}">${a.total_pnl>=0?'▲':'▼'} ${money(a.total_pnl)} · ${num(a.total_return_pct,1)}%</span>`:""},
    {id:"ev",c:"c3",lab:"Expected value",text:money(a.expected_value_usd),raw:a.expected_value_usd,cls:sgn(a.expected_value_usd),
      sub:a.expected_roi_pct!=null?`${a.expected_roi_pct>=0?'+':''}${num(a.expected_roi_pct,1)}% exp roi`:""},
    {id:"act",c:"c2",lab:"Actionable",text:act!=null?act:"—",raw:act,sub:`${m.edges_found!=null?m.edges_found:'—'} found`},
    {id:"fav",c:"c2",lab:"Favourite",text:(fav.team||"—"),raw:null,sm:true,sub:fav.p_champion!=null?`${pct(fav.p_champion)} champ`:""},
    {id:"open",c:"c4",lab:"Open positions",text:a.n_open!=null?a.n_open:"—",raw:a.n_open,sub:`${a.n_settled!=null?a.n_settled:0} settled · ${money(a.invested)} in`},
    {id:"ll",c:"c3",lab:"Model log-loss",text:cal.calibrated!=null?num(cal.calibrated,4):"—",raw:cal.calibrated,sm:true,sub:cal.method?esc(cal.method):""},
  ];
  if(!kpiBuilt){
    $("kpis").innerHTML=cards.map(c=>`<div class="p ${c.c}"><span class="corner"></span>
      <div class="lab">${c.lab}</div><div class="stat mono ${c.sm?'sm':''}" id="kpi-${c.id}"></div>
      <div class="sub" id="sub-${c.id}"></div>${c.chart?`<div class="chart" id="chart-${c.id}"></div>`:""}</div>`).join("");
    kpiBuilt=true;
  }
  cards.forEach(c=>{setStat("kpi-"+c.id,c.text,c.raw,c.cls,c.sm);const s=$("sub-"+c.id);if(s)s.innerHTML=c.sub||"";});
  buildChart("chart-equity",STATE.equity_curve);
}

/* ---------- interactive equity chart (hover crosshair) ---------- */
function buildChart(id,series){const host=$(id);if(!host)return;
  const pts=(series||[]).map(p=>p.equity).filter(v=>v!=null);
  if(pts.length<2){host.innerHTML=`<div class="empty" style="padding:16px">building curve…</div>`;return;}
  const W=600,H=120,mn=Math.min(...pts),mx=Math.max(...pts),rg=(mx-mn)||1,dx=W/(pts.length-1);
  const xy=pts.map((v,i)=>[i*dx,H-((v-mn)/rg)*(H-12)-6]);
  const d=xy.map((p,i)=>`${i?'L':'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ");
  const up=pts[pts.length-1]>=pts[0];const col=up?"#0a8f4f":"#cf1b1b";
  host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
    <defs><linearGradient id="eg" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".22"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
    <path d="${d} L${W},${H} L0,${H} Z" fill="url(#eg)"/>
    <path d="${d}" fill="none" stroke="${col}" stroke-width="2" vector-effect="non-scaling-stroke"/>
    <circle id="${id}-dot" r="3.5" fill="${col}" stroke="#15160f" stroke-width="1" style="opacity:0"/></svg>
    <div class="cross" id="${id}-cr"></div><div class="tip" id="${id}-tp"></div>`;
  const svg=host.querySelector("svg"),dot=$(id+"-dot"),cr=$(id+"-cr"),tp=$(id+"-tp");
  host.onmousemove=e=>{const r=host.getBoundingClientRect();const fx=(e.clientX-r.left)/r.width;
    const i=Math.max(0,Math.min(pts.length-1,Math.round(fx*(pts.length-1))));
    const px=xy[i][0]/W*r.width, py=xy[i][1]/H*r.height;
    cr.style.left=px+"px";dot.setAttribute("cx",xy[i][0]);dot.setAttribute("cy",xy[i][1]);dot.style.opacity=1;
    tp.style.left=px+"px";tp.style.top=py+"px";tp.textContent="$"+pts[i].toLocaleString(undefined,{maximumFractionDigits:2});};
  host.onmouseleave=()=>{dot.style.opacity=0;};
}

/* ---------- sections ---------- */
function tag(t,upd){return `<div class="sectitle">${t}${upd?`<span class="tag live"><span class="led"></span>live · ${rel(upd)}</span>`:""}</div>`;}
function tradeRow(r){const cl=r.side==="NO"?"no":"buy";return `<div class="row"><div style="flex:1;min-width:0">
  <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px"><span class="nm">${esc(r.team)}</span>
    <span class="tag ${cl}">${esc(r.action||r.side||"")}</span>${r.actionable?'<span class="tag act">actionable</span>':''}
    <span class="big">${num(r.edge_pp,1)}PP</span></div>
  <div class="meta"><span>${esc(r.market||"")}</span></div>
  <div class="meta"><span>Model <b>${pct(r.model_prob)}</b></span><span>Price <b>${num(r.exec_price,3)}</b></span>
    <span>EV/$ <b>${num(r.ev_per_dollar,2)}</b></span><span>Size <b>${money(r.kelly_size_usd||r.capped_size_usd)}</b></span>
    ${r.risk_label?`<span>${esc(r.risk_label)}</span>`:""}</div></div></div>`;}
function trackRow(p){const cty=p.pick==="H"?p.home:p.pick==="A"?p.away:"Draw";
  const badge=p.status==="completed"?`<span class="tag ${p.correct?'win':'loss'}">${p.correct?'✓':'✗'} ${esc(p.score||'')}</span>`:`<span class="tag">${esc(p.kickoff||p.date||'')}</span>`;
  return `<div class="row"><div style="flex:1;min-width:0">
    <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px"><span class="nm">${esc(p.home)} <span class="muted">v</span> ${esc(p.away)}</span>${badge}</div>
    <div class="meta"><span>Pick <b>${esc(cty)}</b> ${pct(p.pick_prob)}</span><span>xG <b>${num(p.exp_home_goals,1)}–${num(p.exp_away_goals,1)}</b></span>
      <span>Likely <b>${esc(p.likely_score||'')}</b></span>${p.out_of_sample?'<span class="tag act">OOS</span>':''}</div></div></div>`;}
function bar(name,val,frac,extra){return `<div class="barwrap"><div class="barhead"><span class="nm">${name}</span><span class="v">${val}</span></div>
  <div class="track"><div class="fill" style="width:${Math.max(2,frac*100).toFixed(1)}%"></div></div>${extra?`<div class="extra">${extra}</div>`:""}</div>`;}

function renderOverview(){
  const m=STATE.markets||{},sim=STATE.simulation||{},t=STATE.tracker||{};
  const subs=(sim.submarkets||[]).slice().sort((x,y)=>y.p_champion-x.p_champion);
  const mx=subs.length?subs[0].p_champion:1;
  const slate=(m.slate||[]).slice().sort((x,y)=>(y.edge_pp||0)-(x.edge_pp||0)).filter(r=>r.actionable).slice(0,6);
  const recent=(t.predictions||[]).filter(p=>p.status==="completed").sort((x,y)=>(y.date||"").localeCompare(x.date||"")).slice(0,5);
  let h=`<div class="bento" style="margin-top:14px">`;
  // odds tile
  h+=`<div class="p c4 r3"><span class="corner"></span><div class="lab">Championship odds<span class="tag">${(sim.n_sims||0).toLocaleString()} sims</span></div><div style="margin-top:12px">`
    +subs.slice(0,8).map(s=>bar(esc(s.team),pct(s.p_champion),s.p_champion/mx,`finalist ${pct(s.p_finalist)} · advance ${pct(s.p_advanced)}`)).join("")+`</div></div>`;
  // edges tile
  h+=`<div class="p c4 r3"><span class="corner"></span><div class="lab">Actionable edges<span class="tag">${esc(m.source||'')}</span></div><div style="margin-top:10px">`
    +(slate.length?slate.map(tradeRow).join(""):`<div class="empty">no edges clear the bar</div>`)+`</div></div>`;
  // tracker tile
  h+=`<div class="p c4 r3"><span class="corner"></span><div class="lab">Latest results${(t.live||{}).updated_at?`<span class="tag live"><span class="led"></span>${rel(t.live.updated_at)}</span>`:""}</div><div style="margin-top:10px">`
    +(recent.length?recent.map(trackRow).join(""):`<div class="empty">no completed games yet</div>`)+`</div></div>`;
  h+=`</div>`;
  $("sec-overview").innerHTML=h;
}

function kpiTile(lab,val,cls,sub){return `<div class="p c3"><div class="lab">${lab}</div><div class="stat mono sm ${cls||''}">${val}</div>${sub?`<div class="sub">${sub}</div>`:""}</div>`;}

function renderAccount(){const a=STATE.paper_account||{},sm=a.summary||{};
  let h=tag("Paper account",a.updated_at||STATE.generated_at)+`<div class="bento">`
    +kpiTile("Equity",money(sm.equity))+kpiTile("Cash",money(sm.cash))+kpiTile("Invested",money(sm.invested))
    +kpiTile("Total P&L",money(sm.total_pnl),sgn(sm.total_pnl))+kpiTile("Realised",money(sm.realized_pnl),sgn(sm.realized_pnl))
    +kpiTile("Unrealised",money(sm.unrealized_pnl),sgn(sm.unrealized_pnl))+kpiTile("Max payout",money(sm.max_payout_usd))
    +kpiTile("Win rate",sm.win_rate_pct!=null?sm.win_rate_pct+"%":"—")+`</div>`;
  const pos=(a.positions||[]).slice().sort((x,y)=>(y.edge_pp||0)-(x.edge_pp||0));
  h+=tag("Open positions · "+pos.length);
  h+=pos.length?pos.map(p=>{const evp=(p.shares||0)*(p.model_prob||0)-(p.stake||0);const cl=p.side==="NO"?"no":"buy";
    return `<div class="row"><div style="flex:1;min-width:0"><div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px">
      <span class="nm">${esc(p.team)}</span><span class="tag ${cl}">${esc(p.action||p.side)}</span><span class="big ${sgn(evp)}">${money(evp)}</span></div>
      <div class="meta"><span>${esc(p.market)}</span></div>
      <div class="meta"><span>Entry <b>${num(p.entry_price,3)}→${num(p.current_price,3)}</b></span><span>Stake <b>${money(p.stake)}</b></span>
        <span>Edge <b>${num(p.edge_pp,1)}pp</b></span><span>Model <b>${pct(p.model_prob)}</b></span><span>Settles <b>${esc(p.settle_date||'—')}</b></span></div></div></div>`;}).join(""):`<div class="empty">no open positions</div>`;
  const hist=(a.history||[]).slice().reverse().slice(0,10);
  if(hist.length)h+=tag("Settled")+hist.map(p=>`<div class="row"><div style="flex:1"><div style="display:flex;gap:8px;align-items:center">
    <span class="nm">${esc(p.team)}</span><span class="tag ${p.result==='WON'?'win':'loss'}">${esc(p.result)}</span><span class="big ${sgn(p.pnl)}">${money(p.pnl)}</span></div>
    <div class="meta"><span>${esc(p.market)}</span><span>Stake <b>${money(p.stake)}</b></span></div></div></div>`).join("");
  $("sec-account").innerHTML=h;}

function renderTrades(){const m=STATE.markets||{},rs=m.recommendation_summary||{};
  const slate=(m.slate||[]).slice().sort((x,y)=>(y.edge_pp||0)-(x.edge_pp||0));
  let h=tag("Live edges · "+esc(m.source||""),STATE.generated_at)+`<div class="bento">`
    +kpiTile("Edges found",m.edges_found!=null?m.edges_found:"—")+kpiTile("Actionable",rs.actionable_count!=null?rs.actionable_count:"—")
    +kpiTile("Exposure",money(rs.current_exposure_usd))+kpiTile("Cap left",money(rs.exposure_cap_remaining_usd))+`</div>`;
  h+=tag("Ranked by edge");
  h+=slate.length?slate.map(tradeRow).join(""):`<div class="empty">no edges yet — price loop runs ~60s</div>`;
  if(m.note)h+=`<div class="note">${esc(m.note)}</div>`;
  $("sec-trades").innerHTML=h;}

function renderBook(){const p=STATE.portfolio||{};
  if(!p.available){$("sec-book").innerHTML=tag("Optimised book")+`<div class="note">Correlation-aware portfolio not available${p.reason?` — ${esc(p.reason)}`:""}.</div>`;return;}
  const rec=p.recommended||{},st=rec.stats||{};
  let h=tag("Optimised book · "+(p.n_candidates||0)+" candidates",STATE.generated_at)+`<div class="bento">`
    +kpiTile("Log-growth",num(st.exp_log_growth_pct,2)+"%",sgn(st.exp_log_growth_pct))+kpiTile("Exp return",num(st.exp_return_pct,1)+"%",sgn(st.exp_return_pct))
    +kpiTile("Prob. loss",((st.prob_loss||0)*100).toFixed(0)+"%")+kpiTile("5th pctile",num(st.p05_return_pct,0)+"%",sgn(st.p05_return_pct))
    +kpiTile("Eff. bets",num(st.effective_bets,1))+kpiTile("Sharpe",num(st.sharpe,2))+`</div>`;
  h+=`<div class="note">Growth-optimal (fractional Kelly) over the joint simulated payoffs — sizes the whole book together so correlated bets aren't double-counted.</div>`;
  h+=tag("Allocation");const alloc=rec.allocation||[];
  h+=alloc.length?alloc.map(a=>{const cl=a.side==="NO"?"no":"buy";return `<div class="row"><div style="flex:1"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
    <span class="nm">${esc(a.team)}</span><span class="tag ${cl}">${esc(a.side)}</span><span class="tag">${esc(a.group)}</span><span class="big">$${num(a.stake_usd,2)}</span></div>
    <div class="meta"><span>Weight <b>${num(a.weight_pct,1)}%</b></span><span>Price <b>${num(a.price,3)}</b></span><span>Edge <b>${num(a.edge_pp,1)}pp</b></span><span>Model <b>${pct(a.model_prob)}</b></span></div></div></div>`;}).join(""):`<div class="empty">no positions cleared the caps</div>`;
  const cors=p.correlations||[];if(cors.length)h+=tag("Near-duplicate exposures")+cors.map(c=>`<div class="note"><b>${num(c.corr,2)} corr</b> — ${esc(c.a)} ≈ ${esc(c.b)}</div>`).join("");
  $("sec-book").innerHTML=h;}

function renderOdds(){const s=STATE.simulation||{};const subs=(s.submarkets||[]).slice().sort((a,b)=>b.p_champion-a.p_champion).slice(0,24);
  const mx=subs.length?subs[0].p_champion:1;
  let h=tag(`Monte Carlo · ${(s.n_sims||0).toLocaleString()} sims · ${esc(s.draw_label||'')}`,s.updated_at);
  h+=subs.length?subs.map(t=>bar(esc(t.team),pct(t.p_champion),t.p_champion/mx,
    `finalist ${pct(t.p_finalist)} · last4 ${pct(t.p_last_4)} · advance ${pct(t.p_advanced)} · win-group ${pct(t.p_win_group)}`)).join(""):`<div class="empty">simulating…</div>`;
  $("sec-odds").innerHTML=h;}

function renderTracker(){const t=STATE.tracker||{},sc=t.scorecard||{},c=sc.completed||{},o=sc.out_of_sample||{},live=t.live||{};
  let h=tag(`Live tracker · ${t.n_fixtures||0} fixtures`,live.updated_at)+`<div class="bento">`
    +kpiTile("Completed",(c.n||0)+"",null,c.n?("acc "+pct(c.accuracy)):"")+kpiTile("Out-of-sample",(o.n||0)+"",null,o.n?("acc "+pct(o.accuracy)):"")
    +kpiTile("Log loss",c.n?num(c.log_loss,3):"—",null,"base "+(sc.uniform_log_loss_baseline||"—"))+kpiTile("Scheduled",sc.n_scheduled||0)+`</div>`;
  const ip=live.in_play||[];
  if(ip.length)h+=tag("Live now")+ip.map(l=>`<div class="row"><span class="nm">⚡ ${esc(l)}</span><span class="tag live" style="margin-left:auto"><span class="led"></span>in play</span></div>`).join("");
  h+=tag("Fixtures & predictions");
  h+=(t.predictions||[]).slice().sort((a,b)=>(a.date||"").localeCompare(b.date||"")).map(trackRow).join("");
  $("sec-tracker").innerHTML=h;}

function renderModel(){const m=STATE.model||{},cal=m.calibration||{};const imp=(m.feature_importance||[]).slice(0,16);
  const mxv=imp.length?imp[0].importance:1;
  let h=tag("1X2 model · "+esc(m.kind||""),STATE.generated_at)+`<div class="bento">`
    +kpiTile("Calibrated LL",cal.calibrated!=null?num(cal.calibrated,4):"—")+kpiTile("Uncalibrated",cal.uncalibrated!=null?num(cal.uncalibrated,4):"—")
    +kpiTile("Calibration",esc(cal.method||"—"))+kpiTile("Train rows",(m.n_train||0).toLocaleString())+`</div>`;
  h+=tag("Top features")+imp.map(f=>bar(esc(f.feature),num(f.importance,3),f.importance/mxv)).join("");
  $("sec-model").innerHTML=h;}

function renderElo(){const e=STATE.elo||{},lb=(e.leaderboard||[]).slice(0,32);
  const mx=lb.length?lb[0].rating:1,mn=lb.length?lb[lb.length-1].rating:0;
  let h=tag(`Elo · ${e.n_teams_rated||lb.length} teams${e.live_results_fed?` · ${e.live_results_fed} live fed`:""}`,STATE.generated_at);
  h+=lb.map((t,i)=>bar(`<span class="muted mono">${i+1}.</span> ${esc(t.team)}`,Math.round(t.rating),(t.rating-mn)/((mx-mn)||1))).join("");
  $("sec-elo").innerHTML=h;}

function renderNotes(){const m=STATE.markets||{},ex=STATE.execution||{},d=STATE.data||{};
  let h=tag("Notes & honesty");
  if(m.note)h+=`<div class="note"><b>Markets:</b> ${esc(m.note)}</div>`;
  if(ex.mode)h+=`<div class="note"><b>Execution:</b> ${esc(ex.mode)} — ${esc(ex.note||'')}</div>`;
  (STATE.notes||[]).forEach(n=>h+=`<div class="note">${esc(n)}</div>`);
  h+=`<div class="note"><b>Data:</b> ${esc(d.source||'')} · ${(d.n_matches||0).toLocaleString()} train · ${(d.elo_matches||0).toLocaleString()} Elo · ${esc(d.date_min||'')}→${esc(d.date_max||'')}</div>`;
  $("sec-notes").innerHTML=h;}

function renderOps(){const o=STATE.ops||null,pr=STATE.proposals||null;
  let h=tag("Site watchdog",o?o.checked_at:null);
  if(!o){h+=`<div class="empty">ops watchdog not reporting yet — start wc-ops</div>`;}
  else{const cls=o.status==="OK"?"win":(o.status==="DOWN"?"loss":"no");
    h+=`<div class="bento">`
      +`<div class="p c4"><div class="lab">Status</div><div class="stat mono sm"><span class="tag ${cls}">${esc(o.status)}</span></div><div class="sub">${o.llm_enabled?"AI diagnosis on":"mechanical mode"}</div></div>`
      +kpiTile("Issues",(o.issues||[]).length)+kpiTile("Auto-actions",(o.actions||[]).length)
      +kpiTile("Equity seen",money((o.signals||{}).equity))+`</div>`;
    if((o.issues||[]).length)h+=`<div class="sectitle">Open issues</div>`+o.issues.map(i=>`<div class="note">${esc(i)}</div>`).join("");
    if((o.actions||[]).length)h+=`<div class="sectitle">Auto-heal actions</div>`+o.actions.map(a=>`<div class="note">${esc(a)}</div>`).join("");
    if(o.llm_summary)h+=`<div class="sectitle">AI diagnosis</div><div class="note"><b>SRE:</b> ${esc(o.llm_summary)}</div>`;}
  // implemented log (the back-and-forth result)
  const impl=STATE.improvements||null;
  h+=tag("Auto-implemented",impl?impl.updated_at:null);
  const ie=(impl&&impl.entries||[]).slice().reverse();
  if(!ie.length){h+=`<div class="empty">implementer hasn't landed changes yet — runs hourly, test-gated</div>`;}
  else{h+=ie.map(e=>{const st=e.status==="implemented"?"win":(e.status==="reverted"||e.status==="failed"?"loss":"");
    return `<div class="row"><div style="flex:1;min-width:0"><div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
      <span class="nm">${esc(e.title||"")}</span><span class="tag ${st}">${esc(e.status||"")}</span>${e.commit&&e.commit!=="-"?`<span class="tag">${esc(e.commit)}</span>`:""}</div>
      <div class="meta" style="text-transform:none"><span>${esc(e.summary||e.reason||"")}</span>${e.file?`<span><b>${esc(e.file)}</b></span>`:""}</div></div></div>`;}).join("");}
  // improver proposals
  h+=tag("Improver proposals",pr?pr.updated_at:null);
  const latest=pr&&pr.latest?pr.latest.proposals:null;
  if(!latest||!latest.length){h+=`<div class="empty">no AI proposals yet — start wc-improver (needs OPENAI_API_KEY)</div>`;}
  else{h+=latest.map(p=>`<div class="row"><div style="flex:1;min-width:0">
    <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center"><span class="nm">${esc(p.title||"")}</span>
      <span class="tag ${p.impact==='high'?'buy':(p.impact==='low'?'':'act')}">${esc(p.impact||'')}</span><span class="tag">${esc(p.area||'')}</span>
      <button class="deny" data-t="${esc(p.title||'')}" style="margin-left:auto">deny</button></div>
    <div class="meta" style="text-transform:none"><span>${esc(p.rationale||'')}</span></div>
    <div class="meta" style="text-transform:none"><span><b>First step:</b> ${esc(p.first_step||'')}</span></div></div></div>`).join("");}
  $("sec-ops").innerHTML=h;}

const RENDER={overview:renderOverview,account:renderAccount,trades:renderTrades,book:renderBook,odds:renderOdds,tracker:renderTracker,model:renderModel,elo:renderElo,ops:renderOps,notes:renderNotes};
function renderActive(){if(!STATE)return;(RENDER[active]||(()=>{}))();}

function renderEngine(){const e=STATE.engine||{},jobs=e.jobs||{};jobsBase=jobs;jobsAt=Date.now();
  let h=Object.entries(jobs).map(([n,j])=>`<div class="job"><span class="jled ${j.status}"></span><span class="jn">${esc(n)}</span><span class="ct" id="job-${n}"></span>${j.errors?`<span class="neg">${j.errors}✗</span>`:""}</div>`).join("");
  const errs=e.recent_errors||[];if(errs.length)h+=`<span class="errb">⚠ ${esc(errs[errs.length-1]).slice(0,68)}</span>`;
  $("engine").innerHTML=h;tickJobs();}
function tickJobs(){if(!jobsBase)return;const dt=(Date.now()-jobsAt)/1000;
  Object.entries(jobsBase).forEach(([n,j])=>{const el=$("job-"+n);if(!el)return;
    if(j.status==="running"){el.textContent="running…";return;}el.textContent="next "+dur(Math.max(0,(j.next_in_sec||0)-dt));});}

function applyState(s){STATE=s;recvAt=Date.now();$("overlay").style.display="none";
  nav();show();renderTicker();renderLive();renderKpis();renderActive();renderEngine();
  const up=(STATE.engine||{}).uptime_sec;$("sub").textContent="updated "+rel(STATE.generated_at)+(up!=null?" · up "+dur(up):"");}
function setConn(on){$("led").className="led "+(on?"on":"off");$("conntxt").textContent=on?"live":"reconnecting";}

/* ---------- kinetic text tied to scroll depth ---------- */
let ky=$("kinetic");
function onScroll(){const p=Math.min(1,(window.scrollY||0)/420);
  ky.style.transform=`translateX(${(-p*120).toFixed(1)}px) skewX(${(-p*7).toFixed(2)}deg)`;
  ky.style.letterSpacing=`${(-0.03+ -p*0.045).toFixed(3)}em`;ky.style.opacity=(1-p*0.65).toFixed(2);
  $("heroline").style.transform=`translateX(${(p*60).toFixed(1)}px)`;}
window.addEventListener("scroll",()=>requestAnimationFrame(onScroll),{passive:true});

/* ---------- realtime transport ---------- */
let es=null,pollTimer=null;
function connect(){try{es=new EventSource("events");
  es.onopen=()=>{setConn(true);if(pollTimer){clearInterval(pollTimer);pollTimer=null;}};
  es.onmessage=ev=>{try{applyState(JSON.parse(ev.data));recvAt=Date.now();setConn(true);}catch(e){}};
  es.onerror=()=>{setConn(false);if(!pollTimer)startPolling();};}catch(e){startPolling();}}
function startPolling(){if(pollTimer)return;poll();pollTimer=setInterval(poll,8000);}
async function poll(){try{const r=await fetch("state.json?t="+Date.now());if(!r.ok)throw 0;applyState(await r.json());}catch(e){setConn(false);}}

document.addEventListener("click",e=>{const b=e.target.closest&&e.target.closest(".deny");if(!b||b.disabled)return;
  b.disabled=true;b.textContent="denied ✓";const row=b.closest(".row");if(row)row.style.opacity=".5";
  fetch("resolve",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({title:b.dataset.t,status:"denied"})}).catch(()=>{});});
$("refresh").onclick=async()=>{const b=$("refresh");b.classList.add("spin");
  try{await fetch("trigger",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({refresh:true})});}catch(e){}
  setTimeout(()=>b.classList.remove("spin"),1400);};

setInterval(()=>{if(STATE){const up=(STATE.engine||{}).uptime_sec;$("sub").textContent="updated "+rel(STATE.generated_at)+(up!=null?" · up "+dur(up+(Date.now()-recvAt)/1000):"");}tickJobs();},1000);

nav();onScroll();connect();poll();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body, ctype: str = "application/json", extra: dict | None = None) -> None:
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, INDEX_HTML, "text/html")
        elif path == "/state.json":
            try:
                self._send(200, STATE.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self._send(404, json.dumps({"error": "no state yet"}))
        elif path == "/events":
            self._stream_events()
        elif path == "/healthz":
            self._send(200, "ok", "text/plain")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            req = {}
        if path == "/trigger":
            allowed = {k: True for k in ("refresh", "resim", "results", "retrain") if req.get(k)} or {"refresh": True}
            self._merge_control(allowed)
            self._send(200, json.dumps({"ok": True, "queued": allowed}))
        elif path == "/resolve":
            title = req.get("title")
            if title:
                self._merge_control({"resolve": [{"title": title, "status": req.get("status", "denied")}]})
            self._send(200, json.dumps({"ok": bool(title)}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def _merge_control(self, patch: dict) -> None:
        """Merge a control patch into engine_control.json (accumulate resolves; don't clobber)."""
        try:
            cur = json.loads(CONTROL.read_text(encoding="utf-8")) if CONTROL.exists() else {}
        except (json.JSONDecodeError, OSError):
            cur = {}
        for key, val in patch.items():
            if key == "resolve":
                cur.setdefault("resolve", []).extend(val)
            else:
                cur[key] = val
        try:
            CONTROL.parent.mkdir(parents=True, exist_ok=True)
            CONTROL.write_text(json.dumps(cur), encoding="utf-8")
        except OSError:
            pass

    def _stream_events(self) -> None:
        """Server-Sent Events: push the full state whenever the file changes; ping otherwise."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        last_mtime = 0.0
        last_ping = time.monotonic()
        try:
            while True:
                try:
                    mtime = STATE.stat().st_mtime
                except FileNotFoundError:
                    mtime = 0.0
                if mtime and mtime != last_mtime:
                    last_mtime = mtime
                    try:
                        payload = STATE.read_text(encoding="utf-8")
                    except (FileNotFoundError, OSError):
                        payload = None
                    if payload:
                        # Default (unnamed) event so the browser's EventSource.onmessage fires.
                        self.wfile.write(b"data: ")
                        self.wfile.write(payload.replace("\n", "").encode("utf-8"))
                        self.wfile.write(b"\n\n")
                        self.wfile.flush()
                        last_ping = time.monotonic()
                elif time.monotonic() - last_ping > 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = time.monotonic()
                time.sleep(1.0)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except OSError:
            return

    def log_message(self, *args) -> None:  # quiet
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-time web dashboard for the World Cup edge engine.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (127.0.0.1 behind a proxy; 0.0.0.0 for direct LAN)")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    httpd.daemon_threads = True
    print(f"Web dashboard at http://{args.host}:{args.port}  (state: {STATE})")
    print("Real-time via SSE (/events). Run live_engine.py alongside to feed it.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
