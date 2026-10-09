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
.line{margin:6px 0;font-size:14px}.who{color:var(--dim);font-size:12px;margin-right:6px}
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
    <label class="dim"><input type="checkbox" id="withTests"> Incluir ensayos en el Excel</label>
    <label class="dim">Ensayo por teléfono: llamar a este número en vez del cliente
      <input id="test" placeholder="+50688887777" style="width:150px"></label>
  </div>
  <div class="dim" style="margin-bottom:10px">Las llamadas salen una tras otra mientras esta página esté abierta. <b>Ensayar</b> hace la llamada aquí mismo, con su micrófono y sin Twilio: usted hace de cliente. Los ensayos no se escriben en el Excel salvo que marque la casilla.</div>
  <div class="tablewrap"><table><thead><tr><th>#</th><th>Cliente</th><th>Población</th><th>Teléfono</th><th>Estado</th>
  <th>Resultado</th><th>Resumen</th><th></th></tr></thead><tbody id="rows"></tbody></table></div>
</section>

<section class="card" id="rehearsal" hidden>
  <div class="row" style="justify-content:space-between"><h2 id="rTitle"></h2><button id="rHang">Colgar</button></div>
  <div class="dim" id="rStatus"></div><div id="rLines"></div>
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
    const rb=el('button','Ensayar','sec');rb.style.marginLeft='6px';rb.disabled=running;rb.onclick=()=>rehearse(row);
    const see=el('button','Ver','sec');see.style.marginLeft='6px';see.onclick=()=>detail(row.id);act.append(call,rb,see);
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

