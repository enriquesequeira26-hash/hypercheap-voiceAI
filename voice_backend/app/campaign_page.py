# ruff: noqa: E501  (inline HTML, CSS and JS)
"""The /campana panel: one self-contained page (no build step)."""

PAGE = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Campaña de llamadas</title>
<style>
:root{--bg:#0f1115;--card:#181b22;--line:#2a303c;--text:#e8eaed;--dim:#9aa3b2;--red:#d9232e;--ok:#3fb27f;--warn:#e0a53a}
*{box-sizing:border-box}body{font-family:system-ui,sans-serif;background:var(--bg);color:var(--text);margin:0;padding:20px}
main{max-width:1100px;margin:0 auto;display:grid;gap:16px}h1{font-size:22px;margin:0}h2{font-size:15px;margin:0 0 10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.dim{color:var(--dim);font-size:13px}
input,select{padding:9px 10px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:inherit;font-size:14px}
button{padding:9px 14px;border:0;border-radius:8px;background:var(--red);color:#fff;font-size:14px;font-weight:600;cursor:pointer}
button.sec{background:#2a303c}button:disabled{opacity:.5;cursor:default}
.chip{font-size:12px;padding:4px 9px;border-radius:99px;border:1px solid var(--line);color:var(--dim)}
.chip.ok{color:var(--ok);border-color:var(--ok)}.chip.no{color:var(--warn);border-color:var(--warn)}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:500}td:last-child{white-space:nowrap}.tablewrap{overflow-x:auto}td.sum{max-width:360px;color:var(--dim)}
.live{color:var(--warn)}.done{color:var(--ok)}#msg{min-height:20px;font-size:14px}#msg.err{color:#ff7b7b}
#detail .line{margin:6px 0;font-size:14px}#detail .who{color:var(--dim);font-size:12px;margin-right:6px}
#detail dl{display:grid;grid-template-columns:170px 1fr;gap:4px 12px;font-size:14px;margin:10px 0}#detail dt{color:var(--dim)}#detail dd{margin:0}
audio{width:100%;margin-top:8px}[hidden]{display:none!important}
</style></head><body><main>
<div class="row" style="justify-content:space-between"><h1>Campaña de llamadas</h1><div class="row" id="chips"></div></div>

<section class="card" id="login">
  <h2>Clave de llamadas</h2>
  <form class="row" id="loginForm"><input id="key" type="password" placeholder="CALL_API_KEY" autocomplete="off" required>
  <button>Entrar</button></form>
</section>

<div id="app" hidden style="display:grid;gap:16px">
<div id="msg"></div>

<section class="card">
  <h2>1. Base de clientes</h2>
  <div class="row"><input type="file" id="file" accept=".xlsx"><button class="sec" id="upload">Subir Excel</button>
  <span class="dim" id="baseInfo"></span></div>
</section>

<section class="card">
  <h2>2. Lote a llamar</h2>
  <div class="row"><label class="dim">Segmento <select id="seg"><option value="P1">P1 Reactivación</option>
  <option value="P2">P2 Bajo consumo</option><option value="P3">P3 Desarrollo</option><option value="P4">P4 Mantener</option>
  <option value="">Todos</option></select></label>
  <label class="dim">Cantidad <input id="qty" type="number" min="1" max="50" value="20" style="width:80px"></label>
  <button class="sec" id="mkBatch">Crear lote nuevo</button>
  <span class="dim">Toma los siguientes clientes pendientes con teléfono. Reemplaza el lote actual.</span></div>
</section>

<section class="card">
  <h2>3. Llamadas</h2>
  <div class="row" style="margin-bottom:12px">
    <button id="start">Iniciar llamadas</button><button class="sec" id="pause" disabled>Pausar</button>
    <button class="sec" id="xlsx">Descargar Excel</button>
    <label class="dim">Ensayo: llamar a este número en vez del cliente
      <input id="test" placeholder="+50688887777" style="width:150px"></label>
  </div>
  <div class="dim" style="margin-bottom:10px">Las llamadas salen una tras otra mientras esta página esté abierta. Con un número de ensayo, el resultado no se escribe en el Excel.</div>
  <div class="tablewrap"><table><thead><tr><th>#</th><th>Cliente</th><th>Población</th><th>Teléfono</th><th>Estado</th>
  <th>Resultado</th><th>Resumen</th><th></th></tr></thead><tbody id="rows"></tbody></table></div>
</section>

<section class="card" id="detail" hidden>
  <div class="row" style="justify-content:space-between"><h2 id="dTitle"></h2><button class="sec" id="dClose">Cerrar</button></div>
  <dl id="dFields"></dl>
  <div class="row"><button class="sec" id="dPlay" hidden>Escuchar grabación</button></div><audio id="dAudio" controls hidden></audio>
  <h2 style="margin-top:14px">Transcripción</h2><div id="dLines"></div>
</section>
</div>
</main>
<script>
const $=id=>document.getElementById(id);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
let key='',state=null,running=false,live={};
try{key=sessionStorage.getItem('callKey')||''}catch(e){}

function say(text,isErr){const m=$('msg');m.textContent=text||'';m.className=isErr?'err':''}
function el(tag,text,cls){const e=document.createElement(tag);if(text!=null)e.textContent=text;if(cls)e.className=cls;return e}

async function api(path,opts){
  opts=opts||{};
  const r=await fetch('/campana/api'+path,Object.assign({},opts,{headers:Object.assign({'x-call-key':key},opts.headers||{})}));
  if(r.status===401){logout();throw new Error('Clave incorrecta.')}
  if(!r.ok){let m='Error '+r.status;try{m=(await r.json()).detail||m}catch(e){}throw new Error(m)}
  return r;
}
function logout(){key='';try{sessionStorage.removeItem('callKey')}catch(e){}$('app').hidden=true;$('login').hidden=false}

$('loginForm').addEventListener('submit',async e=>{e.preventDefault();key=$('key').value.trim();
  try{await load();try{sessionStorage.setItem('callKey',key)}catch(err){}}catch(err){$('key').value='';alert(err.message)}});

function chip(label,ok){return el('span',label,'chip '+(ok?'ok':'no'))}
const PHASE={pendiente:'Pendiente',marcando:'Marcando…',en_llamada:'En llamada…',cerrando:'Cerrando…',terminada:'Terminada',sin_llamar:'Pendiente'};

function render(){
  const c=$('chips');c.replaceChildren(chip(state.almacenamiento?'Almacenamiento listo':'Falta almacenamiento',state.almacenamiento),
    chip(state.twilio?'Twilio listo':'Falta Twilio',state.twilio),
    chip(state.en_horario?'En horario ('+state.horario+' h)':'Fuera de horario ('+state.horario+' h)',state.en_horario),
    chip(state.grabacion?'Grabación activa':'Sin grabación',state.grabacion));
  $('baseInfo').textContent=state.base?state.base.total+' clientes, '+state.base.con_telefono+' con teléfono, '+state.base.pendientes+' pendientes por llamar.':'Todavía no hay base cargada.';
  const body=$('rows');body.replaceChildren();
  if(!state.lote.length){const tr=el('tr');const td=el('td','No hay lote. Cree uno en el paso 2.','dim');td.colSpan=8;tr.append(td);body.append(tr)}
  state.lote.forEach((row,i)=>{
    const s=live[row.id]||row,tr=el('tr');
    const phase=el('td',(PHASE[s.fase]||s.fase)+(s.prueba?' (ensayo)':''),s.fase==='terminada'?'done':(s.fase==='pendiente'||s.fase==='sin_llamar'?'':'live'));
    const act=el('td');const call=el('button','Llamar','sec');call.disabled=running;call.onclick=()=>single(row.id);
    const see=el('button','Ver','sec');see.style.marginLeft='6px';see.onclick=()=>detail(row.id);act.append(call,see);
    tr.append(el('td',String(i+1)),el('td',row.nombre),el('td',row.poblacion),el('td',row.telefono),phase,
      el('td',[s.estado_gestion,s.resultado].filter(Boolean).join(' · ')),el('td',s.resumen||'','sum'),act);
    body.append(tr);
  });
  $('start').disabled=running||!state.lote.length;$('pause').disabled=!running;
}

async function load(){state=await (await api('/estado')).json();$('login').hidden=true;$('app').hidden=false;render();
  const wanted=new URLSearchParams(location.search).get('cliente');if(wanted&&!$('detail').dataset.id)detail(wanted)}

$('upload').onclick=async()=>{const f=$('file').files[0];if(!f)return say('Elija primero el archivo .xlsx.',true);
  say('Subiendo y leyendo el Excel…');try{state=await (await api('/base',{method:'POST',body:f})).json();live={};render();say('Base cargada.')}catch(e){say(e.message,true)}};

$('mkBatch').onclick=async()=>{if(state.lote.length&&!confirm('Esto reemplaza el lote actual. ¿Continuar?'))return;
  say('Creando lote…');try{state=await (await api('/lote',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({segmento:$('seg').value,cantidad:Number($('qty').value)||20})})).json();live={};render();say('Lote creado: '+state.lote.length+' clientes.')}catch(e){say(e.message,true)}};

async function callOne(id){
  const test=$('test').value.trim();
  live[id]={fase:'marcando',prueba:!!test};render();
  await api('/llamar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:id,probar_con:test})});
  const t0=Date.now();let closing=0;
  while(Date.now()-t0<14*60*1000){
    await sleep(3000);
    let snap;
    try{snap=await (await api('/llamada/'+encodeURIComponent(id)+(closing&&Date.now()-closing>40000?'?forzar=1':''))).json()}catch(e){continue}
    if(snap.fase==='cerrando'){if(!closing)closing=Date.now()}
    live[id]=snap;render();
    if(snap.fase==='terminada')return snap;
  }
  throw new Error('La llamada no terminó a tiempo; revise en Twilio.');
}

async function single(id){if(running)return;running=true;render();say('Llamando…');
  try{const s=await callOne(id);say('Llamada terminada: '+(s.resultado||s.estado_gestion||'sin resultado')+'.')}catch(e){delete live[id];say(e.message,true)}
  running=false;render()}

$('start').onclick=async()=>{if(running)return;running=true;render();let made=0;
  for(const row of state.lote){
    if(!running)break;
    const done=(live[row.id]||row).fase==='terminada';
    if(done&&!$('test').value.trim())continue;
    say('Llamando a '+row.nombre+'…');
    try{await callOne(row.id);made++}catch(e){delete live[row.id];say(e.message,true);break}
    if(running)await sleep(6000);
  }
  if(running)say(made?'Lote terminado: '+made+' llamadas.':'No quedaban clientes por llamar en este lote.');
  running=false;render()};
$('pause').onclick=()=>{running=false;say('En pausa: la llamada en curso termina y no se marca la siguiente.');$('pause').disabled=true};

$('xlsx').onclick=async()=>{say('Preparando el Excel…');try{const r=await api('/excel');const blob=await r.blob();
  const name=(/filename="([^"]+)"/.exec(r.headers.get('Content-Disposition')||'')||[])[1]||'Plan Televentas.xlsx';
  const a=el('a');a.href=URL.createObjectURL(blob);a.download=name;document.body.append(a);a.click();a.remove();say('Excel descargado.')}catch(e){say(e.message,true)}};

async function detail(id){
  const box=$('detail');box.dataset.id=id;box.hidden=false;
  const row=(state.lote.find(r=>r.id===id))||{nombre:'Cliente '+id};
  $('dTitle').textContent=row.nombre;$('dFields').replaceChildren();$('dLines').replaceChildren(el('div','Cargando…','dim'));
  $('dAudio').hidden=true;$('dAudio').removeAttribute('src');$('dPlay').hidden=true;
  let s;try{s=await (await api('/llamada/'+encodeURIComponent(id))).json()}catch(e){$('dLines').replaceChildren(el('div',e.message,'dim'));return}
  const fields=[['Estado',(PHASE[s.fase]||s.fase)+(s.prueba?' (ensayo)':'')],['Fecha',(s.inicio||'').replace('T',' ').slice(0,16)],
    ['Duración',s.duracion_s?s.duracion_s+' s':''],['Estado gestión',s.estado_gestion],['Resultado',s.resultado],
    ['Contacto',s.nombre_contacto],['Teléfono 2',s.telefono_2],['Clasificación',s.clasificacion],['Causas',s.causas],
    ['Próximo seguimiento',s.proximo_seguimiento],['Resumen',s.resumen]];
  const dl=$('dFields');fields.forEach(([k,v])=>{if(v){dl.append(el('dt',k),el('dd',String(v)))}});
  const lines=$('dLines');lines.replaceChildren();
  (s.transcripcion||[]).forEach(l=>{const d=el('div',null,'line');d.append(el('span',l.quien==='agente'?'Agente':'Cliente','who'),document.createTextNode(l.texto));lines.append(d)});
  if(!(s.transcripcion||[]).length)lines.append(el('div','Sin transcripción.','dim'));
  if(s.grabacion){const b=$('dPlay');b.hidden=false;b.disabled=false;b.textContent='Escuchar grabación';b.onclick=async()=>{b.disabled=true;b.textContent='Cargando…';
    try{const blob=await (await api('/grabacion/'+s.grabacion)).blob();const a=$('dAudio');a.src=URL.createObjectURL(blob);a.hidden=false;b.hidden=true;a.play().catch(()=>{})}
    catch(e){b.disabled=false;b.textContent='Escuchar grabación';say(e.message,true)}}}
  box.scrollIntoView({behavior:'smooth'});
}
$('dClose').onclick=()=>{$('detail').hidden=true;delete $('detail').dataset.id;$('dAudio').pause()};

if(key)load().catch(()=>logout());
</script></body></html>"""
