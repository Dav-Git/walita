// ---------- Map ----------
let map=null, mapBounds=[], mapFitted=false;
(function(){
  // Karte mit gültiger Startansicht initialisieren; fitBounds erst beim ersten
  // Anzeigen des Tabs, sonst rechnet Leaflet auf einem 0-Pixel-Container.
  map=L.map("mapview",{preferCanvas:true,maxZoom:19}).setView([51,10],6);
  // Provider-Kette: schlägt einer fehl (DNS/Referer/Block), wird automatisch der
  // nächste versucht. Esri braucht keinen Referer und keinen API-Key.
  // maxZoom (einheitlich 19) erlaubt das Reinzoomen; über maxNativeZoom hinaus
  // werden die letzten verfügbaren Kacheln hochskaliert statt das Zoomen zu sperren.
  const PROVIDERS=[
    {url:"https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
     opts:{maxZoom:19, maxNativeZoom:16, attribution:"&copy; Esri, HERE, Garmin"}},
    {url:"https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
     // OSM verlangt laut Tile-Policy einen identifizierbaren Referer; "unsafe-url"
     // sendet die volle Seiten-URL mit, damit die Kacheln nicht (rate-)geblockt
     // werden. Wirkt nur, wenn das Dashboard über http(s) ausgeliefert wird –
     // bei file:// unterdrücken Browser den Referer generell.
     opts:{maxZoom:19, maxNativeZoom:19, subdomains:"abc",
       referrerPolicy:"unsafe-url", attribution:"&copy; OpenStreetMap"}},
    {url:"https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
     opts:{maxZoom:19, maxNativeZoom:17, subdomains:"abc", attribution:"&copy; OpenTopoMap (CC-BY-SA)"}},
  ];
  let baseLayer=null, tileErrors=0;
  function useProvider(i){
    if(baseLayer) map.removeLayer(baseLayer);
    const p=PROVIDERS[i]; tileErrors=0;
    baseLayer=L.tileLayer(p.url, Object.assign(
      {referrerPolicy:"strict-origin-when-cross-origin"}, p.opts));
    baseLayer.on("tileerror", ()=>{
      if(++tileErrors>6 && i+1<PROVIDERS.length){ useProvider(i+1); }
    });
    baseLayer.addTo(map);
  }
  useProvider(0);

  // Rohdaten: Kanten/Knoten referenzieren einen Variant-Index (Attribut-Kombi
  // aus DATA.variants) und nur Station-IDs; die Koordinaten liegen einmalig in ST.
  // Fahrzeug-Filter nutzt eigene Buckets (vehEdges/vehNodes), damit Mehrfachwagen
  // bei "Alle" nicht doppelt zählen.
  let ST={}, VAR=[], FAM={}, VEH=[];
  let rawEdges=[], rawNodes=[], rawVehEdges=[], rawVehNodes=[], rawDisc=[], rawDiscVeh=[];
  const DISC="#64748b";
  const lineEl=document.getElementById("mapLine");
  const locEl=document.getElementById("mapLoc");
  const vehEl=document.getElementById("mapVehicle");
  const catEl=document.getElementById("mapCat");
  const opEl=document.getElementById("mapOperator");
  const yearEl=document.getElementById("mapYear");
  const dateFromEl=document.getElementById("mapDateFrom");
  const dateToEl=document.getElementById("mapDateTo");
  const discEl=document.getElementById("mapDiscovered");
  const countEl=document.getElementById("mapCount");

  // Farbskala nach Häufigkeit, Turbo-Spektrum Blau -> Rot.
  function color(t){ // t in [0,1]
    const stops=[[48,105,229],[0,193,212],[38,201,111],[173,220,48],
                 [250,214,40],[247,150,32],[224,52,32]];
    const x=t*(stops.length-1), i=Math.min(Math.floor(x),stops.length-2), f=x-i;
    const a=stops[i], b=stops[i+1];
    return `rgb(${Math.round(a[0]+(b[0]-a[0])*f)},${Math.round(a[1]+(b[1]-a[1])*f)},${Math.round(a[2]+(b[2]-a[2])*f)})`;
  }

  // Filter-Dropdowns aus den Varianten befüllen (nach Label sortiert). `labelFn`
  // erlaubt eine abweichende Anzeige (z.B. Kategorie-Code -> Klartext). Leere
  // Werte (ungetaggte Fahrt) als eigene "(ohne …)"-Option anbieten.
  function fillSelect(el, values, emptyLabel, labelFn, desc){
    const lf = labelFn || (v=>v);
    const set=new Set(values);
    const hasEmpty=emptyLabel && set.has("");
    let nonEmpty=[...set].filter(v=>v!=="")
      .sort((a,b)=>String(lf(a)).localeCompare(String(lf(b)),undefined,{numeric:true}));
    if(desc) nonEmpty.reverse();
    el.innerHTML='<option value="__all__">Alle</option>'
      + nonEmpty.map(v=>`<option value="${esc(v)}">${esc(lf(v))}</option>`).join("")
      + (hasEmpty?`<option value="__none__">${esc(emptyLabel)}</option>`:"");
  }
  function vehLabel(v){
    if(!v) return "";
    const loc=v[0]||"", num=v[1]||"";
    if(loc && num) return loc+" · "+num;
    return num||loc;
  }
  function fillVehicleSelect(){
    const tagged=VEH.map((v,i)=>({i,v})).filter(x=>x.v[0]||x.v[1]);
    tagged.sort((a,b)=>vehLabel(a.v).localeCompare(vehLabel(b.v),undefined,{numeric:true}));
    const hasEmpty=VEH.some(v=>!v[0]&&!v[1]);
    vehEl.innerHTML='<option value="__all__">Alle</option>'
      + tagged.map(x=>`<option value="${x.i}">${esc(vehLabel(x.v))}</option>`).join("")
      + (hasEmpty?'<option value="__none__">(ohne Fahrzeug)</option>':'');
  }
  function fillLocSelect(){
    const set=new Set(VAR.map(v=>v[1]));
    const hasEmpty=set.has("");
    const classes=[...set].filter(v=>v!=="")
      .sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
    const families=Object.keys(FAM)
      .sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
    let html='<option value="__all__">Alle</option>';
    if(families.length){
      html+='<optgroup label="Familien">'+
        families.map(f=>`<option value="__fam__${esc(f)}">${esc(f)}</option>`).join("")+
        '</optgroup>';
    }
    if(classes.length){
      html+='<optgroup label="Baureihen">'+
        classes.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join("")+
        '</optgroup>';
    }
    if(hasEmpty) html+='<option value="__none__">(ohne Baureihe)</option>';
    locEl.innerHTML=html;
  }
  function fillLineSelect(){
    const set=new Set(VAR.map(v=>v[0]));
    const hasEmpty=set.has("");
    const byOp=new Map();
    [...set].filter(v=>v!=="").forEach(k=>{
      const op=lineOperator(k)||"";
      if(!byOp.has(op)) byOp.set(op,[]);
      byOp.get(op).push(k);
    });
    const ops=[...byOp.keys()].sort((a,b)=>{
      if(!a) return 1;
      if(!b) return -1;
      return a.localeCompare(b,"de");
    });
    let html='<option value="__all__">Alle</option>';
    ops.forEach(op=>{
      const lines=byOp.get(op).sort((a,b)=>
        lineName(a).localeCompare(lineName(b),undefined,{numeric:true}));
      const label=op||"(ohne Operator)";
      html+=`<optgroup label="${esc(label)}">`+
        lines.map(k=>`<option value="${esc(k)}">${esc(lineName(k)||"(ohne Linie)")}</option>`).join("")+
        '</optgroup>';
    });
    if(hasEmpty) html+='<option value="__none__">(ohne Linie)</option>';
    lineEl.innerHTML=html;
  }
  function restoreSelect(el, value){
    if(!el) return;
    const ok=value && [...el.options].some(o=>o.value===value);
    el.value=ok?value:"__all__";
  }
  function restoreVehicle(value, label){
    if(!value || value==="__all__"){ vehEl.value="__all__"; return; }
    if(value==="__none__"){ restoreSelect(vehEl, value); return; }
    const opt=[...vehEl.options].find(o=>o.textContent===label && o.value!=="__all__" && o.value!=="__none__");
    vehEl.value=opt?opt.value:"__all__";
  }
  function applyScope(){
    const src=D();
    ST=src.stations||{};
    VAR=src.variants||[];
    FAM=src.locClassFamilies||{};
    VEH=src.mapVehicles||[];
    rawEdges=src.edges||[];
    rawNodes=src.nodes||[];
    rawVehEdges=src.vehEdges||[];
    rawVehNodes=src.vehNodes||[];
    rawDisc=src.discoveredEdges||[];
    rawDiscVeh=src.discoveredVehEdges||[];
    const keep={
      line:lineEl.value, loc:locEl.value, cat:catEl.value, op:opEl.value, year:yearEl.value,
      veh:vehEl.value,
      vehLabel:(vehEl.selectedOptions&&vehEl.selectedOptions[0])?vehEl.selectedOptions[0].textContent:"",
    };
    fillLineSelect();
    fillLocSelect();
    fillVehicleSelect();
    fillSelect(catEl, VAR.map(v=>v[2]), "(ohne Kategorie)", catLabel);
    fillSelect(opEl, VAR.map(v=>v[3]), "(ohne Operator)");
    fillSelect(yearEl, VAR.map(v=>(v[4]||"").slice(0,4)), "(ohne Jahr)", null, true);
    restoreSelect(lineEl, keep.line);
    restoreSelect(locEl, keep.loc);
    restoreSelect(catEl, keep.cat);
    restoreSelect(opEl, keep.op);
    restoreSelect(yearEl, keep.year);
    restoreVehicle(keep.veh, keep.vehLabel);
    const kpis=src.kpis||{};
    bindDateInput(dateFromEl, kpis.first, kpis.last);
    bindDateInput(dateToEl, kpis.first, kpis.last);
  }

  // Einzelnen Select-Wert gegen ein Attribut prüfen (__all__/__none__/Wert).
  function sel(val, actual){
    if(val==="__all__") return true;
    if(val==="__none__") return actual==="";
    return actual===val;
  }
  // Baureihen-Filter: exakter Tag, oder alle Mitglieder einer Familie (__fam__).
  function selLoc(val, actual){
    if(val==="__all__") return true;
    if(val==="__none__") return actual==="";
    if(val.startsWith("__fam__")){
      const members=FAM[val.slice(7)]||[];
      return members.indexOf(actual)>=0;
    }
    return actual===val;
  }
  // Aktuelle Filter-Auswahl auf eine Variante (Attribut-Kombi) anwenden.
  // Index 4 ist das Reisedatum (YYYY-MM-DD). Von/Bis ist inklusive; solange
  // keines von beiden gesetzt ist, filtert das Jahr-Dropdown per Präfix.
  function matchesVar(vi){
    const v=VAR[vi]; if(!v) return false;
    const date=v[4]||"";
    const from=dateFromEl.value, to=dateToEl.value;
    const ranged=!!(from||to);
    const yearOk=ranged ? true : sel(yearEl.value, date.slice(0,4));
    const dateOk=(!from || date>=from) && (!to || date<=to);
    return sel(lineEl.value, v[0]) && selLoc(locEl.value, v[1])
        && sel(catEl.value, v[2]) && sel(opEl.value, v[3]) && yearOk && dateOk;
  }
  function matchesVeh(vehIdx){
    const want=vehEl.value;
    if(want==="__all__") return true;
    const v=VEH[vehIdx]; if(!v) return false;
    if(want==="__none__") return !v[0]&&!v[1];
    return String(vehIdx)===want;
  }

  // Sichtbare Kanten/Knoten aus den passenden Buckets aggregieren. Skala je
  // Ansicht neu aus den sichtbaren Zählwerten (min..max) bestimmen.
  let vEdges=[], vNodes=[], vDiscEdges=[], vMin=1, vMax=1;
  function scale(c){ if(vMax===vMin) return 0.5;
    const t=(Math.log(Math.max(c,1))-Math.log(vMin))/(Math.log(vMax)-Math.log(vMin));
    return Math.max(0, Math.min(1, t)); }
  function computeVisible(){
    const useVeh=vehEl.value!=="__all__";
    const em=new Map();
    if(useVeh){
      rawVehEdges.forEach(row=>{
        const a=row[0], b=row[1], vi=row[2], vhi=row[3], cnt=row[4];
        if(!matchesVar(vi) || !matchesVeh(vhi)) return;
        const sa=ST[a], sb=ST[b];
        if(!sa||!sb) return;
        const key=a+"|"+b, cur=em.get(key);
        if(cur) cur.count+=cnt;
        else em.set(key,{a:[sa[0],sa[1]],b:[sb[0],sb[1]],from:sa[2],to:sb[2],count:cnt});
      });
    } else {
      rawEdges.forEach(row=>{
        const a=row[0], b=row[1];
        if(!matchesVar(row[2])) return;
        const sa=ST[a], sb=ST[b];
        if(!sa||!sb) return;
        const key=a+"|"+b, cur=em.get(key);
        if(cur) cur.count+=row[3];
        else em.set(key,{a:[sa[0],sa[1]],b:[sb[0],sb[1]],from:sa[2],to:sb[2],count:row[3]});
      });
    }
    vEdges=[...em.values()];
    const nm=new Map();
    if(useVeh){
      rawVehNodes.forEach(row=>{
        const sid=row[0], vi=row[1], vhi=row[2], cnt=row[3], used=row[4]||0;
        const held=row[5]||0, passed=row[6]||0;
        if(!matchesVar(vi) || !matchesVeh(vhi)) return;
        const s=ST[sid]; if(!s) return;
        const cur=nm.get(sid);
        if(cur){ cur.count+=cnt; cur.usedCount+=used; cur.heldCount+=held; cur.passCount+=passed; }
        else nm.set(sid,{lat:s[0],lon:s[1],name:s[2],count:cnt,usedCount:used,heldCount:held,passCount:passed});
      });
    } else {
      rawNodes.forEach(row=>{
        const sid=row[0];
        if(!matchesVar(row[1])) return;
        const s=ST[sid]; if(!s) return;
        const cnt=row[2], used=row[3]||0, held=row[4]||0, passed=row[5]||0;
        const cur=nm.get(sid);
        if(cur){ cur.count+=cnt; cur.usedCount+=used; cur.heldCount+=held; cur.passCount+=passed; }
        else nm.set(sid,{lat:s[0],lon:s[1],name:s[2],count:cnt,usedCount:used,heldCount:held,passCount:passed});
      });
    }
    vNodes=[...nm.values()];
    const cs=vEdges.map(e=>e.count);
    vMax=cs.length?Math.max(...cs):1;
    vMin=cs.length?Math.min(...cs):1;
    // Dünne/seltene zuerst, damit dicke/häufige oben liegen.
    vEdges.sort((p,q)=>p.count-q.count);
    vNodes.sort((p,q)=>p.count-q.count || ((p.usedCount||0)-(q.usedCount||0)));
    // Entdeckt nur, wenn die Variante zum Filter passt und die Kante unter
    // genau diesem Filter nicht schon als befahren in em liegt (Linie A
    // befahren / Linie B nur Laufweg → bei B grau, bei „Alle“ Heatmap).
    vDiscEdges=[];
    if(discEl && discEl.checked){
      const riddenKeys=new Set(em.keys());
      const dm=new Map();
      if(useVeh){
        rawDiscVeh.forEach(row=>{
          const a=row[0], b=row[1], vi=row[2], vhi=row[3];
          if(!matchesVar(vi) || !matchesVeh(vhi)) return;
          const key=a+"|"+b;
          if(riddenKeys.has(key)) return;
          const sa=ST[a], sb=ST[b];
          if(!sa||!sb) return;
          if(!dm.has(key))
            dm.set(key,{a:[sa[0],sa[1]],b:[sb[0],sb[1]],from:sa[2],to:sb[2]});
        });
      } else {
        rawDisc.forEach(row=>{
          const a=row[0], b=row[1];
          if(!matchesVar(row[2])) return;
          const key=a+"|"+b;
          if(riddenKeys.has(key)) return;
          const sa=ST[a], sb=ST[b];
          if(!sa||!sb) return;
          if(!dm.has(key))
            dm.set(key,{a:[sa[0],sa[1]],b:[sb[0],sb[1]],from:sa[2],to:sb[2]});
        });
      }
      vDiscEdges=[...dm.values()];
    }
  }
  // Gerichtete Kanten "im Rechtsverkehr": jede Richtung entlang der (nach rechts
  // zeigenden) Segment-Normalen versetzt, plus Richtungspfeil in der Mitte. In
  // Layer-Point-Koordinaten gerechnet und bei jedem Zoom neu gezeichnet, damit der
  // Versatz optisch konstant bleibt.
  const ARROW=7, GAP=1.5;
  // Kanten UND Knoten liegen in EINER LayerGroup im Standard-Canvas (kein eigenes
  // Knoten-Pane): ein zweites Canvas darüber würde als oberste Ebene sämtliche
  // Klicks abfangen, bevor sie das Kanten-Canvas darunter erreichen (Leaflet reicht
  // Klicks nicht von einem Canvas an ein anderes weiter) – dann wäre keine Linie
  // klickbar. Die Knoten werden pro Redraw ZULETZT gezeichnet, liegen dadurch
  // optisch oben und gewinnen den Hit-Test nur punktgenau; Klicks auf die Linie
  // dazwischen treffen die Kante.
  const overlay=L.layerGroup().addTo(map);
  function px(latlng){ return map.latLngToLayerPoint(latlng); }
  function ll(pt){ return map.layerPointToLatLng(pt); }
  function edgeGeom(e, weight){
    const pa=px(e.a), pb=px(e.b);
    const dx=pb.x-pa.x, dy=pb.y-pa.y;
    const len=Math.hypot(dx,dy)||1;
    const ux=dx/len, uy=dy/len;
    const nx=-uy, ny=ux;
    const offMag=weight/2 + GAP;
    const off=L.point(nx*offMag, ny*offMag);
    return {oa:pa.add(off), ob:pb.add(off), ux, uy, nx, ny};
  }
  function draw(){
    overlay.clearLayers();
    vDiscEdges.forEach(e=>{
      const weight=2, g=edgeGeom(e, weight);
      L.polyline([ll(g.oa),ll(g.ob)],{
        color:DISC, weight:weight, opacity:.75, dashArray:"6 6"
      }).bindPopup(`<b>${esc(e.from)} → ${esc(e.to)}</b><br>entdeckt (nicht eingecheckt)`)
        .addTo(overlay);
      const m=L.point((g.oa.x+g.ob.x)/2,(g.oa.y+g.ob.y)/2);
      const tip=L.point(m.x+g.ux*ARROW, m.y+g.uy*ARROW);
      const wingL=L.point(tip.x-g.ux*ARROW+g.nx*ARROW*0.6, tip.y-g.uy*ARROW+g.ny*ARROW*0.6);
      const wingR=L.point(tip.x-g.ux*ARROW-g.nx*ARROW*0.6, tip.y-g.uy*ARROW-g.ny*ARROW*0.6);
      L.polyline([ll(wingL),ll(tip),ll(wingR)],
        {color:DISC,weight:2,opacity:.8,interactive:false}).addTo(overlay);
    });
    vEdges.forEach(e=>{
      const t=scale(e.count), col=color(t), weight=3+t*7;
      const g=edgeGeom(e, weight);
      L.polyline([ll(g.oa),ll(g.ob)],{color:col,weight:weight,opacity:.85})
        .bindPopup(`<b>${esc(e.from)} → ${esc(e.to)}</b><br>${e.count}× befahren`)
        .addTo(overlay);
      // Pfeil-Chevron am Mittelpunkt; interactive:false, damit Klicks zur Linie
      // darunter durchgehen und die Kante klickbar bleibt.
      const m=L.point((g.oa.x+g.ob.x)/2,(g.oa.y+g.ob.y)/2);
      const tip=L.point(m.x+g.ux*ARROW, m.y+g.uy*ARROW);
      const wingL=L.point(tip.x-g.ux*ARROW+g.nx*ARROW*0.6, tip.y-g.uy*ARROW+g.ny*ARROW*0.6);
      const wingR=L.point(tip.x-g.ux*ARROW-g.nx*ARROW*0.6, tip.y-g.uy*ARROW-g.ny*ARROW*0.6);
      L.polyline([ll(wingL),ll(tip),ll(wingR)],
        {color:col,weight:2+t*3,opacity:.9,interactive:false}).addTo(overlay);
    });
    // Knoten ZULETZT -> liegen optisch oben, fangen Klicks aber nur punktgenau ab.
    vNodes.forEach(n=>{
      const used=(n.usedCount||0)>0;
      const held=(n.heldCount||0)>0;
      const passed=(n.passCount||0)>0;
      const heldOnly=held && !used;
      const col=used?color(scale(n.usedCount)):"#3d5a80";
      const parts=[`${n.count}× befahren`];
      if(used) parts.push(`${n.usedCount}× Ein-/Ausstieg`);
      if(held) parts.push(`${n.heldCount}× gehalten`);
      if(passed) parts.push(`${n.passCount}× physische Durchfahrt`);
      L.circleMarker([n.lat,n.lon],{
        radius:used?5:(heldOnly?4:3),
        color:heldOnly?"#000":(used?"#1e293b":"#33475b"),
        weight:heldOnly?1.5:(used?2:1),
        fillColor:heldOnly?"#fff":col,
        fillOpacity:heldOnly?1:(used?.95:.75)})
        .bindPopup(`<b>${esc(n.name)}</b><br>`+parts.join("<br>")).addTo(overlay);
    });
  }

  let legendDiv=null;
  function updateLegend(){
    if(!legendDiv) return;
    let html=`<b>Befahrungen</b><br>
      <i style="background:${color(0)}"></i> selten (${vMin}×)<br>
      <i style="background:${color(.5)}"></i> mittel<br>
      <i style="background:${color(1)}"></i> häufig (${vMax}×)<br>
      <span class="muted">Pfeil = Fahrtrichtung<br>großer Punkt = Ein-/Ausstieg</span><br>
      <i style="background:#fff;width:10px;height:10px;border-radius:50%;border:1.5px solid #000;box-sizing:border-box"></i> gehalten<br>
      <i style="background:#3d5a80;width:8px;height:8px;border-radius:50%;opacity:.75"></i> nur physische Durchfahrt`;
    if(discEl && discEl.checked){
      html+=`<br><i style="background:${DISC}"></i> Entdeckte Kanten`;
    }
    legendDiv.innerHTML=html;
  }
  const lg=L.control({position:"bottomright"});
  lg.onAdd=function(){ legendDiv=L.DomUtil.create("div","legend"); updateLegend(); return legendDiv; };
  lg.addTo(map);

  // Alles neu berechnen und zeichnen (Init + bei Filterwechsel). Der erste
  // fitBounds passiert beim ersten Anzeigen des Tabs (siehe nav-Handler) auf
  // Basis der hier gefüllten mapBounds; die Ansicht bleibt beim Filtern erhalten.
  function drawAll(){
    computeVisible();
    mapBounds=[];
    vEdges.forEach(e=>{ mapBounds.push(e.a,e.b); });
    draw();
    updateLegend();
    countEl.textContent = (discEl && discEl.checked)
      ? vEdges.length+" Segmente · "+vDiscEdges.length+" entdeckt"
      : vEdges.length+" Segmente";
  }
  applyScope();
  drawAll();
  onHomeChange(()=>{ applyScope(); drawAll(); });
  map.on("zoomend", draw);
  [lineEl,locEl,vehEl,catEl,opEl,yearEl,dateFromEl,dateToEl,discEl].forEach(el=>{
    if(!el) return;
    el.onchange=drawAll;
    if(el===dateFromEl||el===dateToEl) el.oninput=drawAll;
  });
})();

