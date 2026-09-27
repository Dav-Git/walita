// ---------- Statistiken (Linie / Baureihe / Fahrzeug / Station) ----------
(function(){
  const WD=["Mo","Di","Mi","Do","Fr","Sa","So"];
  function lineColors(){ return D().lineColors||{}; }
  const turbo=(t)=>{
    t=Math.max(0,Math.min(1,t));
    const stops=[[0.0,[48,18,59]],[0.1,[68,57,144]],[0.2,[65,117,199]],
      [0.3,[46,167,194]],[0.4,[51,200,142]],[0.5,[126,214,63]],
      [0.6,[210,214,52]],[0.7,[249,186,52]],[0.8,[245,116,36]],[1.0,[122,4,3]]];
    let a=stops[0], b=stops[stops.length-1];
    for(let i=0;i<stops.length-1;i++) if(t>=stops[i][0]&&t<=stops[i+1][0]){a=stops[i];b=stops[i+1];break;}
    const u=(t-a[0])/((b[0]-a[0])||1);
    const rgb=a[1].map((v,i)=>Math.round(v+(b[1][i]-v)*u));
    // Relative Luminanz: dunkler Hintergrund → weißer Text.
    const lum=0.2126*rgb[0]+0.7152*rgb[1]+0.0722*rgb[2];
    return {bg:`rgb(${rgb[0]},${rgb[1]},${rgb[2]})`, fg: lum<140?"#ffffff":"#111a27"};
  };
  function lineBadge(key){
    const name=lineName(key);
    const c=lineColors()[key];
    const st=c?` style="background:${c[0]};color:${c[1]}"`:"";
    return `<span class="line-badge"${st}>${esc(name)}</span>`;
  }
  function fmtNum(v){
    if(v==null||v==="") return "—";
    return Number(v).toLocaleString("de-DE");
  }
  function fmtPct(v){ return v==null?"—":fmtNum(v)+" %"; }
  function tripLabel(t){
    if(!t) return "—";
    return `${esc(t.line||"")} · ${esc(t.from||"")} → ${esc(t.to||"")} · ${fmtNum(t.km)} km`+
      (t.date?` · ${esc(t.date)}`:"");
  }

  // KPI-Überblick
  let applyStationSort=function(){};
  document.querySelectorAll("[data-station-sort]").forEach(a=>{
    a.addEventListener("click",()=>{
      const k=a.getAttribute("data-station-sort");
      if(k) applyStationSort(k);
    });
  });
  function renderStats(){
  const S=D().stats;
  if(!S){
    ["stats-extra","stats-lines","stats-loc","stats-veh","stats-stations",
     "stats-edges","stats-repeat","stats-cross"].forEach(id=>{
      const el=document.getElementById(id);
      if(el) el.innerHTML="";
    });
    document.getElementById("stats-extra").innerHTML=
      "<p class='hint'>Keine Statistik-Daten vorhanden.</p>";
    applyStationSort=function(){};
    return;
  }
  const ex=S.extra||{};
  const cards=[
    ["Linien", fmtNum(ex.lines)],
    ["Baureihen", fmtNum(ex.locClasses)],
    ["Fahrzeuge", fmtNum(ex.vehicles)],
    ["Einstiegs-St.", fmtNum(ex.stationsBoarded)],
    ["Ausstiegs-St.", fmtNum(ex.stationsAlighted)],
    ["Gehalten-St.", fmtNum(ex.stationsThrough)],
    ["Physisch-St.", fmtNum(ex.stationsPassed)],
    ["Kanten", fmtNum(ex.edges)],
    ["Kanten >1×", fmtNum(ex.edgesRepeat)],
    ["Fz×Kante >1", fmtNum(ex.vehEdgeRepeat)],
    ["Tag Baureihe", fmtPct(ex.tagLocPct)],
    ["Tag Fahrzeug", fmtPct(ex.tagVehPct)],
    ["Unique Linie×Fz", fmtNum(ex.uniqueLineVehicle)],
    ["Unique Linie×BR", fmtNum(ex.uniqueLineLocClass)],
    ["Unique Routen", fmtNum(ex.uniqueRoutes)],
  ];
  let extraHtml=`<h3>Überblick</h3>
    <div class="cards">${cards.map(c=>
      `<div class="card"><div class="v">${c[1]}</div><div class="l">${esc(c[0])}</div></div>`
    ).join("")}</div>
    <div class="panel" style="margin-top:16px">
      <div><strong>Längste Strecke:</strong> ${tripLabel(ex.maxTrip)}</div>
      <div style="margin-top:6px"><strong>Kürzeste Strecke:</strong> ${tripLabel(ex.minTrip)}</div>
      ${ex.topLine?`<div style="margin-top:6px"><strong>Top-Linie (km):</strong> ${lineBadge(ex.topLine.key)} · ${fmtNum(ex.topLine.km)} km</div>`:""}
      ${ex.topVehicle?`<div style="margin-top:6px"><strong>Top-Fahrzeug (km):</strong> ${esc(ex.topVehicle.key)}${ex.topVehicle.locClass?` · ${esc(ex.topVehicle.locClass)}`:""} · ${fmtNum(ex.topVehicle.km)} km</div>`:""}
    </div>`;
  document.getElementById("stats-extra").innerHTML=extraHtml;

  function renderBarTable(containerId, title, hint, rows, cols, opts){
    opts=opts||{};
    const el=document.getElementById(containerId);
    const initialLimit=opts.initialLimit||0;
    if(!rows||!rows.length){
      el.innerHTML=`<h3>${esc(title)}</h3><p class="hint">${esc(hint||"Keine Daten.")}</p>`;
      return;
    }
    let sortKey=opts.defaultSort||"distanceKm";
    let sortAsc=!!opts.defaultAsc;
    let showAll=!initialLimit || rows.length<=initialLimit;
    function sorted(){
      return rows.slice().sort((a,b)=>{
        let av=a[sortKey], bv=b[sortKey];
        if(av==null) av=sortAsc?Infinity:-Infinity;
        if(bv==null) bv=sortAsc?Infinity:-Infinity;
        if(typeof av==="string"||typeof bv==="string"){
          const c=String(av).localeCompare(String(bv),"de",{numeric:true});
          return sortAsc?c:-c;
        }
        return sortAsc?av-bv:bv-av;
      });
    }
    function paint(){
      const all=sorted();
      const data=(!showAll && initialLimit)?all.slice(0, initialLimit):all;
      const remaining=all.length-data.length;
      const maxBar=Math.max(1,...all.map(r=>+r.distanceKm||0));
      const head=cols.map(c=>{
        const cls=c.key===sortKey?(sortAsc?"sort-asc":"sort-desc"):"";
        return `<th class="${c.lbl?"lbl ":""}${cls}" data-k="${c.key}">${esc(c.label)}</th>`;
      }).join("");
      const body=data.map(r=>{
        return "<tr>"+cols.map(c=>{
          if(c.key==="key" && opts.lineKeys){
            return `<td class="lbl">${lineBadge(r.key)}</td>`;
          }
          if(c.key==="_bar"){
            const pct=Math.round(100*(+r.distanceKm||0)/maxBar);
            return `<td><div class="barcell"><span class="n">${fmtNum(r.distanceKm)}</span>`+
              `<div class="bar"><i style="width:${pct}%"></i></div></div></td>`;
          }
          if(c.route) return `<td class="route" title="${esc(r[c.key]||"")}">${esc(r[c.key]||"—")}</td>`;
          if(c.pct) return `<td>${fmtPct(r[c.key])}</td>`;
          if(c.date) return `<td>${esc(r[c.key]||"—")}</td>`;
          if(c.lbl) return `<td class="lbl">${esc(r[c.key]==null||r[c.key]===""?"—":r[c.key])}</td>`;
          return `<td>${fmtNum(r[c.key])}</td>`;
        }).join("")+"</tr>";
      }).join("");
      const more=remaining>0
        ? `<button type="button" class="stats-more">Mehr laden (${fmtNum(remaining)} weitere, ${fmtNum(all.length)} gesamt)</button>`
        : "";
      el.innerHTML=`<h3>${esc(title)}</h3>
        <p class="hint">${esc(hint||"")}</p>
        <div class="panel stats-scroll"><table class="stats"><thead><tr>${head}</tr></thead>
        <tbody>${body}</tbody></table>${more}</div>`;
      el.querySelectorAll("th[data-k]").forEach(th=>{
        th.onclick=()=>{
          const k=th.dataset.k;
          if(sortKey===k) sortAsc=!sortAsc; else { sortKey=k; sortAsc=!!cols.find(c=>c.key===k&&c.date); }
          paint();
        };
      });
      const moreBtn=el.querySelector(".stats-more");
      if(moreBtn) moreBtn.onclick=()=>{ showAll=true; paint(); };
    }
    paint();
  }

  const baseCols=[
    {key:"_bar", label:"km"},
    {key:"count", label:"Fahrten"},
    {key:"avgDistanceKm", label:"Ø km"},
    {key:"durationMin", label:"Min"},
    {key:"points", label:"Pkt"},
    {key:"first", label:"zuerst", date:true},
    {key:"last", label:"zuletzt", date:true},
    {key:"minDistanceKm", label:"min km"},
    {key:"minRoute", label:"kürzeste", route:true},
    {key:"maxDistanceKm", label:"max km"},
    {key:"maxRoute", label:"längste", route:true},
    {key:"uniqueRoutes", label:"Routen"},
    {key:"uniqueWeekdays", label:"WTage"},
    {key:"uniqueMonths", label:"Monate"},
    {key:"avgDelay", label:"Ø Versp."},
    {key:"onTimePct", label:"pünktig %", pct:true},
  ];

  renderBarTable("stats-lines", "Linien",
    "Nach Kilometern – Klick auf Spaltenkopf sortiert. Zunächst 50 Einträge.",
    S.byLine, [{key:"key", label:"Linie", lbl:true}, ...baseCols,
      {key:"uniqueVehicles", label:"Fz"},
      {key:"uniqueLocClasses", label:"BR"}],
    {lineKeys:true, initialLimit:50});

  renderBarTable("stats-loc", "Baureihen",
    S.byLocClass&&S.byLocClass.length
      ? "Alle getaggten Baureihen."
      : "Keine Baureihen-Tags (trwl:locomotive_class) in den Daten.",
    S.byLocClass, [{key:"key", label:"Baureihe", lbl:true}, ...baseCols,
      {key:"uniqueVehicles", label:"Fz"},
      {key:"uniqueLines", label:"Linien"}]);

  renderBarTable("stats-veh", "Fahrzeuge",
    S.byVehicle&&S.byVehicle.length
      ? "Fahrzeuge nach Kilometern, getrennt nach Baureihe und Wagennummer. Zunächst 50 Einträge."
      : "Keine Fahrzeug-Tags (trwl:vehicle_number) in den Daten.",
    S.byVehicle, [{key:"key", label:"Wagen", lbl:true},
      {key:"locClass", label:"BR", lbl:true}, ...baseCols,
      {key:"uniqueLines", label:"Linien"}],
    {initialLimit:50});

  // Stationen: Ein / Aus / gehalten / physisch mit kombinierbaren Rollenfiltern
  (function renderStations(){
    const el=document.getElementById("stats-stations");
    const rows=S.byStation||[];
    if(!rows.length){
      el.innerHTML=`<h3>Stationen</h3>
        <p class="hint">Keine Stations-Daten (keine befahrbaren Stopovers).</p>`;
      return;
    }
    let useBoarded=true, useAlighted=true, useThrough=true, usePassed=true, andMode=false;
    let sortKey="total", sortAsc=false;
    const cols=[
      {key:"key", label:"Station", lbl:true},
      {key:"boarded", label:"Eingestiegen"},
      {key:"alighted", label:"Ausgestiegen"},
      {key:"through", label:"Gehalten"},
      {key:"passed", label:"Physisch"},
      {key:"total", label:"Summe"},
      {key:"first", label:"zuerst", date:true},
      {key:"last", label:"zuletzt", date:true},
      {key:"uniqueLines", label:"Linien"},
    ];
    function roleSum(r){
      return (useBoarded?r.boarded:0)+(useAlighted?r.alighted:0)
        +(useThrough?r.through:0)+(usePassed?r.passed||0:0);
    }
    function matches(r){
      const parts=[];
      if(useBoarded) parts.push(r.boarded>0);
      if(useAlighted) parts.push(r.alighted>0);
      if(useThrough) parts.push(r.through>0);
      if(usePassed) parts.push((r.passed||0)>0);
      if(!parts.length) return false;
      return andMode?parts.every(Boolean):parts.some(Boolean);
    }
    function cellVal(r, key){
      if(key==="total") return roleSum(r);
      return r[key];
    }
    function paint(){
      const data=rows.filter(matches).map(r=>({...r, total:roleSum(r)})).sort((a,b)=>{
        let av=cellVal(a, sortKey), bv=cellVal(b, sortKey);
        if(av==null) av=sortAsc?Infinity:-Infinity;
        if(bv==null) bv=sortAsc?Infinity:-Infinity;
        if(typeof av==="string"||typeof bv==="string"){
          const c=String(av).localeCompare(String(bv),"de",{numeric:true});
          return sortAsc?c:-c;
        }
        return sortAsc?av-bv:bv-av;
      });
      const head=cols.map(c=>{
        const cls=c.key===sortKey?(sortAsc?"sort-asc":"sort-desc"):"";
        return `<th class="${c.lbl?"lbl ":""}${cls}" data-k="${c.key}">${esc(c.label)}</th>`;
      }).join("");
      const body=data.map(r=>"<tr>"+cols.map(c=>{
        if(c.date) return `<td>${esc(cellVal(r,c.key)||"—")}</td>`;
        if(c.lbl){
          const v=cellVal(r,c.key);
          return `<td class="lbl">${esc(v==null||v===""?"—":v)}</td>`;
        }
        return `<td>${fmtNum(cellVal(r,c.key))}</td>`;
      }).join("")+"</tr>").join("");
      el.innerHTML=`<h3>Stationen</h3>
        <p class="hint">Pro Fahrt: Einstieg am ersten Halt, Ausstieg am letzten, gehalten an Träwelling-Zwischenhalten, physische Durchfahrt an Patch-Via-Stationen.
          Entfällt-Zwischenhalte zählen weder als gehalten noch als Durchfahrt, bis sie als Via im Patch stehen.
          Tag dubi=start/ende zählt Origin/Destination als gehalten. Summe und Filter beziehen sich auf die aktivierten Rollen.</p>
        <div class="stats-roles">
          <label><input type="checkbox" id="stRoleB" ${useBoarded?"checked":""}> Eingestiegen</label>
          <label><input type="checkbox" id="stRoleA" ${useAlighted?"checked":""}> Ausgestiegen</label>
          <label><input type="checkbox" id="stRoleT" ${useThrough?"checked":""}> Gehalten</label>
          <label><input type="checkbox" id="stRoleP" ${usePassed?"checked":""}> Physisch</label>
          <span class="sep"></span>
          <label><input type="checkbox" id="stRoleAnd" ${andMode?"checked":""}> nur Kombination</label>
          <span style="margin-left:auto">${fmtNum(data.length)} Stationen</span>
        </div>
        <div class="panel stats-scroll"><table class="stats"><thead><tr>${head}</tr></thead>
        <tbody>${body.length?body:`<tr><td colspan="${cols.length}" class="lbl">Keine Stationen für diese Filter.</td></tr>`}</tbody></table></div>`;
      el.querySelector("#stRoleB").onchange=e=>{ useBoarded=e.target.checked; paint(); };
      el.querySelector("#stRoleA").onchange=e=>{ useAlighted=e.target.checked; paint(); };
      el.querySelector("#stRoleT").onchange=e=>{ useThrough=e.target.checked; paint(); };
      el.querySelector("#stRoleP").onchange=e=>{ usePassed=e.target.checked; paint(); };
      el.querySelector("#stRoleAnd").onchange=e=>{ andMode=e.target.checked; paint(); };
      el.querySelectorAll("th[data-k]").forEach(th=>{
        th.onclick=()=>{
          const k=th.dataset.k;
          if(sortKey===k) sortAsc=!sortAsc; else { sortKey=k; sortAsc=!!cols.find(c=>c.key===k&&c.date); }
          paint();
        };
      });
    }
    paint();
    applyStationSort=function(k){
      if(!k) return;
      sortKey=k; sortAsc=false;
      if(k==="boarded") useBoarded=true;
      if(k==="alighted") useAlighted=true;
      if(k==="through") useThrough=true;
      if(k==="passed") usePassed=true;
      paint();
    };
  })();

  function renderComboTable(mountEl, title, hint, rows, cols, opts){
    opts=opts||{};
    const jumpId=opts.id||"";
    const idAttr=jumpId?` id="${esc(jumpId)}"`:"";
    const initialLimit=opts.initialLimit||0;
    if(!rows||!rows.length){
      const empty=`<div class="panel stats-jump" style="margin-bottom:16px"${idAttr}><h3 style="margin:0 0 6px;font-size:14px">${esc(title)}</h3>
        <p class="hint" style="margin:0">${esc(hint||"Keine Einträge.")}</p></div>`;
      if(mountEl){ mountEl.innerHTML=empty; return ""; }
      return empty;
    }
    const wrap=document.createElement("div");
    wrap.className="panel stats-scroll stats-jump";
    wrap.style.marginBottom="16px";
    if(jumpId) wrap.id=jumpId;
    let sortKey=opts.defaultSort||"count";
    let sortAsc=!!opts.defaultAsc;
    let showAll=!initialLimit || rows.length<=initialLimit;
    function daysSince(dateStr){
      if(!dateStr) return null;
      const parts=String(dateStr).slice(0,10).split("-").map(Number);
      if(parts.length<3||parts.some(n=>!Number.isFinite(n))) return null;
      const d=new Date(parts[0], parts[1]-1, parts[2]);
      const now=new Date();
      const today=new Date(now.getFullYear(), now.getMonth(), now.getDate());
      return Math.round((today-d)/86400000);
    }
    function cellVal(row, key){
      if(key==="daysSinceLast") return daysSince(row.last);
      if(key==="daysSinceFirst") return daysSince(row.first);
      return row[key];
    }
    function paint(){
      const sorted=rows.slice().sort((a,b)=>{
        let av=cellVal(a, sortKey), bv=cellVal(b, sortKey);
        if(av==null) av=sortAsc?Infinity:-Infinity;
        if(bv==null) bv=sortAsc?Infinity:-Infinity;
        if(typeof av==="string"||typeof bv==="string"){
          const c=String(av).localeCompare(String(bv),"de",{numeric:true});
          return sortAsc?c:-c;
        }
        return sortAsc?av-bv:bv-av;
      });
      const data=(!showAll && initialLimit)?sorted.slice(0, initialLimit):sorted;
      const remaining=sorted.length-data.length;
      const head=cols.map(c=>{
        const cls=c.key===sortKey?(sortAsc?"sort-asc":"sort-desc"):"";
        return `<th class="${c.lbl||c.line?"lbl ":""}${cls}" data-k="${c.key}">${esc(c.label)}</th>`;
      }).join("");
      const body=data.map(r=>"<tr>"+cols.map(c=>{
        if(c.line) return `<td class="lbl">${lineBadge(cellVal(r,c.key))}</td>`;
        if(c.date) return `<td>${esc(cellVal(r,c.key)||"—")}</td>`;
        if(c.lbl){
          const v=cellVal(r,c.key);
          return `<td class="lbl">${esc(v==null||v===""?"—":v)}</td>`;
        }
        return `<td>${fmtNum(cellVal(r,c.key))}</td>`;
      }).join("")+"</tr>").join("");
      const more=remaining>0
        ? `<button type="button" class="stats-more">Mehr laden (${fmtNum(remaining)} weitere, ${fmtNum(sorted.length)} gesamt)</button>`
        : "";
      wrap.innerHTML=`<h3 style="margin:0 0 8px;font-size:14px">${esc(title)}</h3>
        <p class="hint" style="margin:0 0 10px">${esc(hint||"")}</p>
        <table class="stats"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>${more}`;
      wrap.querySelectorAll("th[data-k]").forEach(th=>{
        th.onclick=()=>{
          const k=th.dataset.k;
          if(sortKey===k) sortAsc=!sortAsc; else { sortKey=k; sortAsc=!!cols.find(c=>c.key===k&&c.date); }
          paint();
        };
      });
      const moreBtn=wrap.querySelector(".stats-more");
      if(moreBtn) moreBtn.onclick=()=>{ showAll=true; paint(); };
    }
    paint();
    if(mountEl){ mountEl.appendChild(wrap); return ""; }
    return wrap.outerHTML;
  }

  const edgeCols=[
    {key:"from", label:"Von", lbl:true},
    {key:"to", label:"Nach", lbl:true},
    {key:"count", label:"×"},
    {key:"first", label:"zuerst", date:true},
    {key:"last", label:"zuletzt", date:true},
    {key:"daysSinceLast", label:"Tage her"},
    {key:"uniqueVehicles", label:"Fz"},
    {key:"uniqueLines", label:"Linien"},
    {key:"uniqueLocClasses", label:"BR"},
  ];
  // Erstbefahrung: „Tage her“ = Tage seit first, nicht seit last.
  const edgeColsFirst=edgeCols.map(c=>
    c.key==="daysSinceLast"?{key:"daysSinceFirst", label:"Tage her"}:c
  );
  const edgesEl=document.getElementById("stats-edges");
  edgesEl.innerHTML=`<h3>Kanten</h3>
    <p class="hint">Gerichtete Zwischenhalt-Segmente. „Tage her“ relativ zum heutigen Datum beim Öffnen der Seite (bei Erstbefahrung: seit zuerst).</p>`;
  const edgeBlocks=[
    ["edgesByCount", "Häufigste Kanten", "Sortiert nach Befahrungen. Zunächst 40 Einträge.", "count", false, edgeCols, "stats-edges-by-count"],
    ["edgesByDaysSince", "Am längsten nicht befahren", "Sortiert nach Tagen seit letzter Befahrung. Zunächst 40 Einträge.", "daysSinceLast", false, edgeCols, "stats-edges-by-days"],
    ["edgesByLast", "Zuletzt befahren", "Sortiert nach Datum zuletzt. Zunächst 40 Einträge.", "last", false, edgeCols, "stats-edges-by-last"],
    ["edgesByFirst", "Älteste Erstbefahrung", "Sortiert nach Datum zuerst. „Tage her“ seit Erstbefahrung. Zunächst 40 Einträge.", "first", true, edgeColsFirst, "stats-edges-by-first"],
    ["edgesByFirstNew", "Jüngste Erstbefahrung", "Sortiert nach Datum zuerst (neueste zuerst). „Tage her“ seit Erstbefahrung. Zunächst 40 Einträge.", "first", false, edgeColsFirst, "stats-edges-by-first-new"],
    ["edgesOnce", "Vergessene Einmal-Kanten", "Nur einmal befahren, sortiert nach Tagen her. Zunächst 40 Einträge.", "daysSinceLast", false, edgeCols, "stats-edges-once"],
  ];
  edgeBlocks.forEach(([key,title,hint,sort,asc,cols,id])=>{
    const holder=document.createElement("div");
    edgesEl.appendChild(holder);
    renderComboTable(holder, title, hint, S[key], cols,
      {defaultSort:sort, defaultAsc:asc, id:id, initialLimit:40});
  });

  const repeatEl=document.getElementById("stats-repeat");
  repeatEl.innerHTML=`<h3>Wiederholungen</h3>
    <p class="hint">Nur Kombinationen mit mehr als einer Befahrung (count &gt; 1).</p>`;
  function appendCombo(title, hint, rows, cols, sortOpts){
    const holder=document.createElement("div");
    repeatEl.appendChild(holder);
    const opts=Object.assign({initialLimit:100}, sortOpts||{});
    renderComboTable(holder, title, hint, rows, cols, opts);
  }
  appendCombo("Fahrzeug × Kante", "Dasselbe Fahrzeug (Baureihe und Nummer) auf derselben Kante mehrfach. Zunächst 100 Einträge.",
    S.vehEdgeGt1, [
      {key:"vehicle", label:"Wagen", lbl:true},
      {key:"locClass", label:"BR", lbl:true},
      {key:"from", label:"Von", lbl:true},
      {key:"to", label:"Nach", lbl:true},
      {key:"count", label:"×"},
      {key:"first", label:"zuerst", date:true},
      {key:"last", label:"zuletzt", date:true},
      {key:"lines", label:"Linien"},
    ], {id:"stats-repeat-veh-edge"});
  appendCombo("Fahrzeug × Kante × Linie", "Dieselbe Kombi Fahrzeug (Baureihe und Nummer) + Segment + Linie mehrfach. Zunächst 100 Einträge.",
    S.vehEdgeLineGt1, [
      {key:"vehicle", label:"Wagen", lbl:true},
      {key:"locClass", label:"BR", lbl:true},
      {key:"from", label:"Von", lbl:true},
      {key:"to", label:"Nach", lbl:true},
      {key:"line", label:"Linie", line:true},
      {key:"count", label:"×"},
      {key:"first", label:"zuerst", date:true},
      {key:"last", label:"zuletzt", date:true},
    ], {id:"stats-repeat-veh-edge-line"});
  appendCombo("Linie × Kante", "Linie wiederholt auf demselben Segment. Zunächst 100 Einträge.",
    S.lineEdgeGt1, [
      {key:"line", label:"Linie", line:true},
      {key:"from", label:"Von", lbl:true},
      {key:"to", label:"Nach", lbl:true},
      {key:"count", label:"×"},
      {key:"first", label:"zuerst", date:true},
      {key:"last", label:"zuletzt", date:true},
    ], {id:"stats-repeat-line-edge"});
  appendCombo("Baureihe × Kante", "Baureihe wiederholt auf demselben Segment. Zunächst 100 Einträge.",
    S.locEdgeGt1, [
      {key:"locClass", label:"Baureihe", lbl:true},
      {key:"from", label:"Von", lbl:true},
      {key:"to", label:"Nach", lbl:true},
      {key:"count", label:"×"},
      {key:"first", label:"zuerst", date:true},
      {key:"last", label:"zuletzt", date:true},
    ], {defaultSort:"count", id:"stats-repeat-loc-edge"});
  appendCombo("Kanten mit mehreren Fahrzeugen", "Segmente mit mehr als einem Fahrzeug (Baureihe und Nummer). Zunächst 100 Einträge.",
    S.multiVehicleEdges, edgeCols, {defaultSort:"uniqueVehicles", id:"stats-repeat-multi-veh"});

  function mountHeatmap(parent, cross, title, rowLabelFn, colLabelFn, jumpId, opts){
    opts=opts||{};
    const initialLimit=opts.initialLimit||0;
    const wrap=document.createElement("div");
    wrap.className="panel stats-scroll stats-jump";
    wrap.style.marginBottom="16px";
    if(jumpId) wrap.id=jumpId;
    if(!cross||!cross.rows||!cross.rows.length||!cross.cols||!cross.cols.length){
      wrap.innerHTML=`<h3 style="margin:0 0 8px;font-size:14px">${esc(title)}</h3>
        <p class="hint" style="margin:0">Keine Daten.</p>`;
      parent.appendChild(wrap);
      return;
    }
    const allRows=cross.rows, cols=cross.cols, counts=cross.counts, kms=cross.kms||[];
    let max=1;
    counts.forEach(r=>r.forEach(v=>{ if(v>max) max=v; }));
    let showAll=!initialLimit || allRows.length<=initialLimit;
    function paint(){
      const nShow=showAll?allRows.length:Math.min(initialLimit, allRows.length);
      const remaining=allRows.length-nShow;
      const colsN=cols.length;
      let html=`<h3 style="margin:0 0 10px;font-size:14px">${esc(title)}</h3>`;
      if(remaining>0){
        html+=`<p class="hint" style="margin:0 0 10px">Zunächst ${fmtNum(initialLimit)} Zeilen.</p>`;
      }
      html+=`<div class="heatmap" style="grid-template-columns:max-content repeat(${colsN},minmax(28px,auto))">`;
      html+=`<div class="hm-corner"></div>`;
      cols.forEach(c=>{
        html+=`<div class="hm-col" title="${esc(colLabelFn(c))}">${esc(colLabelFn(c))}</div>`;
      });
      for(let ri=0; ri<nShow; ri++){
        const r=allRows[ri];
        html+=`<div class="hm-row" title="${esc(rowLabelFn(r))}">${esc(rowLabelFn(r))}</div>`;
        cols.forEach((_c,ci)=>{
          const n=counts[ri][ci]||0;
          const km=(kms[ri]&&kms[ri][ci])||0;
          const col=n?turbo(n/max):null;
          const bg=col?col.bg:"var(--panel2)";
          const fg=col?col.fg:"var(--fg)";
          const titleAttr=`${esc(rowLabelFn(r))} × ${esc(colLabelFn(_c))}: ${n} · ${km} km`;
          html+=`<div class="hm-cell" style="background:${bg};color:${fg}" title="${titleAttr}">${n||""}</div>`;
        });
      }
      html+="</div>";
      if(remaining>0){
        html+=`<button type="button" class="stats-more">Mehr laden (${fmtNum(remaining)} weitere, ${fmtNum(allRows.length)} gesamt)</button>`;
      }
      wrap.innerHTML=html;
      const moreBtn=wrap.querySelector(".stats-more");
      if(moreBtn) moreBtn.onclick=()=>{ showAll=true; paint(); };
    }
    paint();
    parent.appendChild(wrap);
  }
  const id=x=>x;
  // Fahrzeug-Zeile = Nummer + \\x1f + Baureihe; angezeigt wird nur die Nummer.
  const vehLabel=k=>{
    if(k==null||k==="") return "";
    const s=String(k), i=s.indexOf(LINE_SEP);
    return i<0?s:s.slice(0,i);
  };
  const wdLab=k=>{ const i=+k; return (i>=0&&i<7)?WD[i]:k; };
  const catLab=c=>catLabel(c);
  const crossEl=document.getElementById("stats-cross");
  crossEl.innerHTML=`<h3>Kreuztabellen</h3><p class="hint">Zellen = Anzahl Fahrten; Hover zeigt Kilometer.</p>`;
  mountHeatmap(crossEl, S.crossLocClassLine, "Baureihe × Linie", id, lineName, "stats-cross-loc-line");
  mountHeatmap(crossEl, S.crossVehicleLine, "Fahrzeug × Linie", vehLabel, lineName, "stats-cross-veh-line", {initialLimit:50});
  mountHeatmap(crossEl, S.crossLineMonth, "Linie × Monat", lineName, id, "stats-cross-line-month", {initialLimit:50});
  mountHeatmap(crossEl, S.crossLineWeekday, "Linie × Wochentag", lineName, wdLab, "stats-cross-line-weekday", {initialLimit:50});
  mountHeatmap(crossEl, S.crossLocClassMonth, "Baureihe × Monat", id, id, "stats-cross-loc-month");
  mountHeatmap(crossEl, S.crossLocClassWeekday, "Baureihe × Wochentag", id, wdLab, "stats-cross-loc-weekday");
  mountHeatmap(crossEl, S.crossLocClassCategory, "Baureihe × Kategorie", id, catLab, "stats-cross-loc-category");
  mountHeatmap(crossEl, S.crossLineCategory, "Linie × Kategorie", lineName, catLab, "stats-cross-line-category", {initialLimit:50});
  mountHeatmap(crossEl, S.crossLineDelay, "Linie × Verspätung", lineName, id, "stats-cross-line-delay", {initialLimit:50});
  mountHeatmap(crossEl, S.crossLocClassDelay, "Baureihe × Verspätung", id, id, "stats-cross-loc-delay");
  mountHeatmap(crossEl, S.crossVehicleMonth, "Fahrzeug × Monat", vehLabel, id, "stats-cross-veh-month", {initialLimit:50});
  }
  renderStats();
  onHomeChange(renderStats);
})();

