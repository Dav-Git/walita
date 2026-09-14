// ---------- Theme (hell/dunkel) ----------
(function(){
  const root=document.documentElement;
  const btn=document.getElementById("themeToggle");
  const icon=document.getElementById("themeIcon");
  const label=document.getElementById("themeLabel");
  let stored=null;
  try { stored=localStorage.getItem("trwl-theme"); } catch(e){}
  const prefersDark=window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  let theme = stored || (prefersDark ? "dark" : "light");
  function apply(){
    root.dataset.theme = theme;
    // Der Button zeigt das Ziel des nächsten Klicks an.
    if(theme==="dark"){ icon.textContent="☀️"; label.textContent="Hell"; }
    else { icon.textContent="🌙"; label.textContent="Dunkel"; }
  }
  apply();
  btn.onclick=()=>{
    theme = theme==="dark" ? "light" : "dark";
    try { localStorage.setItem("trwl-theme", theme); } catch(e){}
    apply();
  };
})();

