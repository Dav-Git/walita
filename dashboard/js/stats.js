// ---------- Statistiken (Linie / Baureihe / Fahrzeug / Station) ----------
// Neuaufbau für den Von/Bis-Filter. Leerer Bereich nutzt das voraggregierte DATA.stats.
const STATS_WEEKDAYS=["0","1","2","3","4","5","6"];
const STATS_DELAYS=["früh","0–5","6–15","16–30","31–60",">60","unbekannt"];

function statsRound1(n){
  // Entspricht Pythons round(n, 1): exakter Float-Wert, dann Halbe-auf-Gerade.
  if(!Number.isFinite(n)) return n;
  if(n===0) return 0;
  const buf=new ArrayBuffer(8);
  const view=new DataView(buf);
  view.setFloat64(0, n);
  const hi=BigInt(view.getUint32(0));
  const lo=BigInt(view.getUint32(4));
  const bits=(hi<<32n)|lo;
  const sign=(bits>>63n)?-1n:1n;
  let exp=Number((bits>>52n)&0x7ffn);
  let mant=bits&((1n<<52n)-1n);
  if(exp===0x7ff) return n;
  if(exp===0){
    exp=-1022-52;
  }else{
    mant|=1n<<52n;
    exp=exp-1023-52;
  }
  const num=mant*5n;
  const e=exp+1;
  let rounded;
  if(e>=0){
    rounded=num<<BigInt(e);
  }else{
    const den=1n<<BigInt(-e);
    const quot=num/den;
    const rem=num%den;
    const half=den>>1n;
    rounded=quot;
    if(rem>half || (rem===half && (quot&1n))) rounded=quot+1n;
  }
  const signed=sign<0n?-rounded:rounded;
  return Number(signed)/10;
}
function statsYmd(t){ return (t&&t.date||"").slice(0,10); }
function statsWeekday(ymd){
  if(!ymd||ymd.length<10) return null;
  const y=+ymd.slice(0,4), m=+ymd.slice(5,7), d=+ymd.slice(8,10);
  if(!y||!m||!d) return null;
  const dt=new Date(y, m-1, d);
  if(dt.getFullYear()!==y||dt.getMonth()!==m-1||dt.getDate()!==d) return null;
  return (dt.getDay()+6)%7;
}
function statsDelayBucket(delay){
  if(delay==null||delay==="") return "unbekannt";
  if(delay<0) return "früh";
  if(delay<=5) return "0–5";
  if(delay<=15) return "6–15";
  if(delay<=30) return "16–30";
  if(delay<=60) return "31–60";
  return ">60";
}
function statsVehKey(loc, num){
  return (num&&loc)?(num+LINE_SEP+loc):(num||loc||"");
}
function statsCmp(a,b){ return a<b?-1:a>b?1:0; }
function statsCmpDesc(a,b){ return statsCmp(b,a); }

// Die sechs Kantenlisten der Statistik aus einer Zeilenliste (stats.edgeRows
// bzw. aggregateStats). Sortierung stabil: bei Gleichstand entscheidet die
// Eingangsreihenfolge.
function edgeLists(rows){
  const sortEdges=(list, cmp)=>list.slice().sort(cmp);
  return {
    edgesByCount:sortEdges(rows, (a,b)=>{
      if(a.count!==b.count) return b.count-a.count;
      const c=statsCmp(a.from||"", b.from||"");
      if(c) return c;
      return statsCmp(a.to||"", b.to||"");
    }),
    edgesByDaysSince:sortEdges(rows, (a,b)=>{
      const c=statsCmp(a.last||"9999", b.last||"9999");
      if(c) return c;
      if(a.count!==b.count) return b.count-a.count;
      return statsCmp(a.from||"", b.from||"");
    }),
    edgesByLast:sortEdges(rows, (a,b)=>{
      const c=statsCmpDesc(a.last||"", b.last||"");
      if(c) return c;
      return statsCmpDesc(a.from||"", b.from||"");
    }),
    edgesByFirst:sortEdges(rows, (a,b)=>{
      const c=statsCmp(a.first||"9999", b.first||"9999");
      if(c) return c;
      return statsCmp(a.from||"", b.from||"");
    }),
    edgesByFirstNew:sortEdges(rows, (a,b)=>{
      const c=statsCmpDesc(a.first||"", b.first||"");
      if(c) return c;
      return statsCmpDesc(a.from||"", b.from||"");
    }),
    edgesOnce:sortEdges(rows.filter(r=>r.count===1), (a,b)=>{
      const c=statsCmp(a.last||"9999", b.last||"9999");
      if(c) return c;
      return statsCmp(a.from||"", b.from||"");
    }),
  };
}

