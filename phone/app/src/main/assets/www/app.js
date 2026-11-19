const API = "/api";
let currentPages = null;
let currentGcode = null;
let ws = null;

function el(id){ return document.getElementById(id); }

// ---------- нижняя навигация ----------
function initTabs(){
  document.querySelectorAll(".nav-item").forEach(b=>{
    b.addEventListener("click",()=>{
      document.querySelectorAll(".nav-item").forEach(x=>x.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p=>p.classList.remove("active"));
      b.classList.add("active");
      el("panel-"+b.dataset.tab).classList.add("active");
      // если перешли на превью и есть данные — перерисовать
      if(b.dataset.tab === "preview" && currentPages){
        renderPreviewArea(currentPages);
      }
    });
  });
}

// ---------- шрифты ----------
async function loadFonts(){
  try{
    const r = await fetch(API+"/fonts");
    const d = await r.json();
    const sel = el("sel-font");
    sel.innerHTML = "";
    (d.fonts||[]).forEach(f=>{
      const o = document.createElement("option");
      o.value = f.name;
      o.textContent = f.name + " — кириллица " + f.cyrillic_coverage + "%" + (f.error ? " (ошибка чтения)" : "");
      if (d.default && f.name === d.default) o.selected = true;
      sel.appendChild(o);
    });
    renderFontsList(d.fonts||[]);
    el("hint-layout").textContent = "Найдено шрифтов: " + (d.fonts||[]).length + ", по умолчанию: " + (d.default||"—");
  }catch(e){
    console.error(e);
    el("hint-layout").textContent = "Ошибка загрузки шрифтов.";
  }
}

function renderFontsList(fonts){
  const box = el("fonts-list");
  box.innerHTML = "";
  fonts.forEach(f=>{
    const item = document.createElement("div");
    item.className = "font-item";
    const ok = f.sample_ok ? '<span class="font-ok">✓ кириллица OK</span>' : '<span class="font-bad">✗ неполная кириллица</span>';
    const miss = f.missing_sample && f.missing_sample.length ? '<div class="font-missing">Отсутствуют из образца: '+f.missing_sample.join("")+'</div>' : "";
    item.innerHTML = `<div><div class="font-name">${f.name}</div><div class="font-meta">${f.filename} • ${f.cyrillic_present}/${f.cyrillic_total} кодпоинтов • UPM ${f.units_per_em}</div>${miss}</div>${ok}`;
    box.appendChild(item);
  });
}

// ---------- пресеты ----------
async function loadPresets(){
  try{
    const r = await fetch(API+"/presets");
    const d = await r.json();
    const sel = el("sel-preset");
    sel.innerHTML = "";
    d.presets.forEach(p=>{
      const o = document.createElement("option");
      o.value = p.id;
      o.textContent = p.name + " (" + p.kind + ")";
      sel.appendChild(o);
    });
  }catch(e){ console.error(e); }
}

// ---------- разметка ----------
async function doLayout(){
  const text = el("txt-content").value;
  if(!text.trim()){
    el("hint-layout").textContent = "Вставьте текст конспекта.";
    return;
  }
  const presetId = el("sel-preset").value;
  const fontName = el("sel-font").value || null;
  const body = {
    text,
    preset_id: presetId,
    font_name: fontName,
    font_size_mm: parseFloat(el("in-font-size").value)||8,
    line_height_mm: parseFloat(el("in-line-height").value)||14,
    tracking_mm: parseFloat(el("in-tracking").value)||1,
    slant_deg: parseFloat(el("in-slant").value)||0,
    margin_left_mm: parseFloat(el("in-ml").value)||10,
    margin_right_mm: parseFloat(el("in-mr").value)||10,
    margin_top_mm: parseFloat(el("in-mt").value)||12,
    margin_bottom_mm: parseFloat(el("in-mb").value)||12,
  };
  try{
    el("btn-layout").disabled = true;
    el("btn-layout").textContent = "Выполняется…";
    const r = await fetch(API+"/layout",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    if(!r.ok) throw new Error("Ошибка сервера "+r.status);
    const d = await r.json();
    currentPages = d.pages;
    currentGcode = null;
    renderPreviewArea(d.pages);
    el("result-box").style.display = "block";
    el("stat-pages").textContent = d.page_count;
    el("stat-chars").textContent = d.total_chars;
    el("stat-strokes").textContent = d.total_strokes;
    el("stat-time").textContent = d.est_time_min;
    el("badge-pages").textContent = "Страниц: "+d.page_count;
    el("badge-est").textContent = "Время: ~"+d.est_time_min+" мин";
    el("hint-layout").textContent =
      "Готово: страниц "+d.page_count+" | символов "+d.total_chars+" | штрихов "+d.total_strokes+
      " | примерно "+d.est_time_min+" мин";
  }catch(e){
    alert("Ошибка разметки: "+e.message);
  }finally{
    el("btn-layout").disabled = false;
    el("btn-layout").textContent = "Разместить страницы";
  }
}

async function doGcode(){
  if(!currentPages){ alert("Сначала выполните разметку страниц."); return; }
  try{
    el("btn-gcode").disabled = true;
    el("btn-gcode").textContent = "Генерирую…";
    const r = await fetch(API+"/gcode",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({pages:currentPages,job_name:"conpect"})});
    const d = await r.json();
    if(!d.ok) throw new Error(d.error||"ошибка");
    currentGcode = d.gcode;
    el("hint-layout").textContent += " | G-code: "+d.line_count+" строк, "+d.mm_total+" мм, ~"+d.est_time_min+" мин (сохранено в gcode/"+d.filename+")";
    alert("G-code сформирован: "+d.filename+" ("+d.line_count+" строк)");
  }catch(e){ alert("Ошибка G-code: "+e.message); }
  finally{
    el("btn-gcode").disabled = false;
    el("btn-gcode").textContent = "Сформировать G-code";
  }
}

