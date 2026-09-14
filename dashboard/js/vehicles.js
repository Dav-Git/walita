// ---------- Vehicle matrix ----------
(function(){
  const V=DATA.vehicles||[];
  const LC=DATA.lineColors||{};
  const container=document.getElementById("vehMatrices");
  const groupEl=document.getElementById("vehGroup");
  const opEl=document.getElementById("vehOperator");
  const catEl=document.getElementById("vehCategory");
  const yearEl=document.getElementById("vehYear");
  const filterEl=document.getElementById("vehFilter");
  const countEl=document.getElementById("vehCount");
  const modal=document.getElementById("vehModal");
  const modalBody=document.getElementById("vehModalBody");

  // CAT_LABEL/catLabel sind global definiert (siehe oben).
  let sortKey="vehicleNumber", sortAsc=true;
  let cellRegistry=[];

  function fillSelect(el,opts){
    el.innerHTML='<option value="__all__">Alle</option>'+
      opts.map(o=>`<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
  }
  function uniq(arr){ return [...new Set(arr)]; }

  // Dropdowns aus den Datensätzen befüllen.
  fillSelect(opEl, uniq(V.map(r=>r.operator)).sort()
    .map(o=>({value:o,label:o||"(ohne Operator)"})));
  fillSelect(catEl, uniq(V.map(r=>r.category)).filter(Boolean).sort()
    .map(c=>({value:c,label:catLabel(c)})));
  fillSelect(yearEl, uniq(V.map(r=>(r.date||"").slice(0,4)).filter(Boolean)).sort().reverse()
    .map(y=>({value:y,label:y})));

  function cls(k){ return sortKey===k ? (sortAsc?"sorted asc":"sorted") : ""; }

  function selectedShows(){
    return [...document.querySelectorAll('input[name="vehShow"]:checked')].map(el=>el.value);
  }
  function cellText(cr, shows){
    const parts=[];
    if(shows.includes("date")){
      const latest=cr.reduce((a,b)=>a.date>=b.date?a:b);
      parts.push(fmtDate(latest.date));
    }
    if(shows.includes("count")) parts.push("×"+cr.length);
    if(shows.includes("km")){
      const km=Math.round(cr.reduce((s,r)=>s+(r.distanceKm||0),0)*10)/10;
      parts.push(km+" km");
    }
    if(shows.includes("segments")){
      const seg=cr.reduce((s,r)=>s+(r.segments||0),0);
      parts.push(seg+" Seg.");
    }
    return parts.join(" · ");
  }

  function rowCmp(a,b){
    let x,y;
    if(sortKey==="first"){ x=a.first; y=b.first; }
    else if(sortKey==="last"){ x=a.last; y=b.last; }
    else {
      const nx=parseFloat(a.number), ny=parseFloat(b.number);
      if(!isNaN(nx)&&!isNaN(ny)) return sortAsc? nx-ny : ny-nx;
      x=a.number; y=b.number;
    }
    const r=String(x).localeCompare(String(y),undefined,{numeric:true});
    return sortAsc? r : -r;
  }

  /** Ganzzahlige Wagennummer oder NaN (nur reine Ziffernketten). */
  function vehInt(n){
    const s=String(n==null?"":n).trim();
    return /^\d+$/.test(s) ? +s : NaN;
  }
  /** Markiert Läufe benachbarter Zeilen mit Nummern ±1 (Start/Mitte/Ende). */
  function markStreaks(rows){
    const nums=rows.map(r=>vehInt(r.number));
    rows.forEach(r=>{ r.streak=""; });
    let i=0;
    while(i<rows.length){
      if(isNaN(nums[i])){ i++; continue; }
      let j=i+1;
      let step=null;
      while(j<rows.length && !isNaN(nums[j])){
        const d=nums[j]-nums[j-1];
        if(d!==1 && d!==-1) break;
        if(step===null) step=d;
        else if(d!==step) break;
        j++;
      }
      if(j-i>=2){
        rows[i].streak="veh-streak veh-streak-start";
        for(let k=i+1;k<j-1;k++) rows[k].streak="veh-streak veh-streak-mid";
        rows[j-1].streak="veh-streak veh-streak-end";
      }
      i=j>i ? j : i+1;
    }
  }

  function renderMatrices(){
    const groupDim=groupEl.value;
    const op=opEl.value, cat=catEl.value, yr=yearEl.value;
    const q=filterEl.value.toLowerCase().trim();
    const shows=selectedShows();
    cellRegistry=[];

    const filtered=V.filter(r=>{
      if(op!=="__all__" && r.operator!==op) return false;
      if(cat!=="__all__" && r.category!==cat) return false;
      if(yr!=="__all__" && (r.date||"").slice(0,4)!==yr) return false;
      if(q && !((r.vehicleNumber+" "+lineName(r.line)).toLowerCase().includes(q))) return false;
      return true;
    });

    if(!V.length){
      container.innerHTML='<p class="muted">Keine Fahrzeug-Tags vorhanden. Tagge Fahrten in '+
        'Träwelling mit Wagennummer/Baureihe – die Tags erscheinen beim nächsten Export.</p>';
      countEl.textContent="";
      return;
    }
    if(!filtered.length){
      container.innerHTML='<p class="muted">Keine Fahrzeuge für die gewählten Filter.</p>';
      countEl.textContent="0 Fahrzeuge";
      return;
    }

    // Nach Gruppierungs-Dimension gruppieren.
    const groups=new Map();
    filtered.forEach(r=>{
      const g=groupDim==="locClass" ? (r.locClass||"Unbekannt") : catLabel(r.category);
      if(!groups.has(g)) groups.set(g,[]);
      groups.get(g).push(r);
    });
    const groupNames=[...groups.keys()].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
    const showClass=groupDim!=="locClass";

    let totalVeh=0, html="";
    groupNames.forEach(gname=>{
      const recs=groups.get(gname);
      // Zeilen (Fahrzeuge) und Spalten (Linien) der Gruppe sammeln.
      const rowMap=new Map();
      const lineSet=new Set();
      recs.forEach(r=>{
        const key=r.locClass+"|"+r.vehicleNumber;
        if(!rowMap.has(key)) rowMap.set(key,{number:r.vehicleNumber,locClass:r.locClass,recs:[]});
        rowMap.get(key).recs.push(r);
        if(r.line) lineSet.add(r.line);
      });
      const lines=[...lineSet].sort((a,b)=>
        lineName(a).localeCompare(lineName(b),undefined,{numeric:true})
        || String(a).localeCompare(String(b)));
      const rowArr=[...rowMap.values()];
      rowArr.forEach(row=>{
        const ds=row.recs.map(r=>r.date).filter(Boolean).sort();
        row.first=ds[0]||""; row.last=ds[ds.length-1]||"";
        row.count=row.recs.length;
        row.km=Math.round(row.recs.reduce((s,r)=>s+(r.distanceKm||0),0)*10)/10;
      });
      rowArr.sort(rowCmp);
      markStreaks(rowArr);
      totalVeh+=rowArr.length;
      const groupKm=Math.round(recs.reduce((s,r)=>s+(r.distanceKm||0),0)*10)/10;

      const head=`<tr>
        <th class="sticky lbl ${cls('vehicleNumber')}" data-k="vehicleNumber">Wagen</th>
        ${showClass?'<th class="lbl">Baureihe</th>':''}
        <th class="lbl ${cls('first')}" data-k="first">zuerst</th>
        <th class="lbl ${cls('last')}" data-k="last">zuletzt</th>
        ${lines.map(l=>{
          const c=LC[l];
          const st=c?` style="background:${c[0]};color:${c[1]}"`:'';
          return `<th class="lbl lineh"><span class="line-badge"${st}>${esc(lineName(l))}</span></th>`;
        }).join("")}
      </tr>`;
      const body=rowArr.map(row=>{
        const cells=lines.map(l=>{
          const cr=row.recs.filter(x=>x.line===l);
          if(!cr.length) return "<td></td>";
          const ci=cellRegistry.push(cr)-1;
          const c=LC[l];
          const tint=c?` style="--tint:${c[0]}22"`:'';
          return `<td class="has"${tint} data-ci="${ci}">${cellText(cr,shows)}</td>`;
        }).join("");
        const streakCls=row.streak?` ${row.streak}`:"";
        return `<tr class="veh">
          <td class="sticky lbl${streakCls}">${esc(row.number)}`+
          `<span class="muted veh-stats">×${row.count} · ${row.km} km</span></td>
          ${showClass?`<td class="lbl">${esc(row.locClass||"—")}</td>`:''}
          <td class="lbl">${fmtDate(row.first)}</td>
          <td class="lbl">${fmtDate(row.last)}</td>
          ${cells}</tr>`;
      }).join("");
      html+=`<div class="matrix-wrap"><h3>${esc(gname)} · ${rowArr.length} `+
        `${rowArr.length===1?"Fahrzeug":"Fahrzeuge"} · ×${recs.length} · ${groupKm} km</h3>`+
        `<table class="matrix"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    });
    container.innerHTML=html;
    countEl.textContent=totalVeh+" Fahrzeuge · "+filtered.length+" Fahrten";
  }

  function openModal(ci){
    const recs=(cellRegistry[ci]||[]).slice()
      .sort((a,b)=>String(b.date).localeCompare(String(a.date)));
    if(!recs.length) return;
    const r0=recs[0];
    const head=`<h3>Wagen ${esc(r0.vehicleNumber)}`+
      `${r0.locClass?" · BR "+esc(r0.locClass):""} · Linie ${esc(lineName(r0.line))}</h3>`;
    const body=recs.map(r=>`<div class="ride">
      <div class="d">${fmtDate(r.date)} · ${esc(lineName(r.line))}</div>
      <div>${esc(r.from)} → ${esc(r.to)}</div>
      <div class="muted">${fmtTime(r.depTime)}–${fmtTime(r.arrTime)} · ${r.distanceKm} km · `+
      `${r.points} Punkte${r.operator?" · "+esc(r.operator):""}</div>
    </div>`).join("");
    modalBody.innerHTML=head+body;
    modal.classList.add("open");
  }
  function closeModal(){ modal.classList.remove("open"); }

  container.addEventListener("click",e=>{
    const th=e.target.closest("th[data-k]");
    if(th){ const k=th.dataset.k;
      if(sortKey===k) sortAsc=!sortAsc; else { sortKey=k; sortAsc=true; }
      renderMatrices(); return; }
    const td=e.target.closest("td.has");
    if(td) openModal(+td.dataset.ci);
  });
  document.getElementById("vehModalClose").onclick=closeModal;
  modal.addEventListener("click",e=>{ if(e.target===modal) closeModal(); });
  document.addEventListener("keydown",e=>{ if(e.key==="Escape") closeModal(); });
  [groupEl,opEl,catEl,yearEl].forEach(el=>el.onchange=renderMatrices);
  filterEl.oninput=renderMatrices;
  document.querySelectorAll('input[name="vehShow"]').forEach(el=>{
    el.addEventListener("change",()=>{
      if(!selectedShows().length){ el.checked=true; return; }
      renderMatrices();
    });
  });
  renderMatrices();
})();

