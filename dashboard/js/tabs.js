// ---------- Tabs ----------
function showTab(tabId){
  document.querySelectorAll(".navlist [data-tab]").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
  const btn=document.querySelector(`.navlist [data-tab="${tabId}"]`);
  if(btn) btn.classList.add("active");
  const tab=document.getElementById(tabId);
  if(tab) tab.classList.add("active");
  const statsGroup=document.getElementById("navStats");
  if(statsGroup) statsGroup.classList.toggle("open", tabId==="stats");
  if(tabId==="map" && map){ setTimeout(()=>{
    map.invalidateSize();
    if(!mapFitted && mapBounds.length){ map.fitBounds(mapBounds,{padding:[30,30]}); mapFitted=true; }
  },80); }
}
document.querySelectorAll(".navlist [data-tab]").forEach(b=>b.onclick=()=>{
  showTab(b.dataset.tab);
  if(b.dataset.tab==="stats"){
    const head=document.querySelector("#stats .tab-head");
    if(head) head.scrollIntoView({behavior:"smooth",block:"start"});
  }
});
document.querySelectorAll(".nav-sub [data-jump]").forEach(a=>{
  a.addEventListener("click",e=>{
    e.preventDefault();
    const id=a.getAttribute("data-jump");
    showTab("stats");
    const target=document.getElementById(id);
    if(target) setTimeout(()=>target.scrollIntoView({behavior:"smooth",block:"start"}), 30);
  });
});