async function sendToMachine(){
  if(!currentGcode){ alert("Нет G-code для отправки."); return; }
  if(ws && ws.readyState===WebSocket.OPEN){
    ws.send(JSON.stringify({type:"start",gcode:currentGcode}));
  }else{
    try{
      await fetch(API+"/serial/stream",{method:"POST",body:currentGcode});
      el("hint-layout").textContent += " | отправлено на станок";
    }catch(e){ alert("Ошибка отправки: "+e.message); }
  }
}

// ---------- SVG чертеж ----------
let svgText = null;
el("file-svg").addEventListener("change",async(e)=>{
  const f = e.target.files[0]; if(!f) return;
  const text = await f.text();
  svgText = text;
  el("file-info").style.display = "block";
  el("file-info").innerHTML = '<strong>Загружен:</strong> '+f.name+' ('+text.length+' байт)';
  el("hint-svg").textContent = "Файл готов к предпросмотру.";
});

// drag & drop визуал
const drop = el("file-drop");
["dragenter","dragover"].forEach(ev=>drop.addEventListener(ev,(e)=>{e.preventDefault(); drop.classList.add("drag-over");}));
["dragleave","drop"].forEach(ev=>drop.addEventListener(ev,(e)=>{e.preventDefault(); drop.classList.remove("drag-over");}));
drop.addEventListener("drop",async(e)=>{
  const f = e.dataTransfer.files[0]; if(!f) return;
  const text = await f.text();
  svgText = text;
  el("file-info").style.display = "block";
  el("file-info").innerHTML = '<strong>Загружен:</strong> '+f.name+' ('+text.length+' байт)';
  el("hint-svg").textContent = "Файл готов к предпросмотру.";
});

async function svgPreview(){
  if(!svgText){ alert("Загрузите SVG-файл."); return; }
  const fd = new FormData(); fd.append("file", new Blob([svgText],{type:"image/svg+xml"}),"drawing.svg");
  try{
    const r = await fetch(API+"/svg",{method:"POST",body:fd});
    const d = await r.json();
    currentPages = [{page_no:1,width_mm:210,height_mm:297,strokes:d.strokes,char_count:0}];
    renderPreviewArea(currentPages);
    el("hint-svg").textContent = "Контуров: "+d.count;
    currentGcode = null;
  }catch(e){ alert("Ошибка SVG: "+e.message); }
}
async function svgToGcode(){
  if(!svgText){ alert("Загрузите SVG-файл."); return; }
  if(!currentPages){ await svgPreview(); }
  try{
    const r = await fetch(API+"/gcode",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({pages:currentPages,job_name:"svg"})});
    const d = await r.json();
    currentGcode = d.gcode;
    el("hint-svg").textContent += " | G-code: "+d.line_count+" строк";
    alert("G-code из SVG: "+d.filename);
  }catch(e){ alert("Ошибка: "+e.message); }
}

function buildPreviewSVG(pages){
  let parts = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${pages[0].width_mm} ${pages.reduce((s,p)=>s+p.height_mm,0)}">`;
  let oy = 0;
  pages.forEach(pg=>{
    parts += `<g transform="translate(0,${oy})">`;
    parts += `<rect x="0" y="0" width="${pg.width_mm}" height="${pg.height_mm}" fill="none" stroke="#ddd" stroke-width="0.5"/>`;
    pg.strokes.forEach(s=>{
      if(s.points.length<2) return;
      const pts = s.points.map(pt=>pt.x+","+pt.y).join(" ");
      parts += `<polyline points="${pts}" fill="none" stroke="${s.pen_down?"#111":"#999"}" stroke-width="${s.pen_down?0.18:0.1}" stroke-linecap="round"/>`;
    });
    parts += `</g>`;
    oy += pg.height_mm;
  });
  parts += "</svg>";
  return parts;
}

function renderPreviewArea(pages){
  el("preview-area").innerHTML = buildPreviewSVG(pages);
}

// ---------- порт / статус ----------
async function refreshPorts(){
  try{
    const r = await fetch(API+"/serial/ports");
    const d = await r.json();
    const sel = el("sel-port");
    sel.innerHTML = "";
    (d.ports||[]).forEach(p=>{
      const o = document.createElement("option");
      o.value = p.device;
      o.textContent = p.device + " — " + (p.description||"");
      sel.appendChild(o);
    });
  }catch(e){ console.error(e); }
}

