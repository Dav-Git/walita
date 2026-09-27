// ---------- Heimatregion (globaler Operator-Filter) ----------
const HOME_KEY="trwl-home";
let homeOn=false;
try { homeOn=localStorage.getItem(HOME_KEY)==="1"; } catch(e){}
if(!DATA.home) homeOn=false;
const homeListeners=[];
function homeActive(){ return !!(homeOn && DATA.home); }
function D(){ return homeActive() ? DATA.home : DATA; }
function onHomeChange(fn){ homeListeners.push(fn); }
function paintHomeButton(){
  const btn=document.getElementById("homeToggle");
  if(!btn) return;
  const on=homeActive();
  btn.hidden=!DATA.home;
  btn.setAttribute("aria-pressed", on?"true":"false");
  btn.classList.toggle("active", on);
}
function setHome(on){
  if(!DATA.home) return;
  homeOn=!!on;
  try { localStorage.setItem(HOME_KEY, homeOn?"1":"0"); } catch(e){}
  paintHomeButton();
  homeListeners.forEach(fn=>{ try{ fn(); }catch(err){} });
}
paintHomeButton();
(function(){
  const btn=document.getElementById("homeToggle");
  if(btn) btn.onclick=()=>setHome(!homeOn);
})();
