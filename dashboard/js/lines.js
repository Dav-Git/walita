// ---------- Linien-Katalog ----------
(function(){
  const LINES=DATA.lines||[];
  const LC=DATA.lineColors||{};
  const filterEl=document.getElementById("linesFilter");
  const countEl=document.getElementById("linesCount");
  const bodyEl=document.getElementById("linesBody");
  // RYB: Primär → Sekundär → Tertiär → dunklere, dann hellere Stufen.
  const BR_PALETTE=[
    "#dc2626","#ffd200","#2563eb",
    "#ea580c","#16a34a","#7c3aed",
    "#e11d48","#65a30d","#0891b2","#c026d3","#d97706","#4f46e5",
    "#9f1239","#a16207","#1e40af","#9a3412","#166534","#5b21b6",
    "#fb7185","#facc15","#60a5fa","#fb923c","#4ade80","#a78bfa",
  ];
  const openLines=new Set();

  function fmtN(v){
    if(v==null||v==="") return "—";
    return Number(v).toLocaleString("de-DE");
  }
  function badge(key){
    const name=lineName(key);
    const c=LC[key];
    const st=c?` style="background:${c[0]};color:${c[1]}"`:"";
    return `<span class="line-badge"${st}>${esc(name||"(ohne Linie)")}</span>`;
  }
  function locColor(i){
    return BR_PALETTE[i%BR_PALETTE.length];
  }
  function locLabel(name){ return name||"ohne"; }
  function shares(items, total){
    if(!total) return items.map(([k,n])=>({key:k,n:n,pct:0}));
    let used=0;
    return items.map(([k,n],i)=>{
      const raw=100*n/total;
      const pct=i===items.length-1
        ? Math.max(0, Math.round((100-used)*10)/10)
        : Math.round(raw*10)/10;
      used+=pct;
      return {key:k,n:n,pct:pct};
    });
  }

  function visibleGroups(){
    const q=(filterEl&&filterEl.value||"").trim().toLowerCase();
    const rows=LINES.filter(r=>{
      if(!q) return true;
      const name=lineName(r.key).toLowerCase();
      const op=(r.operator||"").toLowerCase();
      return name.includes(q) || op.includes(q);
    });
    const groups=[];
    const byOp=new Map();
    for(const r of rows){
      const op=r.operator||"";
      let g=byOp.get(op);
      if(!g){
        g={operator:op, lines:[], km:0, count:0};
        byOp.set(op,g);
        groups.push(g);
      }
      g.lines.push(r);
      g.km+=r.distanceKm||0;
      g.count+=r.count||0;
    }
    for(const g of groups){
      g.lines.sort((a,b)=>lineName(a.key).localeCompare(lineName(b.key),undefined,{numeric:true}));
    }
    return {rows, groups};
  }
  function baureihen(r){
    const parts=shares(r.locClasses||[], r.count||0);
    if(!parts.length) return "keine Baureihen getaggt";
    return parts.map(p=>{
      const pct=String(p.pct).replace(".",",");
      return locLabel(p.key)+" "+pct+" %";
    }).join("; ");
  }
  function fahrweg(route){
    const names=(route.stops||[]).map(s=>s[1]||"—");
    const path=names.join(" → ");
    return route.loop&&path?path+" ↻":path;
  }
  function activeFilterHeading(){
    const parts=[];
    const q=(filterEl&&filterEl.value||"").trim();
    if(q) parts.push("Suche "+q);
    const label=parts.length ? parts.join(" · ") : "Alle";
    return "Linien · Eingestellte Filter: "+label;
  }
  function linesTsv(groups){
    const headers=["Operator","Linie","Baureihen","Fahrweg"];
    const out=[tsvCell(activeFilterHeading()), headers.join("\t")];
    groups.forEach(g=>{
      const op=g.operator||"(ohne Operator)";
      g.lines.forEach(r=>{
        const base=[op, lineName(r.key)||"(ohne Linie)", baureihen(r)];
        const routes=r.routes||[];
        if(!routes.length){
          out.push(base.concat([""]).map(tsvCell).join("\t"));
          return;
        }
        routes.forEach(route=>{
          out.push(base.concat([fahrweg(route)]).map(tsvCell).join("\t"));
        });
      });
    });
    return out.join("\n");
  }

  function render(){
    const headEl=document.getElementById("linesFilterHead");
    if(headEl) headEl.textContent=activeFilterHeading();
    const {rows, groups}=visibleGroups();
    if(countEl){
      const nL=rows.length, nO=groups.length;
      countEl.textContent=nL
        ? `${fmtN(nL)} ${nL===1?"Linie":"Linien"} · ${fmtN(nO)} ${nO===1?"Operator":"Operatoren"}`
        : (LINES.length?"Keine Treffer.":"Keine Linien.");
    }
    if(!bodyEl) return;
    if(!rows.length){
      bodyEl.innerHTML=`<p class="hint">${esc(LINES.length?"Keine Treffer.":"Keine Linien in den Daten.")}</p>`;
      return;
    }
    bodyEl.innerHTML=groups.map(g=>{
      const opLabel=g.operator||"(ohne Operator)";
      const n=g.lines.length;
      const cards=g.lines.map(r=>lineCard(r)).join("");
      return `<div class="line-op"><h3>${esc(opLabel)} <span class="muted">· ${fmtN(n)} ${n===1?"Linie":"Linien"} · ${fmtN(Math.round(g.km*10)/10)} km · ${fmtN(g.count)} Fahrten</span></h3>${cards}</div>`;
    }).join("");
    bodyEl.querySelectorAll("details.line-card").forEach(el=>{
      el.addEventListener("toggle",()=>{
        const row=LINES[+el.dataset.idx];
        if(!row) return;
        if(el.open) openLines.add(row.key); else openLines.delete(row.key);
      });
    });
  }

  function lineCard(r){
    const total=r.count||0;
    const parts=shares(r.locClasses||[], total);
    const stack=parts.map((p,i)=>{
      if(!p.pct) return "";
      const col=locColor(i);
      const title=`${locLabel(p.key)}: ${fmtN(p.n)} (${p.pct} %)`;
      return `<i style="flex:${p.pct} 0 0;background:${col}" title="${esc(title)}"></i>`;
    }).join("");
    const legend=parts.map((p,i)=>{
      const col=locColor(i);
      return `<span title="${fmtN(p.n)} Fahrten"><i style="background:${col}"></i>${esc(locLabel(p.key))} ${String(p.pct).replace(".",",")} %</span>`;
    }).join("");
    const nR=(r.routes||[]).length;
    const idx=LINES.indexOf(r);
    const isOpen=openLines.has(r.key);
    return `<details class="line-card"${isOpen?" open":""} data-idx="${idx}">
      <summary class="line-card-head">${badge(r.key)}
        <span class="line-card-meta">${fmtN(r.distanceKm)} km · ${fmtN(r.count)} ${r.count===1?"Fahrt":"Fahrten"} · ${fmtN(nR)} ${nR===1?"Laufweg":"Laufwege"}</span>
      </summary>
      <div class="line-card-body">
        <div class="line-stack">${stack||""}</div>
        <div class="line-stack-legend">${legend||'<span>keine Baureihen getaggt</span>'}</div>
        ${pearl(r)}
      </div>
    </details>`;
  }

  function pearl(r){
    const routes=r.routes||[];
    if(!routes.length) return `<p class="hint">Kein Laufweg.</p>`;
    const c=LC[r.key];
    const st=c?` style="--pearl:${c[0]}"`:"";
    const items=routes.map(route=>{
      const stops=route.stops||[];
      const counts=route.counts||[];
      const loop=!!route.loop;
      const rows=[];
      stops.forEach((s,i)=>{
        const flags=s[2]||0;
        const cls=["pearl-row","pearl-stop"];
        if(flags&1) cls.push("used");
        if(flags&2) cls.push("used-line");
        if(flags&4) cls.push("pass");
        const mark=flags&4?"":"<i></i>";
        rows.push(`<div class="${cls.join(" ")}"><span class="pearl-n"></span><span class="pearl-mark">${mark}</span><span class="pearl-lab">${esc(s[1]||"—")}</span></div>`);
        const last=i===stops.length-1;
        if(!last){
          rows.push(`<div class="pearl-row pearl-edge"><span class="pearl-n">${fmtN(counts[i])}</span><span class="pearl-mark"></span><span class="pearl-lab"></span></div>`);
        }else if(loop){
          rows.push(`<div class="pearl-row pearl-edge"><span class="pearl-n">${fmtN(counts[i])}</span><span class="pearl-mark"></span><span class="pearl-lab muted">↻</span></div>`);
        }
      });
      return `<div class="pearl${loop?" loop":""}"${st}>${rows.join("")}</div>`;
    });
    return `<div class="pearls">${items.join("")}</div>`;
  }

  const copyBtn=document.getElementById("linesCopy");
  function copyTsv(){
    copyText(linesTsv(visibleGroups().groups), copyBtn);
  }
  if(filterEl) filterEl.addEventListener("input", render);
  if(copyBtn) copyBtn.onclick=copyTsv;
  render();
})();
