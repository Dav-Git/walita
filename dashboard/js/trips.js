// ---------- Trips table ----------
(function(){
  const tbody=document.querySelector("#tripsTable tbody");
  const table=document.getElementById("tripsTable");
  const filterEl=document.getElementById("filter");
  const dateFromEl=document.getElementById("tripDateFrom");
  const dateToEl=document.getElementById("tripDateTo");
  const copyBtn=document.getElementById("tripsCopy");
  const delayEl=document.getElementById("tripShowDelay");
  const routeEl=document.getElementById("tripShowRoute");
  const timeEl=document.getElementById("tripTimeMode");
  let rows=DATA.trips.map((t,i)=>({...t,_i:i}));
  const kpis=DATA.kpis||{};
  bindDateInput(dateFromEl, kpis.first, kpis.last);
  bindDateInput(dateToEl, kpis.first, kpis.last);
  let sortKey="date", sortAsc=false;
  let visible=[];
  let routeCacheKey="";
  let routeByI=new Map();
  const TSV_BASE=["Datum","Linie","Operator","Baureihe","Wagen","Von","Nach",
    "Ab","An","Zwischenhalte","km","Min"];

  function prefGet(k, d){ try{ const v=localStorage.getItem(k); return v==null?d:v; }catch(e){ return d; } }
  function prefSet(k, v){ try{ localStorage.setItem(k, v); }catch(e){} }
  delayEl.checked = prefGet("trwl-trips-delay","0")==="1";
  routeEl.checked = false;
  timeEl.value = prefGet("trwl-trips-times","planned")==="real" ? "real" : "planned";

  function showDelay(){ return delayEl.checked; }
  function showRoute(){ return routeEl.checked; }
  function timeMode(){ return timeEl.value==="real" ? "real" : "planned"; }
  function pickTime(planned, real){
    return timeMode()==="planned" ? (planned||real) : (real||planned);
  }
  function tripDep(t){ return pickTime(t.depPlanned, t.depReal); }
  function tripArr(t){ return pickTime(t.arrPlanned, t.arrReal); }
  function colCount(){ return 12 + (showRoute()?1:0) + (showDelay()?1:0); }

  function dash(v){ return v?esc(v):'<span class="muted">—</span>'; }
  function delayCell(d){ if(d==null) return '<span class="muted">—</span>';
    if(d>0) return `<span class="pos">+${d}</span>`;
    if(d<0) return `<span class="neg">${d}</span>`; return "0"; }
  function delayText(d){ if(d==null) return ""; if(d>0) return "+"+d; return String(d); }
  function timeText(iso){ if(!iso) return ""; const s=fmtTime(iso); return s==="—"?"":s; }

  function stopKey(s){
    if(s && s.id!=null && s.id!=="") return String(s.id);
    return "n:"+((s && s.name)||"");
  }
  function tripPath(t){
    const stops=t.stopovers||[];
    const out=[];
    const n=stops.length;
    if(!n){
      if(t.from) out.push({key:"n:"+t.from, name:t.from});
      if(t.to && (!t.from || t.to!==t.from)) out.push({key:"n:"+t.to, name:t.to});
      return out;
    }
    stops.forEach((s,i)=>{
      if(i!==0 && i!==n-1 && s.cancelled) return;
      const key=stopKey(s);
      if(out.length && out[out.length-1].key===key) return;
      out.push({key, name:s.name||""});
    });
    return out;
  }
  function firstAB(path, aKey, bKey){
    let iA=-1;
    for(let i=0;i<path.length;i++){
      if(iA<0 && path[i].key===aKey) iA=i;
      else if(iA>=0 && path[i].key===bKey) return [iA, i];
    }
    return null;
  }
  function computeRoutes(list){
    const paths=list.map(tripPath);
    const nbr=new Map(), od=new Set();
    function addUndirected(a,b){
      if(a===b) return;
      if(!nbr.has(a)) nbr.set(a,new Set());
      if(!nbr.has(b)) nbr.set(b,new Set());
      nbr.get(a).add(b);
      nbr.get(b).add(a);
    }
    paths.forEach(p=>{
      if(!p.length) return;
      od.add(p[0].key);
      od.add(p[p.length-1].key);
      for(let i=0;i<p.length-1;i++) addUndirected(p[i].key, p[i+1].key);
    });
    const anchors=new Set(od);
    paths.forEach(p=>p.forEach(s=>{
      if((nbr.get(s.key)||new Set()).size>2) anchors.add(s.key);
    }));
    function setsBetween(aKey, bKey){
      const sets=[];
      paths.forEach(p=>{
        const pair=firstAB(p, aKey, bKey);
        if(!pair) return;
        const mid=new Set();
        for(let i=pair[0]+1;i<pair[1];i++) mid.add(p[i].key);
        sets.push(mid);
      });
      return sets;
    }
    const out=new Map();
    list.forEach((t,idx)=>{
      const p=paths[idx];
      if(p.length<=1){
        out.set(t._i, p.map(s=>s.name).filter(Boolean).join(" → "));
        return;
      }
      const forced=[];
      p.forEach((s,i)=>{
        if(i===0 || i===p.length-1 || anchors.has(s.key)) forced.push(i);
      });
      const keep=new Set(forced);
      for(let f=0;f<forced.length-1;f++){
        const iA=forced[f], iB=forced[f+1];
        if(iB<=iA+1) continue;
        const sets=setsBetween(p[iA].key, p[iB].key);
        if(sets.length<2) continue;
        const sigs=new Set(sets.map(s=>[...s].sort().join("\0")));
        if(sigs.size<2) continue;
        let inter=null;
        sets.forEach(s=>{
          if(inter==null){ inter=new Set(s); return; }
          [...inter].forEach(k=>{ if(!s.has(k)) inter.delete(k); });
        });
        const uniqueIdx=[];
        for(let i=iA+1;i<iB;i++){
          if(!inter.has(p[i].key)) uniqueIdx.push(i);
        }
        if(!uniqueIdx.length) continue;
        keep.add(uniqueIdx[Math.floor((uniqueIdx.length-1)/2)]);
      }
      const parts=[];
      let lastKey=null;
      p.forEach((s,i)=>{
        if(!keep.has(i) || s.key===lastKey) return;
        lastKey=s.key;
        if(s.name) parts.push(s.name);
      });
      out.set(t._i, parts.join(" → "));
    });
    return out;
  }
  function ensureRoutes(list){
    if(!showRoute()) return;
    const key=list.map(t=>t._i).slice().sort((a,b)=>a-b).join(",");
    if(key===routeCacheKey) return;
    routeCacheKey=key;
    routeByI=computeRoutes(list);
  }
  function routeOf(t){ return routeByI.get(t._i)||""; }

  function detailHtml(t){
    const head=`<div class="stops"><div class="h">Halt</div><div class="h">An</div>
      <div class="h">Ab</div><div class="h">Gleis</div>`;
    const body=t.stopovers.map(s=>{
      const an = pickTime(s.arrivalPlanned, s.arrivalReal);
      const ab = pickTime(s.departurePlanned, s.departureReal);
      const cancel = s.cancelled? ' style="text-decoration:line-through;color:#d1242f"':'';
      return `<div${cancel}>${esc(s.name)}</div><div>${fmtTime(an)}</div>
        <div>${fmtTime(ab)}</div><div>${esc(s.platform||"—")}</div>`;
    }).join("");
    const note = t.body? `<div class="muted" style="margin-top:8px">„${esc(t.body)}"</div>`:"";
    return `<td colspan="${colCount()}">${head}${body}</div>${note}</td>`;
  }

  function filteredSorted(){
    const q=filterEl.value.toLowerCase().trim();
    const fromVal=dateFromEl.value;
    const toVal=dateToEl.value;
    // Immer Kopie: sonst sortiert list===rows die Quelle in-place und
    // rows[data-i] trifft nach dem Sort die falsche Fahrt.
    let list=rows.filter(t=>{
      if(q){
        const hay=(t.date+" "+lineName(t.line)+" "+(t.operator||"")+" "+
          t.locClass+" "+t.vehicles+" "+t.category+" "+t.from+" "+t.to)
          .toLowerCase();
        if(!hay.includes(q)) return false;
      }
      const day=(t.date||"").slice(0,10);
      if(fromVal && day<fromVal) return false;
      if(toVal && day>toVal) return false;
      return true;
    });
    if(showRoute() && sortKey==="route") ensureRoutes(list);
    list.sort((a,b)=>{
      let x=a[sortKey], y=b[sortKey];
      if(sortKey==="line"){ x=lineName(x); y=lineName(y); }
      if(sortKey==="depTime"){ x=tripDep(a); y=tripDep(b); }
      if(sortKey==="arrTime"){ x=tripArr(a); y=tripArr(b); }
      if(sortKey==="route"){ x=routeOf(a); y=routeOf(b); }
      if(x==null)x=-Infinity; if(y==null)y=-Infinity;
      if(typeof x==="string"){ const r=x.localeCompare(y); return sortAsc?r:-r; }
      return sortAsc? x-y : y-x;
    });
    return list;
  }

  function applyExtraCols(){
    table.classList.toggle("no-delay", !showDelay());
    table.classList.toggle("no-route", !showRoute());
    const hidden=(sortKey==="delay" && !showDelay()) || (sortKey==="route" && !showRoute());
    if(hidden){
      sortKey="date"; sortAsc=false;
      document.querySelectorAll("#tripsTable th").forEach(x=>x.classList.remove("sorted","asc"));
      const th=table.querySelector('th[data-k="date"]');
      if(th) th.classList.add("sorted");
    }
  }

  function render(){
    applyExtraCols();
    visible=filteredSorted();
    if(showRoute()) ensureRoutes(visible);
    else { routeCacheKey=""; routeByI=new Map(); }
    document.getElementById("tripcount").textContent=visible.length+" Fahrten";
    tbody.innerHTML=visible.map(t=>
      `<tr class="trip" data-i="${t._i}">
        <td>${fmtDate(t.date)}</td><td>${esc(lineName(t.line))}</td>
        <td>${dash(t.operator)}</td>
        <td>${dash(t.locClass)}</td>
        <td>${dash(t.vehicles)}</td>
        <td>${esc(t.from)}</td><td>${esc(t.to)}</td>
        <td>${fmtTime(tripDep(t))}</td><td>${fmtTime(tripArr(t))}</td>
        <td>${t.viaStops}</td>
        <td class="route">${esc(routeOf(t))}</td>
        <td>${t.distanceKm}</td><td>${t.durationMin}</td>
        <td class="delay">${delayCell(t.delay)}</td></tr>`).join("");
  }

  function tsvCell(s){ return String(s??"").replace(/[\t\n\r]+/g," ").trim(); }
  function copyFallback(text){
    const ta=document.createElement("textarea");
    ta.value=text; ta.setAttribute("readonly","");
    ta.style.cssText="position:fixed;left:-9999px";
    document.body.appendChild(ta); ta.select();
    let ok=false;
    try{ ok=document.execCommand("copy"); }catch(e){}
    document.body.removeChild(ta);
    return ok;
  }
  function copyFeedback(ok){
    const old=copyBtn.textContent;
    copyBtn.textContent=ok?"Kopiert":"Kopieren fehlgeschlagen";
    setTimeout(()=>{ copyBtn.textContent=old; }, ok?1500:2000);
  }
  function copyTsv(){
    const headers=TSV_BASE.slice();
    const routeOn=showRoute(), delayOn=showDelay();
    if(routeOn) headers.splice(10, 0, "Laufweg");
    if(delayOn) headers.push("Versp.");
    const lines=[headers.join("\t")];
    visible.forEach(t=>{
      const row=[fmtDate(t.date), lineName(t.line), t.operator||"", t.locClass||"",
        t.vehicles||"", t.from||"", t.to||"", timeText(tripDep(t)), timeText(tripArr(t)),
        t.viaStops];
      if(routeOn) row.push(routeOf(t));
      row.push(t.distanceKm, t.durationMin);
      if(delayOn) row.push(delayText(t.delay));
      lines.push(row.map(tsvCell).join("\t"));
    });
    const text=lines.join("\n");
    if(navigator.clipboard && window.isSecureContext){
      navigator.clipboard.writeText(text).then(()=>copyFeedback(true))
        .catch(()=>copyFeedback(copyFallback(text)));
      return;
    }
    copyFeedback(copyFallback(text));
  }

  tbody.addEventListener("click",e=>{
    const tr=e.target.closest("tr.trip"); if(!tr) return;
    const next=tr.nextElementSibling;
    if(next && next.classList.contains("detail")){ next.remove(); return; }
    document.querySelectorAll("tr.detail").forEach(d=>d.remove());
    const t=DATA.trips[+tr.dataset.i];
    const dr=document.createElement("tr"); dr.className="detail";
    dr.innerHTML=detailHtml(t); tr.after(dr);
  });

  document.querySelectorAll("#tripsTable th").forEach(th=>th.onclick=()=>{
    const k=th.dataset.k;
    if((k==="delay" && !showDelay()) || (k==="route" && !showRoute())) return;
    if(sortKey===k) sortAsc=!sortAsc; else { sortKey=k; sortAsc=true; }
    document.querySelectorAll("#tripsTable th").forEach(x=>x.classList.remove("sorted","asc"));
    th.classList.add("sorted"); if(sortAsc) th.classList.add("asc");
    render();
  });
  delayEl.onchange=()=>{ prefSet("trwl-trips-delay", showDelay()?"1":"0"); render(); };
  routeEl.onchange=render;
  timeEl.onchange=()=>{ prefSet("trwl-trips-times", timeMode()); render(); };
  copyBtn.onclick=copyTsv;
  filterEl.oninput=render;
  [dateFromEl,dateToEl].forEach(el=>{
    el.onchange=render;
    el.oninput=render;
  });
  render();
})();