function aggregateStats(src, from, to){
  const trips=src.trips||[];
  const allEdges=src.statEdges||[];
  const allStops=src.statStops||[];
  const edgeNames=src.statEdgeNames||{};
  const stopNames=src.statStopNames||{};
  const idxs=[];
  for(let i=0;i<trips.length;i++){
    const day=statsYmd(trips[i]);
    if(from||to){
      if(!day) continue;
      if(from && day<from) continue;
      if(to && day>to) continue;
    }
    idxs.push(i);
  }

  function newEnt(){
    return {
      count:0, km:0, dur:0, points:0,
      delaySum:0, delayN:0, onTime:0,
      first:null, last:null,
      minKm:null, maxKm:null, minRoute:"", maxRoute:"",
      vehicles:new Set(), locClasses:new Set(), lines:new Set(),
      routes:new Set(), weekdays:new Set(), months:new Set(),
    };
  }
  function touchDay(obj, day){
    if(!day) return;
    if(obj.first==null||day<obj.first) obj.first=day;
    if(obj.last==null||day>obj.last) obj.last=day;
  }
  function addEnt(a, o){
    a.count++;
    a.km+=o.km;
    a.dur+=o.dur;
    a.points+=o.points;
    if(o.delay!=null && o.delay!==""){
      a.delaySum+=o.delay;
      a.delayN++;
      if(o.delay<=5) a.onTime++;
    }
    touchDay(a, o.day);
    if(a.minKm==null||o.km<a.minKm){ a.minKm=o.km; a.minRoute=o.route; }
    if(a.maxKm==null||o.km>a.maxKm){ a.maxKm=o.km; a.maxRoute=o.route; }
    if(o.route) a.routes.add(o.route);
    if(o.weekday!=null) a.weekdays.add(String(o.weekday));
    if(o.month) a.months.add(o.month);
    if(o.line) a.lines.add(o.line);
    if(o.loc) a.locClasses.add(o.loc);
    (o.vehicles||[]).forEach(v=>{ if(v[1]) a.vehicles.add((v[0]||"")+LINE_SEP+v[1]); });
  }
  function entRow(a, key, kind){
    const n=a.count||1;
    const row={
      key:key,
      count:a.count,
      distanceKm:statsRound1(a.km),
      durationMin:a.dur,
      points:a.points,
      avgDistanceKm:statsRound1(a.km/n),
      avgDurationMin:statsRound1(a.dur/n),
      first:a.first,
      last:a.last,
      minDistanceKm:a.minKm,
      maxDistanceKm:a.maxKm,
      minRoute:a.minRoute,
      maxRoute:a.maxRoute,
      avgDelay:a.delayN?statsRound1(a.delaySum/a.delayN):null,
      onTimePct:a.delayN?statsRound1(100*a.onTime/a.delayN):null,
      uniqueRoutes:a.routes.size,
      uniqueWeekdays:a.weekdays.size,
      uniqueMonths:a.months.size,
    };
    if(kind==="line"){
      row.uniqueVehicles=a.vehicles.size;
      row.uniqueLocClasses=a.locClasses.size;
    }else if(kind==="locClass"){
      row.uniqueVehicles=a.vehicles.size;
      row.uniqueLines=a.lines.size;
    }else if(kind==="vehicle"){
      row.uniqueLines=a.lines.size;
    }
    return row;
  }
  function finalizeEnt(map, kind){
    const rows=[];
    map.forEach((a,k)=>{
      if(kind==="vehicle"){
        const i=k.indexOf(LINE_SEP);
        const loc=i<0?"":k.slice(0,i);
        const num=i<0?k:k.slice(i+1);
        if(!num) return;
        const row=entRow(a, num, kind);
        row.locClass=loc||"";
        rows.push(row);
      }else if(k){
        rows.push(entRow(a, k, kind));
      }
    });
    rows.sort((a,b)=>{
      if(a.distanceKm!==b.distanceKm) return b.distanceKm-a.distanceKm;
      if(a.count!==b.count) return b.count-a.count;
      const c=statsCmp(String(a.key), String(b.key));
      if(c) return c;
      return statsCmp(String(a.locClass||""), String(b.locClass||""));
    });
    return rows;
  }

  function bump(store, row, col, km){
    if(row==null||col==null||row===""||col==="") return;
    const k=row+"\0"+col;
    let cell=store.get(k);
    if(!cell){
      cell={n:0, km:0, row:row, col:col};
      store.set(k, cell);
    }
    cell.n++;
    cell.km+=km;
  }
  function packCross(store, colOrder){
    const cells=[...store.values()];
    if(!cells.length) return {rows:[], cols:[], counts:[], kms:[]};
    const rowTot=new Map(), colTot=new Map();
    const rowSeen=[], colSeen=[];
    cells.forEach(cell=>{
      if(!rowTot.has(cell.row)){ rowTot.set(cell.row, 0); rowSeen.push(cell.row); }
      if(!colTot.has(cell.col)){ colTot.set(cell.col, 0); colSeen.push(cell.col); }
      rowTot.set(cell.row, rowTot.get(cell.row)+cell.n);
      colTot.set(cell.col, colTot.get(cell.col)+cell.n);
    });
    const byCount=(seen, tot)=>seen.slice().sort((a,b)=>tot.get(b)-tot.get(a));
    const rows=byCount(rowSeen, rowTot);
    let cols;
    if(!colOrder) cols=byCount(colSeen, colTot);
    else {
      const present=new Set(colSeen);
      cols=colOrder.filter(c=>present.has(c));
      const picked=new Set(cols);
      cols=cols.concat(byCount(colSeen, colTot).filter(c=>!picked.has(c)));
    }
    const ri=new Map(rows.map((r,i)=>[r,i]));
    const ci=new Map(cols.map((c,i)=>[c,i]));
    const counts=rows.map(()=>cols.map(()=>0));
    const kms=rows.map(()=>cols.map(()=>0));
    cells.forEach(cell=>{
      const r=ri.get(cell.row), c=ci.get(cell.col);
      if(r==null||c==null) return;
      counts[r][c]=cell.n;
      kms[r][c]=statsRound1(cell.km);
    });
    return {rows:rows, cols:cols, counts:counts, kms:kms};
  }

  function newCombo(){ return {count:0, first:null, last:null, lines:new Set()}; }
  function addCombo(c, day, line){
    c.count++;
    touchDay(c, day);
    if(line) c.lines.add(line);
  }
  function edgeLabel(a, b){
    const lab=edgeNames[String(a)+LINE_SEP+String(b)];
    if(lab) return lab;
    return [stopNames[String(a)]||String(a), stopNames[String(b)]||String(b)];
  }

  const lineAggs=new Map(), locAggs=new Map(), vehAggs=new Map();
  const crosses={
    crossLocClassLine:new Map(), crossVehicleLine:new Map(),
    crossLineMonth:new Map(), crossLineWeekday:new Map(),
    crossLocClassMonth:new Map(), crossLocClassWeekday:new Map(),
    crossLocClassCategory:new Map(), crossLineCategory:new Map(),
    crossLineDelay:new Map(), crossLocClassDelay:new Map(),
    crossVehicleMonth:new Map(),
  };
  const uniqRoutes=new Set();
  const lineVehicle=new Set(), lineLoc=new Set(), vehicleLoc=new Set();
  const edgeAggs=new Map();
  const vehEdge=new Map(), vehEdgeLine=new Map(), lineEdge=new Map(), locEdge=new Map();
  const stationAggs=new Map();
  let tripsWithLoc=0, tripsWithVeh=0, minTrip=null, maxTrip=null;

  idxs.forEach(i=>{
    const t=trips[i];
    const day=statsYmd(t);
    const lk=t.line||"";
    const loc=t.locClass||"";
    const cat=t.category||"";
    const km=Number(t.distanceKm)||0;
    const dur=Number(t.durationMin)||0;
    const points=Number(t.points)||0;
    const delay=(t.delay==null||t.delay==="")?null:Number(t.delay);
    const fromName=t.from||"", toName=t.to||"";
    const route=(fromName||toName)?(fromName+" → "+toName):"";
    const disp=lineName(lk);
    const taggedRoute=disp?(disp+": "+route):route;
    const weekday=day?statsWeekday(day):null;
    const month=day&&day.length>=7?day.slice(0,7):"";
    const wdKey=weekday==null?"":String(weekday);
    const vehs=(t.vehicles||"").trim()
      ? (t.vehicles||"").split(", ").filter(Boolean).map(n=>[loc, n])
      : [];
    if(route) uniqRoutes.add(route);
    const info={line:disp, from:fromName, to:toName, km:km, date:day};
    if(minTrip==null||km<minTrip.km) minTrip=info;
    if(maxTrip==null||km>maxTrip.km) maxTrip=info;
    if(loc) tripsWithLoc++;
    if(vehs.length) tripsWithVeh++;
    const base={km:km, dur:dur, points:points, delay:delay, day:day, weekday:weekday, month:month, vehicles:vehs};
    if(lk){
      if(!lineAggs.has(lk)) lineAggs.set(lk, newEnt());
      addEnt(lineAggs.get(lk), Object.assign({}, base, {route:route, line:"", loc:loc}));
      bump(crosses.crossLineMonth, lk, month, km);
      bump(crosses.crossLineWeekday, lk, wdKey, km);
      bump(crosses.crossLineCategory, lk, cat, km);
      bump(crosses.crossLineDelay, lk, statsDelayBucket(delay), km);
    }
    if(loc){
      if(!locAggs.has(loc)) locAggs.set(loc, newEnt());
      addEnt(locAggs.get(loc), Object.assign({}, base, {route:taggedRoute, line:lk, loc:""}));
      bump(crosses.crossLocClassLine, loc, lk, km);
      bump(crosses.crossLocClassMonth, loc, month, km);
      bump(crosses.crossLocClassWeekday, loc, wdKey, km);
      bump(crosses.crossLocClassCategory, loc, cat, km);
      bump(crosses.crossLocClassDelay, loc, statsDelayBucket(delay), km);
      if(lk) lineLoc.add(lk+LINE_SEP+loc);
    }
    vehs.forEach(pair=>{
      const vloc=pair[0]||"", num=pair[1];
      const vk=vloc+LINE_SEP+num;
      if(!vehAggs.has(vk)) vehAggs.set(vk, newEnt());
      addEnt(vehAggs.get(vk), Object.assign({}, base, {route:taggedRoute, line:lk, loc:"", vehicles:[]}));
      bump(crosses.crossVehicleLine, statsVehKey(vloc, num), lk, km);
      bump(crosses.crossVehicleMonth, statsVehKey(vloc, num), month, km);
      if(lk) lineVehicle.add(lk+"\0"+vloc+"\0"+num);
      vehicleLoc.add(num+"\0"+vloc);
    });

    (allStops[i]||[]).forEach(pair=>{
      const sid=pair[0], bits=pair[1]||0;
      if(!bits) return;
      const key=String(sid);
      let st=stationAggs.get(key);
      if(!st){
        st={
          name:stopNames[key]||key,
          boarded:0, alighted:0, through:0, passed:0,
          first:null, last:null, lines:new Set(),
        };
        stationAggs.set(key, st);
      }
      if(bits&1) st.boarded++;
      if(bits&2) st.alighted++;
      if(bits&4) st.through++;
      if(bits&8) st.passed++;
      touchDay(st, day);
      if(lk) st.lines.add(lk);
    });

    (allEdges[i]||[]).forEach(pair=>{
      const a=pair[0], b=pair[1];
      const ek=String(a)+LINE_SEP+String(b);
      let e=edgeAggs.get(ek);
      if(!e){
        const lab=edgeLabel(a, b);
        e={
          from:lab[0], to:lab[1], count:0, first:null, last:null,
          vehicles:new Set(), lines:new Set(), locs:new Set(),
        };
        edgeAggs.set(ek, e);
      }
      e.count++;
      touchDay(e, day);
      if(lk) e.lines.add(lk);
      if(loc) e.locs.add(loc);
      vehs.forEach(v=>{ if(v[1]) e.vehicles.add((v[0]||"")+LINE_SEP+v[1]); });
      if(lk){
        const lkKey=lk+"\0"+ek;
        if(!lineEdge.has(lkKey)) lineEdge.set(lkKey, newCombo());
        addCombo(lineEdge.get(lkKey), day, "");
      }
      if(loc){
        const locKey=loc+"\0"+ek;
        if(!locEdge.has(locKey)) locEdge.set(locKey, newCombo());
        addCombo(locEdge.get(locKey), day, "");
      }
      vehs.forEach(v=>{
        if(!v[1]) return;
        const vk=(v[0]||"")+LINE_SEP+v[1]+"\0"+ek;
        if(!vehEdge.has(vk)) vehEdge.set(vk, newCombo());
        addCombo(vehEdge.get(vk), day, lk);
        if(lk){
          const vl=vk+"\0"+lk;
          if(!vehEdgeLine.has(vl)) vehEdgeLine.set(vl, newCombo());
          addCombo(vehEdgeLine.get(vl), day, "");
        }
      });
    });
  });

  const byLine=finalizeEnt(lineAggs, "line");
  const byLoc=finalizeEnt(locAggs, "locClass");
  const byVeh=finalizeEnt(vehAggs, "vehicle");
  const monthSet=new Set();
  [crosses.crossLineMonth, crosses.crossLocClassMonth, crosses.crossVehicleMonth].forEach(store=>{
    store.forEach(cell=>{ if(cell.col) monthSet.add(cell.col); });
  });
  const months=[...monthSet].sort();
  const nTrips=idxs.length;
  const denom=nTrips||1;
  const topLine=byLine[0]||null;
  const topVeh=byVeh[0]||null;

  function edgeRow(e){
    return {
      from:e.from, to:e.to, count:e.count, first:e.first, last:e.last,
      uniqueVehicles:e.vehicles.size,
      uniqueLines:e.lines.size,
      uniqueLocClasses:e.locs.size,
    };
  }
  const allEdgeRows=[...edgeAggs.values()].map(edgeRow);
  function sortEdges(rows, cmp){ return rows.slice().sort(cmp); }

  function comboSort(a, b){
    if(a.count!==b.count) return b.count-a.count;
    const c=statsCmp(a.last||"", b.last||"");
    if(c) return c;
    return statsCmp(a.from||"", b.from||"");
  }
  function splitVehEdge(key){
    const cut=key.indexOf("\0");
    const veh=key.slice(0, cut);
    const ek=key.slice(cut+1);
    const vi=veh.indexOf(LINE_SEP);
    const loc=vi<0?"":veh.slice(0, vi);
    const num=vi<0?veh:veh.slice(vi+1);
    const ei=ek.indexOf(LINE_SEP);
    const a=ei<0?ek:ek.slice(0, ei);
    const b=ei<0?ek:ek.slice(ei+1);
    const lab=edgeLabel(a, b);
    return {num:num, loc:loc, from:lab[0], to:lab[1]};
  }
  const vehEdgeGt1=[];
  vehEdge.forEach((c, key)=>{
    if(c.count<=1) return;
    const p=splitVehEdge(key);
    vehEdgeGt1.push({
      vehicle:p.num, locClass:p.loc, from:p.from, to:p.to,
      count:c.count, first:c.first, last:c.last, lines:c.lines.size,
    });
  });
  vehEdgeGt1.sort(comboSort);
  const vehEdgeLineGt1=[];
  vehEdgeLine.forEach((c, key)=>{
    if(c.count<=1) return;
    const cut=key.lastIndexOf("\0");
    const line=key.slice(cut+1);
    const p=splitVehEdge(key.slice(0, cut));
    vehEdgeLineGt1.push({
      vehicle:p.num, locClass:p.loc, from:p.from, to:p.to, line:line,
      count:c.count, first:c.first, last:c.last,
    });
  });
  vehEdgeLineGt1.sort(comboSort);
  const lineEdgeGt1=[];
  lineEdge.forEach((c, key)=>{
    if(c.count<=1) return;
    const cut=key.indexOf("\0");
    const line=key.slice(0, cut);
    const ek=key.slice(cut+1);
    const ei=ek.indexOf(LINE_SEP);
    const lab=edgeLabel(ek.slice(0, ei), ek.slice(ei+1));
    lineEdgeGt1.push({
      line:line, from:lab[0], to:lab[1],
      count:c.count, first:c.first, last:c.last,
    });
  });
  lineEdgeGt1.sort(comboSort);
  const locEdgeGt1=[];
  locEdge.forEach((c, key)=>{
    if(c.count<=1) return;
    const cut=key.indexOf("\0");
    const loc=key.slice(0, cut);
    const ek=key.slice(cut+1);
    const ei=ek.indexOf(LINE_SEP);
    const lab=edgeLabel(ek.slice(0, ei), ek.slice(ei+1));
    locEdgeGt1.push({
      locClass:loc, from:lab[0], to:lab[1],
      count:c.count, first:c.first, last:c.last,
    });
  });
  locEdgeGt1.sort(comboSort);
  const multi=sortEdges(allEdgeRows.filter(r=>r.uniqueVehicles>1), (a,b)=>{
    if(a.uniqueVehicles!==b.uniqueVehicles) return b.uniqueVehicles-a.uniqueVehicles;
    if(a.count!==b.count) return b.count-a.count;
    return statsCmp(a.from||"", b.from||"");
  });

  const byStation=[...stationAggs.values()].map(st=>({
    key:st.name,
    boarded:st.boarded,
    alighted:st.alighted,
    through:st.through,
    passed:st.passed,
    total:st.boarded+st.alighted+st.through+st.passed,
    first:st.first,
    last:st.last,
    uniqueLines:st.lines.size,
  }));
  byStation.sort((a,b)=>{
    if(a.total!==b.total) return b.total-a.total;
    if(a.boarded!==b.boarded) return b.boarded-a.boarded;
    if(a.alighted!==b.alighted) return b.alighted-a.alighted;
    if(a.through!==b.through) return b.through-a.through;
    if(a.passed!==b.passed) return b.passed-a.passed;
    return statsCmp(a.key||"", b.key||"");
  });

  let stationsBoarded=0, stationsAlighted=0, stationsThrough=0, stationsPassed=0;
  stationAggs.forEach(st=>{
    if(st.boarded) stationsBoarded++;
    if(st.alighted) stationsAlighted++;
    if(st.through) stationsThrough++;
    if(st.passed) stationsPassed++;
  });
  const edgesRepeat=allEdgeRows.reduce((n,r)=>n+(r.count>1?1:0), 0);

  return {
    extra:{
      lines:byLine.length,
      locClasses:byLoc.length,
      vehicles:byVeh.length,
      tagLocPct:statsRound1(100*tripsWithLoc/denom),
      tagVehPct:statsRound1(100*tripsWithVeh/denom),
      uniqueLineVehicle:lineVehicle.size,
      uniqueLineLocClass:lineLoc.size,
      uniqueVehicleLocClass:vehicleLoc.size,
      uniqueRoutes:uniqRoutes.size,
      edges:allEdgeRows.length,
      edgesRepeat:edgesRepeat,
      vehEdgeRepeat:vehEdgeGt1.length,
      stationsBoarded:stationsBoarded,
      stationsAlighted:stationsAlighted,
      stationsThrough:stationsThrough,
      stationsPassed:stationsPassed,
      minTrip:minTrip,
      maxTrip:maxTrip,
      topLine:topLine?{key:topLine.key, km:topLine.distanceKm}:null,
      topVehicle:topVeh?{key:topVeh.key, locClass:topVeh.locClass||"", km:topVeh.distanceKm}:null,
    },
    byLine:byLine,
    byLocClass:byLoc,
    byVehicle:byVeh,
    byStation:byStation,
    crossLocClassLine:packCross(crosses.crossLocClassLine),
    crossVehicleLine:packCross(crosses.crossVehicleLine),
    crossLineMonth:packCross(crosses.crossLineMonth, months),
    crossLineWeekday:packCross(crosses.crossLineWeekday, STATS_WEEKDAYS),
    crossLocClassMonth:packCross(crosses.crossLocClassMonth, months),
    crossLocClassWeekday:packCross(crosses.crossLocClassWeekday, STATS_WEEKDAYS),
    crossLocClassCategory:packCross(crosses.crossLocClassCategory),
    crossLineCategory:packCross(crosses.crossLineCategory),
    crossLineDelay:packCross(crosses.crossLineDelay, STATS_DELAYS),
    crossLocClassDelay:packCross(crosses.crossLocClassDelay, STATS_DELAYS),
    crossVehicleMonth:packCross(crosses.crossVehicleMonth, months),
    edgeRows:allEdgeRows,
    vehEdgeGt1:vehEdgeGt1,
    vehEdgeLineGt1:vehEdgeLineGt1,
    lineEdgeGt1:lineEdgeGt1,
    locEdgeGt1:locEdgeGt1,
    multiVehicleEdges:multi,
  };
}

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
  const dateFromEl=document.getElementById("statsDateFrom");
  const dateToEl=document.getElementById("statsDateTo");
  const rangeEl=document.getElementById("statsRange");
  function factsReady(src){
    const n=(src.trips||[]).length;
    return !!(src.statEdges && src.statStops
      && src.statEdges.length===n && src.statStops.length===n);
  }
  function tripsInRange(src, from, to){
    if(!from && !to){
      const k=src.kpis||{};
      return k.count!=null?k.count:(src.trips||[]).length;
    }
    return (src.trips||[]).reduce((n,t)=>{
      const d=statsYmd(t);
      if(!d) return n;
      if(from && d<from) return n;
      if(to && d>to) return n;
      return n+1;
    }, 0);
  }
  function paintStatsRange(src, from, to){
    if(!rangeEl) return;
    let span="gesamter Zeitraum";
    if(from&&to) span=fmtDate(from)+"–"+fmtDate(to);
    else if(from) span="ab "+fmtDate(from);
    else if(to) span="bis "+fmtDate(to);
    const n=tripsInRange(src, from, to);
    rangeEl.textContent=span+" · "+Number(n).toLocaleString("de-DE")+" Fahrten";
  }
  let applyStationSort=function(){};
  document.querySelectorAll("[data-station-sort]").forEach(a=>{
    a.addEventListener("click",()=>{
      const k=a.getAttribute("data-station-sort");
      if(k) applyStationSort(k);
    });
  });
  function renderStats(){
  const src=D();
  const kpis=src.kpis||{};
  bindDateInput(dateFromEl, kpis.first, kpis.last);
  bindDateInput(dateToEl, kpis.first, kpis.last);
  const from=dateFromEl?dateFromEl.value:"";
  const to=dateToEl?dateToEl.value:"";
  paintStatsRange(src, from, to);
  const S=(!from && !to)?src.stats:(factsReady(src)?aggregateStats(src, from, to):null);
  if(!S){
    ["stats-extra","stats-lines","stats-loc","stats-veh","stats-stations",
     "stats-edges","stats-repeat","stats-cross"].forEach(id=>{
      const el=document.getElementById(id);
      if(el) el.innerHTML="";
    });
    document.getElementById("stats-extra").innerHTML=(from||to)
      ? "<p class='hint'>Datumsfilter braucht einen neuen Dashboard-Build.</p>"
      : "<p class='hint'>Keine Statistik-Daten vorhanden.</p>";
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
  const E=edgeLists(S.edgeRows||[]);
  edgeBlocks.forEach(([key,title,hint,sort,asc,cols,id])=>{
    const holder=document.createElement("div");
    edgesEl.appendChild(holder);
    renderComboTable(holder, title, hint, E[key], cols,
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
  [dateFromEl, dateToEl].forEach(el=>{
    if(!el) return;
    el.addEventListener("input", renderStats);
    el.addEventListener("change", renderStats);
  });
  renderStats();
  onHomeChange(renderStats);
})();