function setConnection(ok, simulate){
  const chip = el("chip-connection");
  chip.textContent = simulate ? "Симуляция" : (ok?"Подключено":"Отключено");
  chip.className = "status-chip " + (simulate?"sim":(ok?"ok":""));
}
function updateStatusBar(state, message){
  const bar = el("status-bar");
  const dot = bar.querySelector(".status-indicator");
  bar.className = "status-bar "+(state||"");
  bar.querySelector(".status-text").textContent = "Статус: "+(state||"") + (message?" — "+message:"");
}

async function fetchStatus(){
  try{
    const r = await fetch(API+"/serial/status");
    const d = await r.json();
    setConnection(d.state!=="disconnected", d.state==="disconnected" && false);
    updateStatusBar(d.state, d.message);
    if(d.progress!==undefined){
      const bar = el("status-bar");
      bar.querySelector(".status-text").textContent += " | "+d.progress+"% ("+d.line+"/"+d.total+")";
    }
  }catch(e){ /* игнор */ }
}

async function connectSerial(){
  const port = el("sel-port").value;
  const baud = parseInt(el("sel-baud").value,10)||115200;
  try{
    const r = await fetch(API+"/serial/connect",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({port:port||undefined,baudrate:baud})});
    const d = await r.json();
    setConnection(d.ok, d.simulate);
    fetchStatus();
    if(!d.ok) alert("Подключение: "+(d.error||"ошибка"));
  }catch(e){ alert("Ошибка подключения: "+e.message); }
}
async function disconnectSerial(){
  await fetch(API+"/serial/disconnect",{method:"POST"});
  setConnection(false,false);
  fetchStatus();
}

function cmdPOST(path){
  return fetch(API+path,{method:"POST"});
}
el("btn-refresh-ports").addEventListener("click", refreshPorts);
el("btn-connect").addEventListener("click", connectSerial);
el("btn-disconnect").addEventListener("click", disconnectSerial);
el("btn-home").addEventListener("click",()=>cmdPOST("/serial/home"));
el("btn-zero").addEventListener("click",()=>cmdPOST("/serial/zero"));
el("btn-pause").addEventListener("click",()=>cmdPOST("/serial/pause"));
el("btn-resume").addEventListener("click",()=>cmdPOST("/serial/resume"));
el("btn-stop").addEventListener("click",()=>cmdPOST("/serial/stop"));
el("btn-pen-down").addEventListener("click",()=>cmdPOST("/serial/pen_down"));
el("btn-pen-up").addEventListener("click",()=>cmdPOST("/serial/pen_up"));
el("jog-x-plus").addEventListener("click",()=>jog("X",1));
el("jog-x-minus").addEventListener("click",()=>jog("X",-1));
el("jog-y-plus").addEventListener("click",()=>jog("Y",1));
el("jog-y-minus").addEventListener("click",()=>jog("Y",-1));
function jog(axis, sign){
  const mm = sign*parseFloat(el("in-jog").value||10);
  cmdPOST("/serial/jog?"+new URLSearchParams({axis,mm}));
}

el("btn-layout").addEventListener("click", doLayout);
el("btn-gcode").addEventListener("click", doGcode);
el("btn-send").addEventListener("click", sendToMachine);
el("btn-svg-preview").addEventListener("click", svgPreview);
el("btn-svg-gcode").addEventListener("click", svgToGcode);
el("btn-svg-send").addEventListener("click", sendToMachine);

// ---------- конфиг / настройки ----------
async function loadConfigView(){
  try{
    const r = await fetch("/config.yaml");
    const t = await r.text();
    el("cfg-view").textContent = t || "config.yaml не найден";
  }catch(e){ el("cfg-view").textContent = "Не удалось загрузить config.yaml"; }
}
el("btn-reload-config").addEventListener("click", loadConfigView);

// ---------- текстовый счетчик ----------
el("txt-content").addEventListener("input",()=>{
  el("text-counter").textContent = el("txt-content").value.length + " символов";
});

// ---------- WebSocket для статуса ----------
function initWS(){
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = proto + "//" + location.host + "/ws";
  try{
    ws = new WebSocket(wsUrl);
    ws.onopen = ()=>{ /* connected */ };
    ws.onmessage = (ev)=>{
      try{ const m = JSON.parse(ev.data); if(m.type==="status") updateStatusBar(m.state,m.message); }
      catch(e){}
    };
    ws.onclose = ()=>{ setTimeout(initWS, 2000); };
    ws.onerror = ()=>{};
  }catch(e){}
}

// ---------- старт ----------
window.addEventListener("DOMContentLoaded",()=>{
  initTabs();
  loadFonts();
  loadPresets();
  loadConfigView();
  initWS();
  setInterval(fetchStatus, 1500);
  refreshPorts();
  el("text-counter").textContent = "0 символов";
});
