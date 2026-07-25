"""The single-page front end for `colorgrade gui`, served by server.py.

Kept as one self-contained string so the GUI needs no bundler, no framework
and no extra files on disk - it is the same numpy-only install as the CLI.
"""

INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>colorgrader</title>
<style>
  :root {
    --bg:#0b0b0e; --panel:#15151b; --panel-2:#1c1c24; --line:#282833;
    --ink:#ececf1; --dim:#8a8a97; --accent:#5b8cff; --good:#4ade80;
    --warn:#fbbf24; --bad:#f87171; --radius:12px;
  }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; }
  body {
    background:var(--bg); color:var(--ink);
    font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  button { font:inherit; color:inherit; cursor:pointer; }
  a { color:var(--accent); }

  /* ---- top bar ---- */
  header {
    position:sticky; top:0; z-index:20; background:rgba(11,11,14,.85);
    backdrop-filter:blur(12px); border-bottom:1px solid var(--line);
    padding:14px 22px; display:flex; align-items:center; gap:16px; flex-wrap:wrap;
  }
  .brand { font-weight:700; letter-spacing:-.02em; font-size:16px; }
  .brand span { color:var(--accent); }
  .grow { flex:1; }
  .field { display:flex; align-items:center; gap:8px; }
  input[type=text] {
    background:var(--panel-2); border:1px solid var(--line); color:var(--ink);
    border-radius:8px; padding:8px 11px; min-width:280px; font:inherit;
  }
  input[type=text]:focus { outline:none; border-color:var(--accent); }
  .btn {
    background:var(--panel-2); border:1px solid var(--line); border-radius:8px;
    padding:8px 14px; font-weight:500; transition:.12s;
  }
  .btn:hover { border-color:#3a3a48; background:#22222c; }
  .btn.primary { background:var(--accent); border-color:var(--accent); color:#08122e; font-weight:600; }
  .btn.primary:hover { filter:brightness(1.08); }
  .btn:disabled { opacity:.4; cursor:default; }

  /* ---- controls ---- */
  .controls {
    position:sticky; top:59px; z-index:15; background:var(--panel);
    border-bottom:1px solid var(--line); padding:14px 22px;
    display:flex; gap:26px; align-items:center; flex-wrap:wrap;
  }
  .controls.hidden { display:none; }
  .ctl { display:flex; flex-direction:column; gap:6px; }
  .ctl > label { font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--dim); }
  .seg { display:inline-flex; background:var(--panel-2); border:1px solid var(--line); border-radius:8px; padding:2px; }
  .seg button { border:none; background:none; padding:6px 13px; border-radius:6px; color:var(--dim); font-weight:500; }
  .seg button.on { background:var(--accent); color:#08122e; }
  .slider-row { display:flex; align-items:center; gap:10px; }
  input[type=range] { -webkit-appearance:none; appearance:none; width:170px; height:4px;
    background:var(--line); border-radius:99px; outline:none; }
  input[type=range]::-webkit-slider-thumb { -webkit-appearance:none; width:16px; height:16px;
    border-radius:50%; background:var(--accent); cursor:pointer; border:2px solid #0b0b0e; }
  input[type=range]::-moz-range-thumb { width:16px; height:16px; border-radius:50%;
    background:var(--accent); cursor:pointer; border:2px solid #0b0b0e; }
  .val { font-variant-numeric:tabular-nums; min-width:38px; color:var(--ink); }
  select { background:var(--panel-2); border:1px solid var(--line); color:var(--ink);
    border-radius:8px; padding:7px 10px; font:inherit; }
  select:focus { outline:none; border-color:var(--accent); }

  .summary { margin-left:auto; text-align:right; font-size:13px; color:var(--dim); }
  .summary b { color:var(--ink); font-variant-numeric:tabular-nums; }

  /* ---- body ---- */
  main { padding:24px 22px 80px; }
  .hint { color:var(--dim); max-width:640px; }
  .drop {
    margin:60px auto; max-width:560px; text-align:center; padding:52px 32px;
    border:2px dashed var(--line); border-radius:16px; color:var(--dim);
  }
  .drop h2 { color:var(--ink); margin:0 0 8px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:20px; }

  .card { background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); overflow:hidden; }
  .card.ref { border-color:#356b39; }
  .card-head { display:flex; align-items:center; gap:10px; padding:13px 15px 11px; }
  .card-head .name { font-weight:600; letter-spacing:-.01em; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .badge { font-size:11px; font-weight:600; padding:2px 9px; border-radius:99px; white-space:nowrap; }
  .badge.ref { background:#1d3a1f; color:#84d184; }
  .badge.de { background:#1c2f45; color:#7db9f0; font-variant-numeric:tabular-nums; }
  .badge.de.warn { background:#3a2e12; color:var(--warn); }

  /* before/after wipe */
  .wipe { position:relative; aspect-ratio:16/9; background:#000; overflow:hidden; user-select:none; }
  .wipe img { position:absolute; inset:0; width:100%; height:100%; object-fit:cover; display:block; }
  .wipe .after { clip-path:inset(0 0 0 var(--split,50%)); }
  .wipe .tagl, .wipe .tagr { position:absolute; top:9px; font-size:10px; letter-spacing:.08em;
    text-transform:uppercase; background:rgba(0,0,0,.6); padding:3px 7px; border-radius:5px; color:#fff; }
  .wipe .tagl { left:9px; } .wipe .tagr { right:9px; }
  .wipe .divider { position:absolute; top:0; bottom:0; left:var(--split,50%); width:2px;
    background:#fff; box-shadow:0 0 0 1px rgba(0,0,0,.4); cursor:ew-resize; }
  .wipe .divider::after { content:""; position:absolute; top:50%; left:50%; width:26px; height:26px;
    transform:translate(-50%,-50%); border-radius:50%; background:#fff;
    box-shadow:0 2px 6px rgba(0,0,0,.5); }
  .wipe .divider::before { content:"\21D4"; position:absolute; top:50%; left:50%;
    transform:translate(-50%,-50%); z-index:1; font-size:13px; color:#111; }
  .card.ref .divider, .card.ref .after, .card.ref .tagr { display:none; }

  .card-foot { padding:12px 15px 15px; display:flex; flex-direction:column; gap:11px; }
  .row { display:flex; align-items:center; gap:10px; }
  .row .lbl { font-size:11px; text-transform:uppercase; letter-spacing:.07em; color:var(--dim); min-width:64px; }
  .mini.btn { padding:5px 10px; font-size:12px; }
  .lgg { border-collapse:collapse; width:100%; font-size:12px; }
  .lgg th, .lgg td { text-align:right; padding:3px 6px; border-bottom:1px solid var(--line); font-variant-numeric:tabular-nums; }
  .lgg th:first-child, .lgg td:first-child { text-align:left; color:var(--dim); }
  .lgg th { color:var(--dim); font-weight:500; }
  details summary { color:var(--dim); cursor:pointer; font-size:12px; list-style:none; }
  details summary::-webkit-details-marker { display:none; }
  details summary::before { content:"\25B8 "; }
  details[open] summary::before { content:"\25BE "; }

  /* toast */
  #toast { position:fixed; left:50%; bottom:26px; transform:translateX(-50%) translateY(20px);
    background:var(--panel-2); border:1px solid var(--line); border-radius:10px; padding:14px 20px;
    max-width:min(680px,92vw); box-shadow:0 12px 40px rgba(0,0,0,.5); opacity:0; pointer-events:none;
    transition:.25s; z-index:40; }
  #toast.show { opacity:1; transform:translateX(-50%) translateY(0); pointer-events:auto; }
  #toast .path { font-family:ui-monospace,monospace; color:var(--accent); word-break:break-all; }
  #toast .x { float:right; margin-left:16px; color:var(--dim); }

  .spinner { width:15px; height:15px; border:2px solid var(--line); border-top-color:var(--accent);
    border-radius:50%; animation:spin .7s linear infinite; display:inline-block; vertical-align:-2px; }
  @keyframes spin { to { transform:rotate(360deg); } }
  .err { color:var(--bad); }
</style>
</head>
<body>
<header>
  <div class="brand">color<span>grader</span></div>
  <div class="field">
    <input id="folder" type="text" placeholder="Path to a folder of clips, e.g. /Users/you/footage" spellcheck="false">
    <button class="btn" id="browse" title="Choose a folder">Browse…</button>
    <button class="btn" id="load">Load</button>
  </div>
  <div class="grow"></div>
  <div class="field" id="exportField" style="display:none">
    <input id="outdir" type="text" placeholder="Output folder" spellcheck="false" style="min-width:200px">
    <label class="field" style="gap:5px"><input type="checkbox" id="renderChk"> bake video</label>
    <button class="btn primary" id="export" disabled>Export</button>
  </div>
</header>

<div class="controls hidden" id="controls">
  <div class="ctl">
    <label>Strength</label>
    <div class="slider-row">
      <input type="range" id="strength" min="0" max="100" value="85">
      <span class="val" id="strengthVal">85%</span>
    </div>
  </div>
  <div class="ctl">
    <label>Reference</label>
    <select id="reference"></select>
  </div>
  <div class="summary" id="summary"></div>
</div>

<main>
  <div class="drop" id="drop">
    <h2>Load a folder of clips</h2>
    <p class="hint" style="margin:0 auto">Type or paste the path to a folder of video files above and hit
    <b>Load</b>. colorgrader samples each clip, measures its colour and matches every clip to a reference so
    they cut together. Then tune it live and export LUTs for Resolve &amp; Premiere.</p>
  </div>
  <div class="grid" id="grid"></div>
</main>

<div id="toast"></div>

<script>
const $ = s => document.querySelector(s);
const state = { clips:[], ref:0, strength:0.85, overrides:{}, folder:"" };
let previewTimer = null;

function toast(html, ms=6000){
  const t = $("#toast");
  t.innerHTML = '<span class="x" onclick="this.parentNode.classList.remove(\'show\')">✕</span>' + html;
  t.classList.add("show");
  if (ms) setTimeout(()=>t.classList.remove("show"), ms);
}

async function api(path, body){
  const r = await fetch(path, {method:"POST", headers:{"Content-Type":"application/json"},
                              body:JSON.stringify(body)});
  const data = await r.json();
  if (!r.ok || data.error) throw new Error(data.error || ("HTTP "+r.status));
  return data;
}

async function load(){
  const folder = $("#folder").value.trim();
  if (!folder) return;
  state.folder = folder;
  $("#load").innerHTML = '<span class="spinner"></span>';
  $("#drop").innerHTML = '<span class="spinner"></span> reading clips…';
  try {
    const data = await api("/api/load", {folder});
    state.clips = data.clips;
    state.ref = data.suggested_ref;
    buildReferenceSelect();
    buildGrid();
    $("#controls").classList.remove("hidden");
    $("#exportField").style.display = "flex";
    $("#export").disabled = false;
    if (!$("#outdir").value) $("#outdir").value = data.default_output;
    $("#drop").style.display = "none";
    await preview();
  } catch(e){
    $("#drop").innerHTML = '<h2 class="err">Could not load</h2><p class="hint" style="margin:0 auto">'
      + escapeHtml(e.message) + '</p>';
    $("#drop").style.display = "block";
  } finally {
    $("#load").textContent = "Load";
  }
}

function buildReferenceSelect(){
  const sel = $("#reference");
  sel.innerHTML = "";
  state.clips.forEach((c,i)=>{
    const o = document.createElement("option");
    o.value = i; o.textContent = c.name;
    if (i===state.ref) o.selected = true;
    sel.appendChild(o);
  });
}

function buildGrid(){
  const grid = $("#grid");
  grid.innerHTML = "";
  state.clips.forEach((c,i)=>{
    const card = document.createElement("div");
    card.className = "card"; card.id = "card"+i;
    card.innerHTML = `
      <div class="card-head">
        <span class="name" title="${escapeHtml(c.name)}">${escapeHtml(c.name)}</span>
        <span class="badge de" id="de${i}"></span>
      </div>
      <div class="wipe" id="wipe${i}">
        <img class="before" src="${c.before}">
        <img class="after" id="after${i}" src="${c.before}">
        <span class="tagl">before</span><span class="tagr">after</span>
        <div class="divider"></div>
      </div>
      <div class="card-foot">
        <div class="row">
          <button class="mini btn" onclick="setRef(${i})">Set as reference</button>
          <span class="lbl" style="min-width:0">Override</span>
          <input type="range" min="0" max="100" style="width:110px" id="ov${i}" oninput="setOverride(${i},this.value)">
          <span class="val" id="ovv${i}">auto</span>
        </div>
        <details id="det${i}"><summary>lift / gamma / gain</summary>
          <table class="lgg" id="lgg${i}"></table>
        </details>
      </div>`;
    grid.appendChild(card);
    wireWipe(i);
  });
}

function wireWipe(i){
  const wipe = $("#wipe"+i), div = wipe.querySelector(".divider");
  let dragging = false;
  const move = e => {
    if (!dragging) return;
    const r = wipe.getBoundingClientRect();
    const x = ((e.touches?e.touches[0].clientX:e.clientX) - r.left) / r.width;
    wipe.style.setProperty("--split", Math.max(0,Math.min(1,x))*100 + "%");
  };
  div.addEventListener("mousedown", e=>{dragging=true; e.preventDefault();});
  div.addEventListener("touchstart", ()=>dragging=true);
  window.addEventListener("mousemove", move);
  window.addEventListener("touchmove", move);
  window.addEventListener("mouseup", ()=>dragging=false);
  window.addEventListener("touchend", ()=>dragging=false);
}

async function preview(){
  const body = {strength:state.strength, reference:state.ref,
                overrides:state.overrides};
  let data;
  try { data = await api("/api/preview", body); }
  catch(e){ toast('<span class="err">Preview failed:</span> '+escapeHtml(e.message)); return; }

  state.ref = data.reference;
  let improved=0, sumBefore=0, sumAfter=0, n=0;
  data.clips.forEach(c=>{
    const card = $("#card"+c.i);
    card.classList.toggle("ref", c.i===state.ref);
    $("#after"+c.i).src = c.after;
    const de = $("#de"+c.i);
    if (c.i===state.ref){
      de.className = "badge ref"; de.textContent = "REFERENCE";
    } else {
      de.className = "badge de" + (c.delta.after > 4 ? " warn" : "");
      de.textContent = "ΔE " + c.delta.before.toFixed(1) + " → " + c.delta.after.toFixed(1);
      sumBefore += c.delta.before; sumAfter += c.delta.after; n++;
      if (c.delta.after < c.delta.before) improved++;
    }
    renderLgg(c);
  });
  $("#summary").innerHTML = n
    ? `matched <b>${n}</b> clip${n>1?"s":""} &middot; mean ΔE
       <b>${(sumBefore/n).toFixed(1)}</b> → <b>${(sumAfter/n).toFixed(1)}</b>`
    : "";
}

function renderLgg(c){
  const t = $("#lgg"+c.i);
  if (c.i===state.ref || !c.lgg){ $("#det"+c.i).style.display="none"; return; }
  $("#det"+c.i).style.display="";
  const f = a => a.map(v=>v.toFixed(3)).join("</td><td>");
  t.innerHTML =
    `<tr><th></th><th>R</th><th>G</th><th>B</th></tr>
     <tr><td>Lift</td><td>${f(c.lgg.lift)}</td></tr>
     <tr><td>Gamma</td><td>${f(c.lgg.gamma)}</td></tr>
     <tr><td>Gain</td><td>${f(c.lgg.gain)}</td></tr>
     <tr><td>Saturation</td><td colspan="3">${c.lgg.saturation.toFixed(3)}</td></tr>`;
}

function schedulePreview(){ clearTimeout(previewTimer); previewTimer = setTimeout(preview, 120); }

function setRef(i){ state.ref = i; $("#reference").value = i; schedulePreview(); }
function setOverride(i,v){
  state.overrides[i] = v/100;
  $("#ovv"+i).textContent = v+"%";
  schedulePreview();
}

// ---- control wiring ----
$("#load").onclick = load;
$("#browse").onclick = async ()=>{
  const btn = $("#browse"); btn.disabled = true;
  try {
    const d = await api("/api/browse", {initialdir:$("#folder").value.trim()});
    if (d.path){ $("#folder").value = d.path; load(); }  // empty = user cancelled
  } catch(e){ toast('<span class="err">'+escapeHtml(e.message)+'</span>'); }
  finally { btn.disabled = false; }
};
$("#folder").addEventListener("keydown", e=>{ if(e.key==="Enter") load(); });
$("#strength").addEventListener("input", e=>{
  state.strength = e.target.value/100;
  $("#strengthVal").textContent = e.target.value+"%";
  schedulePreview();
});
$("#reference").addEventListener("change", e=>{ state.ref = +e.target.value; schedulePreview(); });

$("#export").onclick = async ()=>{
  const btn = $("#export"); const orig = btn.textContent;
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> exporting';
  try {
    const data = await api("/api/export", {
      folder:state.folder, output:$("#outdir").value.trim(),
      strength:state.strength, reference:state.ref,
      overrides:state.overrides, render:$("#renderChk").checked,
    });
    toast(`Exported <b>${data.lut_count}</b> LUT${data.lut_count!==1?"s":""}`
      + (data.rendered?` and <b>${data.rendered}</b> baked video${data.rendered!==1?"s":""}`:"")
      + ` to<br><span class="path">${escapeHtml(data.output)}</span><br>`
      + `Open <b>report.html</b> there, then see <b>HOW_TO_USE.md</b> for Resolve &amp; Premiere.`, 12000);
  } catch(e){ toast('<span class="err">Export failed:</span> '+escapeHtml(e.message)); }
  finally { btn.disabled=false; btn.textContent = orig; }
};

function escapeHtml(s){ return String(s).replace(/[&<>"]/g, c=>(
  {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
</script>
</body>
</html>
"""