$('xlsx').onclick=async()=>{say('Preparando el Excel…');try{const r=await api('/excel'+($('withTests').checked?'?ensayos=1':''));const blob=await r.blob();
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
// --- Ensayo en el navegador: el micrófono hace de cliente ---
const CAPTURE=`class Cap extends AudioWorkletProcessor{constructor(){super();this.ratio=sampleRate/16000;this.phase=0;this.acc=0;this.n=0;this.out=[]}
process(inputs){const ch=inputs[0][0];if(!ch)return true;for(let i=0;i<ch.length;i++){this.acc+=ch[i];this.n++;this.phase+=1;
if(this.phase>=this.ratio){this.phase-=this.ratio;let s=this.acc/this.n;this.acc=0;this.n=0;s=Math.max(-1,Math.min(1,s));this.out.push(s<0?s*32768:s*32767);
if(this.out.length>=640){const b=new Int16Array(this.out);this.out=[];this.port.postMessage(b.buffer,[b.buffer])}}}return true}}
registerProcessor('cap',Cap)`;
let reh=null;
function rLine(who,text){const d=el('div',null,'line');d.append(el('span',who,'who'),document.createTextNode(text));$('rLines').append(d);d.scrollIntoView({block:'nearest'})}

async function rehearse(row){
  if(running||reh)return;
  running=true;render();say('');
  const box=$('rehearsal');box.hidden=false;$('rTitle').textContent='Ensayo: '+row.nombre;$('rLines').replaceChildren();
  $('rStatus').textContent='Pidiendo permiso para usar el micrófono…';box.scrollIntoView({behavior:'smooth'});
  const s=reh={id:row.id,sources:[],playAt:0,rate:48000,closed:false};
  try{
    s.stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true,channelCount:1}});
    s.ctx=new (window.AudioContext||window.webkitAudioContext)();await s.ctx.resume();
    const url=URL.createObjectURL(new Blob([CAPTURE],{type:'application/javascript'}));
    await s.ctx.audioWorklet.addModule(url);URL.revokeObjectURL(url);
    s.node=new AudioWorkletNode(s.ctx,'cap');s.mic=s.ctx.createMediaStreamSource(s.stream);s.mic.connect(s.node);
    $('rStatus').textContent='Conectando…';
    s.ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws/ensayo');s.ws.binaryType='arraybuffer';
    s.ws.onopen=()=>s.ws.send(JSON.stringify({type:'start',key:key,id:row.id}));
    s.node.port.onmessage=e=>{if(s.live&&s.ws.readyState===1)s.ws.send(e.data)};
    s.ws.onmessage=e=>{
      if(typeof e.data!=='string'){playChunk(s,e.data);return}
      const m=JSON.parse(e.data);
      if(m.type==='ready'){s.rate=m.rate||48000;s.live=true;$('rStatus').textContent='En llamada. Conteste como si fuera el cliente; use audífonos para que el agente no se escuche a sí mismo.'}
      else if(m.type==='cliente')rLine('Cliente',m.texto);
      else if(m.type==='agente')rLine('Agente',m.texto);
      else if(m.type==='clear')stopAudio(s);
      else if(m.type==='end'){$('rStatus').textContent='El agente se despidió; colgando…';setTimeout(()=>endRehearsal(''),Math.max(0,(s.playAt-s.ctx.currentTime)*1000)+600)}
      else if(m.type==='error')endRehearsal(m.detalle||'Error en el ensayo.');
    };
    s.ws.onerror=()=>endRehearsal('No se pudo conectar el ensayo.');
    s.ws.onclose=()=>endRehearsal('');
  }catch(e){endRehearsal(e&&e.name==='NotAllowedError'?'Debe permitir el micrófono para ensayar.':(e.message||'No se pudo iniciar el ensayo.'))}
}
function playChunk(s,buf){
  if(s.closed)return;const n=buf.byteLength>>1;if(!n)return;
  const pcm=new Int16Array(buf,0,n),f=new Float32Array(n);for(let i=0;i<n;i++)f[i]=pcm[i]/32768;
  const ab=s.ctx.createBuffer(1,n,s.rate);ab.copyToChannel(f,0);const src=s.ctx.createBufferSource();src.buffer=ab;src.connect(s.ctx.destination);
  const at=Math.max(s.ctx.currentTime+0.03,s.playAt);src.start(at);s.playAt=at+ab.duration;s.sources.push(src);
  src.onended=()=>{s.sources=s.sources.filter(x=>x!==src)};
}
function stopAudio(s){s.sources.forEach(x=>{try{x.stop()}catch(e){}});s.sources=[];s.playAt=0}
async function endRehearsal(error){
  const s=reh;if(!s||s.closed)return;s.closed=true;s.live=false;
  stopAudio(s);try{s.stream&&s.stream.getTracks().forEach(t=>t.stop())}catch(e){}
  if(!error&&s.ws&&s.ws.readyState===1){
    // Ask the server to finish and wait until it closes: it saves the transcript before closing.
    $('rStatus').textContent='Guardando el ensayo…';
    await new Promise(done=>{const t=setTimeout(done,6000);s.ws.onclose=()=>{clearTimeout(t);done()};
      try{s.ws.send(JSON.stringify({type:'stop'}))}catch(e){clearTimeout(t);done()}});
  }
  try{s.ws&&s.ws.close()}catch(e){}
  try{s.ctx&&s.ctx.close()}catch(e){}
  reh=null;
  if(error){$('rehearsal').hidden=true;running=false;render();say(error,true);return}
  $('rStatus').textContent='Ensayo terminado. Analizando la llamada…';
  let snap=null;const t0=Date.now();
  while(Date.now()-t0<90000){await sleep(2500);
    try{snap=await (await api('/llamada/'+encodeURIComponent(s.id)+(Date.now()-t0>10000?'?forzar=1':''))).json()}catch(e){continue}
    if(snap.fase==='terminada')break}
  running=false;$('rehearsal').hidden=true;
  if(snap&&snap.fase==='terminada'){live[s.id]=snap;render();say('Ensayo terminado: '+(snap.resultado||snap.estado_gestion||'sin resultado')+'.');detail(s.id)}
  else{render();say('El ensayo terminó, pero el análisis no llegó. Pulse "Ver" en un momento.',true)}
}
$('rHang').onclick=()=>endRehearsal('');

$('dClose').onclick=()=>{$('detail').hidden=true;delete $('detail').dataset.id;$('dAudio').pause()};

if(key)load().catch(()=>logout());
</script></body></html>"""
