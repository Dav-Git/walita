// ---------- Linien-Katalog ----------
(function(){
  const LINES=DATA.lines||[];
  const LC=DATA.lineColors||{};
  const filterEl=document.getElementById("linesFilter");
  const countEl=document.getElementById("linesCount");
  const bodyEl=document.getElementById("linesBody");
  const EDGE_LIMIT=12;
  // RYB: Primär → Sekundär → Tertiär → dunklere, dann hellere Stufen.
  const BR_PALETTE=[
    "#dc2626","#ffd200","#2563eb",
    "#ea580c","#16a34a","#7c3aed",
    "#e11d48","#65a30d","#0891b2","#c026d3","#d97706","#4f46e5",
    "#9f1239","#a16207","#1e40af","#9a3412","#166534","#5b21b6",
    "#fb7185","#facc15","#60a5fa","#fb923c","#4ade80","#a78bfa",
  ];
  const expanded=new Set();
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

  function render(){
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
    bodyEl.querySelectorAll(".stats-more[data-idx]").forEach(btn=>{
      btn.onclick=()=>{
        const row=LINES[+btn.dataset.idx];
        if(row){ expanded.add(row.key); openLines.add(row.key); }
        render();
      };
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
    const allEdges=r.edges||[];
    const idx=LINES.indexOf(r);
    const showAll=expanded.has(r.key);
    const edges=(!showAll && allEdges.length>EDGE_LIMIT)?allEdges.slice(0,EDGE_LIMIT):allEdges;
    const remaining=allEdges.length-edges.length;
    const edgeRows=edges.map(e=>
      `<tr><td class="lbl">${esc(e[0]||"—")}</td><td class="lbl">${esc(e[1]||"—")}</td><td class="n">×${fmtN(e[2])}</td></tr>`
    ).join("");
    const more=remaining>0
      ? `<button type="button" class="stats-more" data-idx="${idx}">Mehr laden (${fmtN(remaining)} weitere, ${fmtN(allEdges.length)} gesamt)</button>`
      : "";
    const edgeBlock=allEdges.length
      ? `<table class="line-edges"><thead><tr><th>Von</th><th>Nach</th><th></th></tr></thead><tbody>${edgeRows}</tbody></table>${more}`
      : `<p class="hint" style="margin:8px 0 0">Keine Kanten.</p>`;
    const isOpen=openLines.has(r.key);
    return `<details class="line-card"${isOpen?" open":""} data-idx="${idx}">
      <summary class="line-card-head">${badge(r.key)}
        <span class="line-card-meta">${fmtN(r.distanceKm)} km · ${fmtN(r.count)} ${r.count===1?"Fahrt":"Fahrten"} · ${fmtN(allEdges.length)} ${allEdges.length===1?"Kante":"Kanten"}</span>
      </summary>
      <div class="line-card-body">
        <div class="line-stack">${stack||""}</div>
        <div class="line-stack-legend">${legend||'<span>keine Baureihen getaggt</span>'}</div>
        ${edgeBlock}
      </div>
    </details>`;
  }

  if(filterEl) filterEl.addEventListener("input", render);
  render();
})();
