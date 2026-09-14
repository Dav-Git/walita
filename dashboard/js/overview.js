// ---------- Overview ----------
(function(){
  const k=DATA.kpis;
  document.getElementById("subtitle").textContent =
    (k.first&&k.last)? (k.first+" – "+k.last) : "";
  const cards=[
    ["Check-ins", k.count.toLocaleString("de-DE")],
    ["Distanz", k.distanceKm.toLocaleString("de-DE")+" km"],
    ["Reisezeit", fmtDuration(k.durationMin)],
    ["Punkte", k.points.toLocaleString("de-DE")],
    ["Stationen", k.stations.toLocaleString("de-DE")],
    ["Linien", k.lines.toLocaleString("de-DE")],
  ];
  document.getElementById("kpis").innerHTML = cards.map(c=>
    `<div class="card"><div class="v">${esc(c[1])}</div><div class="l">${esc(c[0])}</div></div>`).join("");
  (function(){
    const segs=DATA.segments||[];
    const tbody=document.querySelector("#segtable tbody");
    const panel=document.getElementById("segpanel");
    let showAll=segs.length<=30;
    function paint(){
      const data=showAll?segs:segs.slice(0,30);
      const remaining=segs.length-data.length;
      tbody.innerHTML=data.map(s=>
        `<tr><td>${esc(s.from)}</td><td>${esc(s.to)}</td><td>${s.count}</td></tr>`).join("");
      let btn=panel.querySelector(".stats-more");
      if(remaining>0){
        if(!btn){ btn=document.createElement("button"); btn.type="button"; btn.className="stats-more"; panel.appendChild(btn); }
        btn.textContent=`Mehr laden (${remaining.toLocaleString("de-DE")} weitere, ${segs.length.toLocaleString("de-DE")} gesamt)`;
        btn.onclick=()=>{ showAll=true; paint(); };
      } else if(btn){ btn.remove(); }
    }
    paint();
  })();
})();

