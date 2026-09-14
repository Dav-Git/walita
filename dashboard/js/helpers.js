// ---------- Helpers ----------
function fmtDate(iso){ if(!iso) return ""; const d=new Date(iso);
  return isNaN(d)? iso : d.toLocaleDateString("de-DE",{year:"numeric",month:"2-digit",day:"2-digit"}); }
function fmtTime(iso){ if(!iso) return "—"; const d=new Date(iso);
  return isNaN(d)? "—" : d.toLocaleTimeString("de-DE",{hour:"2-digit",minute:"2-digit"}); }
function fmtDuration(min){ const h=Math.floor(min/60), m=min%60;
  return h? h+" h "+m+" min" : m+" min"; }
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"']/g,c=>(
  {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }

function isoToday(){
  const d=new Date();
  const m=String(d.getMonth()+1).padStart(2,"0");
  const day=String(d.getDate()).padStart(2,"0");
  return d.getFullYear()+"-"+m+"-"+day;
}
function datePickerMax(last){
  const t=isoToday();
  return (last && last>t)?last:t;
}
function bindDateInput(el, first, last){
  if(!el) return;
  if(first) el.min=first;
  el.max=datePickerMax(last);
}

// Interner Linien-Schlüssel = Name + \\x1f + Operator; Anzeige nur der Name.
const LINE_SEP="\x1f";
const lineName=k=>{
  if(k==null||k==="") return "";
  const s=String(k), i=s.indexOf(LINE_SEP);
  return i<0 ? s : s.slice(0,i);
};

// Produktkategorie -> Label (global, von Karte und Fahrzeugen genutzt).
const CAT_LABEL={suburban:"S-Bahn",regional:"Regional",regionalExp:"Regional-Express",
  nationalExpress:"Fernverkehr",national:"Fernverkehr",tram:"Tram",subway:"U-Bahn",
  bus:"Bus",ferry:"Fähre"};
const catLabel=c=>CAT_LABEL[c]||c||"Unbekannt";

