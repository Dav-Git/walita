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
  const modeEl=document.getElementById("mapMode");

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
  let vPaths=[], vEdgeSlots=new Map(), vVehColor=new Map(), vVehCount=0;
  function isVehicleMode(){ return modeEl && modeEl.value==="vehicle"; }
  // Knick zwischen zwei gerichteten Kanten an der gemeinsamen Station.
  // 0 = geradeaus. Längen in lon/lat, nur der Winkel zählt.
  function turnAngle(inE, outE){
    const ix=inE.b[1]-inE.a[1], iy=inE.b[0]-inE.a[0];
    const ox=outE.b[1]-outE.a[1], oy=outE.b[0]-outE.a[0];
    const il=Math.hypot(ix,iy)||1, ol=Math.hypot(ox,oy)||1;
    const dot=(ix/il)*(ox/ol)+(iy/il)*(oy/ol);
    return Math.acos(Math.max(-1, Math.min(1, dot)));
  }
  // Gerichtete Kanten eines Fahrzeugs zu Pfaden verketten. An einer Station
  // gewinnt die Fortsetzung mit dem kleinsten Knick; übrige Äste starten neu.
  function chainPaths(edges){
    edges.forEach(e=>{ e.next=null; e.prev=null; });
    const outs=new Map(), ins=new Map();
    edges.forEach(e=>{
      if(!outs.has(e.aId)) outs.set(e.aId, []);
      outs.get(e.aId).push(e);
      if(!ins.has(e.bId)) ins.set(e.bId, []);
      ins.get(e.bId).push(e);
    });
    const stations=new Set([...outs.keys(), ...ins.keys()]);
    stations.forEach(sid=>{
      const incoming=ins.get(sid)||[], outgoing=outs.get(sid)||[];
      const pairs=[];
      incoming.forEach(inn=>{
        outgoing.forEach(out=>{
          pairs.push({inn, out, ang:turnAngle(inn, out)});
        });
      });
      pairs.sort((p,q)=>p.ang-q.ang || (p.out.bId<q.out.bId?-1:p.out.bId>q.out.bId?1:0));
      const usedIn=new Set(), usedOut=new Set();
      pairs.forEach(p=>{
        if(usedIn.has(p.inn) || usedOut.has(p.out)) return;
        usedIn.add(p.inn);
        usedOut.add(p.out);
        p.inn.next=p.out;
        p.out.prev=p.inn;
      });
    });
    const seen=new Set(), paths=[];
    function walk(start){
      const path=[];
      let cur=start;
      while(cur && !seen.has(cur)){
        seen.add(cur);
        path.push(cur);
        cur=cur.next;
      }
      if(path.length) paths.push(path);
    }
    edges.forEach(e=>{ if(!e.prev) walk(e); });
    edges.forEach(e=>{ if(!seen.has(e)) walk(e); });
    return paths;
  }
  function scale(c){ if(vMax===vMin) return 0.5;
    const t=(Math.log(Math.max(c,1))-Math.log(vMin))/(Math.log(vMax)-Math.log(vMin));
    return Math.max(0, Math.min(1, t)); }
  function computeVisible(){
    const vehicleMode=isVehicleMode();
    const useVeh=vehEl.value!=="__all__";
    const em=new Map();
    if(vehicleMode){
      rawVehEdges.forEach(row=>{
        const a=row[0], b=row[1], vi=row[2], vhi=row[3], cnt=row[4];
        if(a===b || !matchesVar(vi) || !matchesVeh(vhi)) return;
        const sa=ST[a], sb=ST[b];
        if(!sa||!sb) return;
        const key=vhi+"|"+a+"|"+b, cur=em.get(key);
        if(cur) cur.count+=cnt;
        else em.set(key,{
          a:[sa[0],sa[1]], b:[sb[0],sb[1]], aId:a, bId:b,
          from:sa[2], to:sb[2], count:cnt, vehIdx:vhi, key:a+"|"+b,
        });
      });
    } else if(useVeh){
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
    vPaths=[];
    vEdgeSlots=new Map();
    vVehCount=0;
    if(vehicleMode){
      const byVeh=new Map();
      vEdges.forEach(e=>{
        if(!byVeh.has(e.vehIdx)) byVeh.set(e.vehIdx, []);
        byVeh.get(e.vehIdx).push(e);
        let slots=vEdgeSlots.get(e.key);
        if(!slots){ slots=[]; vEdgeSlots.set(e.key, slots); }
        if(slots.indexOf(e.vehIdx)<0) slots.push(e.vehIdx);
      });
      // Nur Mitfahrer dieser Kante, und nur solche, die der aktuelle Filter
      // noch zeigt. Ausgeblendete Fahrzeuge lassen keine leere Spur zurück.
      vEdgeSlots.forEach(list=>list.sort((a,b)=>
        vehSortKey(a).localeCompare(vehSortKey(b), undefined, {numeric:true})));
      vVehCount=byVeh.size;
      // Farbkreis nur über diesen Filter. Die Wagennummer wählt den Ton nicht:
      // wer dieselbe Kante teilt, liegt möglichst weit auseinander.
      assignVehicleColors([...byVeh.keys()]);
      [...byVeh.keys()].sort((a,b)=>
        vehSortKey(a).localeCompare(vehSortKey(b), undefined, {numeric:true}))
        .forEach(idx=>{
          chainPaths(byVeh.get(idx)).forEach(edges=>{
            vPaths.push({vehIdx:idx, edges});
          });
        });
    }
    vDiscEdges=[];
    if(discEl && discEl.checked){
      const riddenKeys=vehicleMode ? new Set(vEdges.map(e=>e.key)) : new Set(em.keys());
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
  // Fahrzeugmodus: feste Strichstärke. Jede Richtung liegt rechts der Mittellinie
  // (Rechtsverkehr), dazwischen eine feste Lücke. Weitere Fahrzeuge derselben
  // Richtung stapeln nach außen, nicht in die Gegenrichtung. Kurvenradius in
  // Pixeln, damit er beim Zoom konstant bleibt.
  const VEH_WEIGHT=3, CURVE_R=16, BADGE_MIN_PX=28, VEH_DIR_GAP=10, BADGE_MAX=3, BADGE_REPEAT=220;
  function hueGap(a, b){
    const d=Math.abs(a-b)%360;
    return d>180 ? 360-d : d;
  }
  // Volle Sättigung. Gelb und Grün brauchen mehr Helligkeit, sonst werden sie oliv.
  function vividCss(hue){
    const h=((hue%360)+360)%360;
    const ang=center=>Math.min(Math.abs(h-center), 360-Math.abs(h-center));
    const near=(center, width)=>Math.max(0, 1-ang(center)/width);
    const y=near(52, 42), g=near(122, 48);
    const L=Math.round(43+14*Math.max(y, g*0.45));
    const S=Math.round(86+14*Math.max(y, g*0.7));
    return "hsl("+Math.round(h)+","+S+"%,"+L+"%)";
  }
  function badgeInk(col){
    const m=/hsl\(\s*([\d.]+),\s*([\d.]+)%,\s*([\d.]+)%/.exec(col||"");
    if(!m) return "#fff";
    const H=+m[1], S=+m[2]/100, L=+m[3]/100;
    const a=S*Math.min(L, 1-L);
    const f=n=>{
      const k=(n+H/30)%12;
      return L-a*Math.max(-1, Math.min(k-3, 9-k, 1));
    };
    const lin=c=>c<=0.04045 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4);
    const Y=0.2126*lin(f(0))+0.7152*lin(f(8))+0.0722*lin(f(4));
    return Y>0.45 ? "#1c1917" : "#fff";
  }
  // Gleichmäßige Palette für genau die Fahrzeuge des aktuellen Filters.
  // Zuteilung nach gemeinsamen Kanten, nicht nach Baureihe oder Nummer.
  function assignVehicleColors(ids){
    vVehColor=new Map();
    const n=ids.length;
    if(!n) return;
    if(n===1){
      vVehColor.set(ids[0], vividCss(210));
      return;
    }
    const palette=[];
    // Bei Gelb anfangen, damit der Kreis Gelb und Grün nicht auslässt.
    for(let i=0;i<n;i++) palette.push((52+Math.round(i*360/n))%360);
    const conflicts=new Map();
    ids.forEach(idx=>conflicts.set(idx, new Set()));
    vEdgeSlots.forEach(list=>{
      for(let i=0;i<list.length;i++){
        for(let j=i+1;j<list.length;j++){
          const a=list[i], b=list[j];
          if(!conflicts.has(a) || !conflicts.has(b)) continue;
          conflicts.get(a).add(b);
          conflicts.get(b).add(a);
        }
      }
    });
    const hueOf=new Map();
    const used=new Array(n).fill(false);
    const order=ids.slice().sort((a,b)=>conflicts.get(b).size-conflicts.get(a).size);
    order.forEach(idx=>{
      const mates=conflicts.get(idx);
      let bestSlot=0, bestScore=-1;
      for(let si=0; si<n; si++){
        if(used[si]) continue;
        const hue=palette[si];
        let score=360, saw=false;
        mates.forEach(other=>{
          const prev=hueOf.get(other);
          if(prev==null) return;
          saw=true;
          const gap=hueGap(hue, prev);
          if(gap<score) score=gap;
        });
        if(!saw){
          hueOf.forEach(prev=>{
            const gap=hueGap(hue, prev);
            if(gap<score) score=gap;
          });
        }
        if(score>bestScore){ bestScore=score; bestSlot=si; }
      }
      used[bestSlot]=true;
      hueOf.set(idx, palette[bestSlot]);
    });
    hueOf.forEach((hue, idx)=>vVehColor.set(idx, vividCss(hue)));
  }
  function vehicleColor(idx){
    return vVehColor.get(idx) || "#64748b";
  }
  function vehicleBadgeText(idx){
    const v=VEH[idx];
    return (v && v[1]) ? v[1] : "–";
  }
  function vehSortKey(idx){
    const v=VEH[idx]||["",""];
    return (v[0]||"")+" "+(v[1]||"");
  }
  function offsetGeom(e, slot){
    const pa=px(e.a), pb=px(e.b);
    const dx=pb.x-pa.x, dy=pb.y-pa.y;
    const len=Math.hypot(dx,dy)||1;
    const ux=dx/len, uy=dy/len;
    const nx=-uy, ny=ux;
    const step=VEH_WEIGHT+2;
    // slot 0 innen an der Lücke, höhere Slots weiter rechts.
    const offMag=VEH_DIR_GAP/2 + VEH_WEIGHT/2 + slot*step;
    const off=L.point(nx*offMag, ny*offMag);
    return {oa:pa.add(off), ob:pb.add(off), ux, uy, nx, ny, len};
  }
  function lineIntersect(p, ux, uy, q, vx, vy){
    const det=ux*vy-uy*vx;
    if(Math.abs(det)<1e-6) return null;
    const dx=q.x-p.x, dy=q.y-p.y;
    const t=(dx*vy-dy*vx)/det;
    const s=(dx*uy-dy*ux)/det;
    return {x:p.x+ux*t, y:p.y+uy*t, t, s};
  }
  function sampleQuad(p0, c, p1, steps){
    const pts=[];
    for(let i=0;i<=steps;i++){
      const t=i/steps, u=1-t;
      pts.push(L.point(
        u*u*p0.x + 2*u*t*c.x + t*t*p1.x,
        u*u*p0.y + 2*u*t*c.y + t*t*p1.y));
    }
    return pts;
  }
  function curvePoints(g0, g1, r){
    const p0=L.point(g0.ob.x-g0.ux*r, g0.ob.y-g0.uy*r);
    const p2=L.point(g1.oa.x+g1.ux*r, g1.oa.y+g1.uy*r);
    const hit=lineIntersect(p0, g0.ux, g0.uy, p2, g1.ux, g1.uy);
    let c;
    if(!hit || hit.t<=0 || hit.s>=0 || Math.hypot(hit.x-p0.x, hit.y-p0.y)>r*4){
      c=L.point(
        (p0.x+g0.ux*r + p2.x-g1.ux*r)/2,
        (p0.y+g0.uy*r + p2.y-g1.uy*r)/2);
    } else c=L.point(hit.x, hit.y);
    return sampleQuad(p0, c, p2, 8);
  }
  function slotOf(key, vehIdx){
    const list=vEdgeSlots.get(key)||[vehIdx];
    const slot=list.indexOf(vehIdx);
    return slot<0?0:slot;
  }
  function pathPopup(path, edge){
    const v=VEH[path.vehIdx]||["",""];
    const num=v[1]||"–";
    const loc=v[0] ? ("BR "+esc(v[0])+"<br>") : "";
    const route=esc(path.edges[0].from)+" → "+path.edges.map(e=>esc(e.to)).join(" → ");
    const leg=esc(edge.from)+" → "+esc(edge.to);
    return "<b>"+esc(num)+"</b><br>"+loc+route+"<br>"+leg+": "+edge.count+"× befahren";
  }
  function addArrow(g, col, weight, fromPt, toPt){
    const m=L.point((fromPt.x+toPt.x)/2, (fromPt.y+toPt.y)/2);
    const tip=L.point(m.x+g.ux*ARROW, m.y+g.uy*ARROW);
    const wingL=L.point(tip.x-g.ux*ARROW+g.nx*ARROW*0.6, tip.y-g.uy*ARROW+g.ny*ARROW*0.6);
    const wingR=L.point(tip.x-g.ux*ARROW-g.nx*ARROW*0.6, tip.y-g.uy*ARROW-g.ny*ARROW*0.6);
    L.polyline([ll(wingL),ll(tip),ll(wingR)],
      {color:col, weight:weight, opacity:.9, interactive:false}).addTo(overlay);
  }
  function addBadge(pt, text, col, vehIdx){
    const icon=L.divIcon({
      className:"map-veh-badge",
      html:'<span data-veh="'+vehIdx+'" style="background:'+col+';color:'+badgeInk(col)+'">'+esc(text)+"</span>",
      iconSize:[0,0],
      iconAnchor:[0,0],
    });
    L.marker(ll(pt), {icon, interactive:false, keyboard:false}).addTo(overlay);
  }
  let badgeMeasure=null;
  function badgeBox(text){
    if(!badgeMeasure){
      badgeMeasure=document.createElement("canvas").getContext("2d");
      badgeMeasure.font="700 10px system-ui, Segoe UI, Roboto, sans-serif";
    }
    // Polster enthält den Abstand, den zwei Badges mindestens frei lassen.
    return {w:Math.ceil(badgeMeasure.measureText(text).width)+12, h:18};
  }
  function badgeHits(grid, box){
    const cell=64;
    const x0=Math.floor((box.x-box.w/2)/cell), x1=Math.floor((box.x+box.w/2)/cell);
    const y0=Math.floor((box.y-box.h/2)/cell), y1=Math.floor((box.y+box.h/2)/cell);
    const seen=new Set();
    for(let ix=x0; ix<=x1; ix++){
      for(let iy=y0; iy<=y1; iy++){
        const list=grid.get(ix+","+iy);
        if(!list) continue;
        for(let k=0;k<list.length;k++){
          const o=list[k];
          if(seen.has(o)) continue;
          seen.add(o);
          if(Math.abs(box.x-o.x)<(box.w+o.w)/2 && Math.abs(box.y-o.y)<(box.h+o.h)/2)
            return true;
        }
      }
    }
    return false;
  }
  function badgeKeep(grid, box){
    const cell=64;
    const x0=Math.floor((box.x-box.w/2)/cell), x1=Math.floor((box.x+box.w/2)/cell);
    const y0=Math.floor((box.y-box.h/2)/cell), y1=Math.floor((box.y+box.h/2)/cell);
    for(let ix=x0; ix<=x1; ix++){
      for(let iy=y0; iy<=y1; iy++){
        const key=ix+","+iy;
        let list=grid.get(key);
        if(!list){ list=[]; grid.set(key, list); }
        list.push(box);
      }
    }
  }
  // Sichtbarer Ausschnitt in Layer-Pixeln. Badges außerhalb zählen nicht.
  function clipToView(a, b){
    const origin=map.containerPointToLayerPoint(L.point(0, 0));
    const size=map.getSize();
    const maxX=origin.x+size.x, maxY=origin.y+size.y;
    let t0=0, t1=1;
    const dx=b.x-a.x, dy=b.y-a.y;
    function clip(p, q){
      if(Math.abs(p)<1e-8) return q>=0;
      const t=q/p;
      if(p<0){ if(t>t1) return false; if(t>t0) t0=t; }
      else { if(t<t0) return false; if(t<t1) t1=t; }
      return true;
    }
    if(!clip(-dx, a.x-origin.x) || !clip(dx, maxX-a.x) || !clip(-dy, a.y-origin.y) || !clip(dy, maxY-a.y))
      return null;
    const ax=a.x+dx*t0, ay=a.y+dy*t0;
    const bx=a.x+dx*t1, by=a.y+dy*t1;
    const len=Math.hypot(bx-ax, by-ay);
    if(len<1) return null;
    return {a:L.point(ax,ay), b:L.point(bx,by), ux:(bx-ax)/len, uy:(by-ay)/len, len};
  }
  function inView(x, y){
    const origin=map.containerPointToLayerPoint(L.point(0, 0));
    const size=map.getSize();
    return x>=origin.x && y>=origin.y && x<=origin.x+size.x && y<=origin.y+size.y;
  }
  // Alle Kandidaten im festen Abstand entlang des Pfads, danach nur die im Ausschnitt.
  function badgeCandidates(segs){
    const pts=[];
    let travel=0, nextAt=BADGE_REPEAT/2;
    segs.forEach(seg=>{
      const start=travel;
      travel+=seg.len;
      while(nextAt<=travel+1e-6){
        const at=nextAt-start;
        if(at>=0 && at<=seg.len){
          const x=seg.a.x+seg.ux*at, y=seg.a.y+seg.uy*at;
          if(inView(x, y)) pts.push({x, y});
        }
        nextAt+=BADGE_REPEAT;
      }
    });
    return pts;
  }
  // Nächster Punkt, der von den schon gewählten am weitesten entfernt ist.
  function farthestBadge(pool, chosen){
    if(!chosen.length){
      const cx=pool.reduce((s,p)=>s+p.x,0)/pool.length;
      const cy=pool.reduce((s,p)=>s+p.y,0)/pool.length;
      let best=pool[0], bd=-1;
      pool.forEach(p=>{
        const d=(p.x-cx)**2+(p.y-cy)**2;
        if(d>bd){ bd=d; best=p; }
      });
      return best;
    }
    let best=null, bd=-1;
    pool.forEach(p=>{
      let md=Infinity;
      chosen.forEach(c=>{
        const d=(p.x-c.x)**2+(p.y-c.y)**2;
        if(d<md) md=d;
      });
      if(md>bd){ bd=md; best=p; }
    });
    return best;
  }
  function drawVehiclePaths(){
    const badgeQueue=[];
    vPaths.forEach(path=>{
      const col=vehicleColor(path.vehIdx);
      const geoms=path.edges.map(e=>{
        return offsetGeom(e, slotOf(e.key, path.vehIdx));
      });
      const n=geoms.length;
      const trim=geoms.map((g,i)=>{
        let r0=0, r1=0;
        if(i>0) r0=Math.min(CURVE_R, geoms[i-1].len*0.45, g.len*0.45);
        if(i<n-1) r1=Math.min(CURVE_R, g.len*0.45, geoms[i+1].len*0.45);
        return {r0, r1};
      });
      const segs=[];
      path.edges.forEach((e,i)=>{
        const g=geoms[i], r0=trim[i].r0, r1=trim[i].r1;
        const a=L.point(g.oa.x+g.ux*r0, g.oa.y+g.uy*r0);
        const b=L.point(g.ob.x-g.ux*r1, g.ob.y-g.uy*r1);
        const straight=Math.hypot(b.x-a.x, b.y-a.y);
        if(straight>=1){
          L.polyline([ll(a),ll(b)],{
            color:col, weight:VEH_WEIGHT, opacity:.9, lineCap:"round", smoothFactor:0
          }).bindPopup(pathPopup(path, e)).addTo(overlay);
          if(straight>=8) addArrow(g, col, 2, a, b);
          segs.push({a, b, ux:g.ux, uy:g.uy, len:straight});
        }
        if(i<n-1 && r1>=1){
          const pts=curvePoints(g, geoms[i+1], r1).map(ll);
          const curve=L.polyline(pts,{
            color:col, weight:VEH_WEIGHT, opacity:.9, lineCap:"round", smoothFactor:0,
            interactive:straight<1,
          });
          if(straight<1) curve.bindPopup(pathPopup(path, e));
          curve.addTo(overlay);
        }
      });
      if(segs.length){
        let longest=0;
        segs.forEach(s=>{ if(s.len>longest) longest=s.len; });
        badgeQueue.push({vehIdx:path.vehIdx, text:vehicleBadgeText(path.vehIdx), col, segs, longest});
      }
    });
    const byVeh=new Map();
    badgeQueue.forEach(item=>{
      let g=byVeh.get(item.vehIdx);
      if(!g){ g={text:item.text, col:item.col, vehIdx:item.vehIdx, segs:[], pool:[]}; byVeh.set(item.vehIdx, g); }
      item.segs.forEach(seg=>g.segs.push(seg));
      badgeCandidates(item.segs).forEach(p=>g.pool.push(p));
    });
    const grid=new Map();
    byVeh.forEach(g=>{
      const size=badgeBox(g.text);
      const pool=g.pool;
      if(!pool.length){
        g.segs.forEach(seg=>{
          const c=clipToView(seg.a, seg.b);
          if(!c || c.len<BADGE_MIN_PX) return;
          pool.push({x:(c.a.x+c.b.x)/2, y:(c.a.y+c.b.y)/2});
        });
      }
      const open=pool.slice();
      const shown=[];
      while(shown.length<BADGE_MAX && open.length){
        const pick=farthestBadge(open, shown);
        open.splice(open.indexOf(pick), 1);
        const box={x:pick.x, y:pick.y, w:size.w, h:size.h};
        if(badgeHits(grid, box)) continue;
        badgeKeep(grid, box);
        addBadge(L.point(pick.x, pick.y), g.text, g.col, g.vehIdx);
        shown.push(pick);
      }
    });
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
    if(isVehicleMode()) drawVehiclePaths();
    else vEdges.forEach(e=>{
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
      const col=isVehicleMode()
        ? (used?"#1e293b":"#3d5a80")
        : (used?color(scale(n.usedCount)):"#3d5a80");
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
    const roles=`<br>
      <i style="background:#fff;width:10px;height:10px;border-radius:50%;border:1.5px solid #000;box-sizing:border-box"></i> gehalten<br>
      <i style="background:#3d5a80;width:8px;height:8px;border-radius:50%;opacity:.75"></i> nur physische Durchfahrt`;
    let html;
    if(isVehicleMode()){
      html=`<b>Fahrzeuge</b><br>
        <span class="muted">Farbe = Fahrzeug<br>Badge = Nummer<br>Pfeil = Fahrtrichtung<br>großer Punkt = Ein-/Ausstieg</span>`+roles;
    } else {
      html=`<b>Befahrungen</b><br>
        <i style="background:${color(0)}"></i> selten (${vMin}×)<br>
        <i style="background:${color(.5)}"></i> mittel<br>
        <i style="background:${color(1)}"></i> häufig (${vMax}×)<br>
        <span class="muted">Pfeil = Fahrtrichtung<br>großer Punkt = Ein-/Ausstieg</span>`+roles;
    }
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
    const disc=(discEl && discEl.checked) ? " · "+vDiscEdges.length+" entdeckt" : "";
    countEl.textContent = isVehicleMode()
      ? vVehCount+" Fahrzeuge · "+vEdges.length+" Segmente"+disc
      : vEdges.length+" Segmente"+disc;
  }
  applyScope();
  drawAll();
  onHomeChange(()=>{ applyScope(); drawAll(); });
  map.on("moveend", draw);
  [modeEl,lineEl,locEl,vehEl,catEl,opEl,yearEl,dateFromEl,dateToEl,discEl].forEach(el=>{
    if(!el) return;
    el.onchange=drawAll;
    if(el===dateFromEl||el===dateToEl) el.oninput=drawAll;
  });

  const mapEl=map.getContainer();
  const FS_ENTER='<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M4 9V4h5v2H6v3H4zm11-5h5v5h-2V6h-3V4zM4 15h2v3h3v2H4v-5zm13 3v-3h2v5h-5v-2h3z"/></svg>';
  const FS_EXIT='<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M9 4H7v3H4v2h5V4zm8 0h-2v5h5V7h-3V4zM4 15v2h3v3h2v-5H4zm11 2h5v-2h-5v5h2v-3z"/></svg>';
  const fsCtrl=L.control({position:"topleft"});
  let fsLink=null;
  fsCtrl.onAdd=function(){
    const wrap=L.DomUtil.create("div", "leaflet-bar leaflet-control leaflet-control-fullscreen");
    fsLink=L.DomUtil.create("a", "map-fs-btn", wrap);
    fsLink.href="#";
    fsLink.role="button";
    fsLink.innerHTML=FS_ENTER;
    L.DomEvent.disableClickPropagation(wrap);
    L.DomEvent.disableScrollPropagation(wrap);
    L.DomEvent.on(fsLink, "click", function(e){
      L.DomEvent.preventDefault(e);
      L.DomEvent.stop(e);
      if(mapFullscreenOn()) exitMapFullscreen();
      else enterMapFullscreen();
    });
    return wrap;
  };
  fsCtrl.addTo(map);
  function fsElement(){
    return document.fullscreenElement || document.webkitFullscreenElement || null;
  }
  function mapFullscreenOn(){
    return fsElement()===mapEl || mapEl.classList.contains("is-fullscreen");
  }
  function syncFsBtn(){
    if(!fsLink) return;
    const on=mapFullscreenOn();
    fsLink.title=on?"Vollbild beenden":"Vollbild";
    fsLink.setAttribute("aria-label", fsLink.title);
    fsLink.setAttribute("aria-pressed", on?"true":"false");
    fsLink.innerHTML=on?FS_EXIT:FS_ENTER;
  }
  function resizeMap(){
    setTimeout(()=>{ map.invalidateSize(); draw(); }, 60);
  }
  function enterMapFullscreen(){
    const req=mapEl.requestFullscreen || mapEl.webkitRequestFullscreen;
    if(!req){
      mapEl.classList.add("is-fullscreen");
      syncFsBtn();
      resizeMap();
      return;
    }
    const pending=req.call(mapEl);
    if(pending && pending.catch) pending.catch(()=>{
      mapEl.classList.add("is-fullscreen");
      syncFsBtn();
      resizeMap();
    });
  }
  function exitMapFullscreen(){
    if(mapEl.classList.contains("is-fullscreen")){
      mapEl.classList.remove("is-fullscreen");
      syncFsBtn();
      resizeMap();
      return;
    }
    const exit=document.exitFullscreen || document.webkitExitFullscreen;
    if(fsElement()===mapEl && exit) exit.call(document);
  }
  syncFsBtn();
  document.addEventListener("fullscreenchange", ()=>{ syncFsBtn(); resizeMap(); });
  document.addEventListener("webkitfullscreenchange", ()=>{ syncFsBtn(); resizeMap(); });
  document.addEventListener("keydown", e=>{
    if(e.key==="Escape" && mapEl.classList.contains("is-fullscreen")) exitMapFullscreen();
  });
  document.querySelectorAll(".navlist [data-tab]").forEach(b=>{
    b.addEventListener("click", ()=>{
      if(b.dataset.tab!=="map") exitMapFullscreen();
    });
  });
})();

