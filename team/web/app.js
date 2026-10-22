'use strict';
const $ = id => document.getElementById(id);
const view = new URLSearchParams(location.search).get('view') || 'all';
document.body.dataset.view = view;
const number = v => Number(v).toLocaleString('ru-RU', {maximumFractionDigits: 2});
const val = (thing, field) => latest?.twins[thing]?.[field]?.properties.value ?? 0;
let latest, pending = false, desired = null, commandDeadline = 0, eventsTick = 0;
const trails = {}, colors = ['#66dfcc', '#eab56e', '#99bdf4', '#be9ded', '#e9a7b3', '#a8d19b', '#c8c1a0'];
function setupCanvas(id) {
  const canvas = $(id), ctx = canvas.getContext('2d');
  const w = canvas.clientWidth, h = canvas.clientHeight, dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(w*dpr) || canvas.height !== Math.round(h*dpr)) {canvas.width = Math.round(w*dpr);canvas.height = Math.round(h*dpr);}
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);ctx.clearRect(0, 0, w, h);
  return {canvas, ctx, w, h};
}
function update(data) {
  latest = data;
  const a = data.audit;
  $('physical').textContent = number(a.physical_stock);
  $('book').textContent = number(a.book_stock);
  $('offset').textContent = (a.fraud_offset ? '+' : '') + number(a.fraud_offset);
  $('offset').style.color = a.anomaly ? '#ff798b' : '#66dfcc';
  $('residual').textContent = number(a.physical_residual);
  $('percent').textContent = `${number(a.discrepancy_pct)}% неподтвержденного остатка`;
  $('status').textContent = a.status + (a.anomaly ? ` (+${number(a.discrepancy_pct)}% Unverified Ore)` : '');
  $('status').className = 'status' + (a.anomaly ? ' critical' : '');
  $('mode').textContent = data.fraud_mode === 'fixed' ? 'Приписка +300 т в учетном отчете' : 'Приписка +10% к физическому остатку';
  const age = data.telemetry_age;
  $('connection').textContent = data.mqtt_connected && age < 15 ? `● DITTO LIVE · ревизия ${data.revision}` : '● НЕТ СВЕЖЕЙ ТЕЛЕМЕТРИИ';
  $('connection').style.color = data.mqtt_connected && age < 15 ? '#66dfcc' : '#ff798b';
  $('clock').textContent = `${Math.floor(data.virtual_time / 3600)} ч ${Math.floor(data.virtual_time % 3600 / 60)} мин`;
  $('geometry').textContent = `V = ${number(a.scan_volume)} м³ · h = ${number(a.height)} м · ρ = 2.7 т/м³`;
  if (pending && data.fraud === desired) {pending = false;desired = null;$('command').textContent = 'Команда применена и записана в журнал.';}
  if (pending && Date.now() > commandDeadline) {pending = false;desired = null;$('command').textContent = 'Подтверждение не получено. Проверьте MQTT и текущее состояние.';}
  if (!pending) $('fraud').checked = data.fraud;
  $('fraud').disabled = pending || !data.mqtt_connected || age > 15;
  $('reset').disabled = $('fraud').disabled;
  $('toggle-state').textContent = pending ? 'ОЖИДАНИЕ' : data.fraud ? 'ON' : 'OFF';
  const vehicles = Object.keys(data.twins).filter(k => /^(Truck|Wagon)_/.test(k));
  for (const name of vehicles) {
    const p = [val(name,'lat'),val(name,'lon')], line = trails[name] ||= [];
    if (!line.length || line.at(-1).some((v,i) => v !== p[i])) line.push(p);
    if (line.length > 900) line.shift();
  }
  $('vehicles').replaceChildren(...vehicles.map(name => {
    const item = document.createElement('div');item.className = 'vehicle';
    const strong = document.createElement('strong');strong.textContent = name;
    item.append(strong, document.createTextNode(` · ${val(name,'state')} · ${number(val(name,'weight'))} т · ${number(val(name,'speed'))} км/ч · ${val(name,'geofence')}`));
    return item;
  }));
  if (view === 'all' || view === 'map') drawMap();
  if (view === 'all') drawChart();
}
async function refresh() {
  try {
    const response = await fetch('api/state', {cache:'no-store'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    update(await response.json());
    if (++eventsTick % 5 === 1 && (view === 'all' || view === 'audit')) {
      const audit = await fetch('api/audit', {cache:'no-store'});
      if (!audit.ok) throw new Error(`HTTP ${audit.status}`);
      $('events').replaceChildren(...(await audit.json()).map(e => {
        const row = document.createElement('tr');
        for (const value of [e.id,new Date(e.timestamp*1000).toISOString().replace('T',' ').slice(0,19),e.command ? 'ON' : 'OFF',number(e.physical),number(e.book),e.source]) {
          const cell = document.createElement('td');cell.textContent = value;row.append(cell);
        }
        return row;
      }));
    }
  } catch (error) {
    $('connection').textContent = '● СВЯЗЬ ПРЕРВАНА';$('connection').style.color='#ff798b';
    $('fraud').disabled = true;$('reset').disabled = true;
    $('command').textContent = 'Нет свежих данных: ' + error.message;
  } finally {setTimeout(refresh,1000);}
}
async function command(value) {
  if (pending) return;
  pending = true;desired = !!value;commandDeadline=Date.now()+15000;
  $('fraud').disabled = true;$('reset').disabled = true;
  $('command').textContent = 'Отправка команды по MQTT…';
  try {
    const r = await fetch('api/fraud', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({value})});
    if (!r.ok) throw new Error((await r.json()).error || String(r.status));
    $('command').textContent = 'MQTT подтвердил прием. Ожидание пересчета баланса…';
  } catch (e) {pending=false;desired=null;$('command').textContent='Ошибка: '+e.message;}
}
$('fraud').addEventListener('change', e => command(e.target.checked ? 1 : 0));
$('reset').addEventListener('click', () => command(0));

// Local dependency-free 3D renderer. Project and depth-sort a triangle mesh.
let yaw = -.6, pitch = .65, zoom = 1, drag, dragged = false, labels = [];
const scene = $('scene');
scene.addEventListener('pointerdown', e => {drag=[e.clientX,e.clientY];dragged=false;scene.setPointerCapture(e.pointerId);});
scene.addEventListener('pointermove', e => {if (!drag) return;const dx=e.clientX-drag[0],dy=e.clientY-drag[1];if(Math.abs(dx)+Math.abs(dy)>2)dragged=true;yaw+=dx*.007;pitch=Math.max(.25,Math.min(1.25,pitch+dy*.005));drag=[e.clientX,e.clientY];});
scene.addEventListener('pointerup', e => {drag=null;if(dragged)return;const r=scene.getBoundingClientRect();const target=labels.find(p=>Math.hypot(p.x-(e.clientX-r.left),p.y-(e.clientY-r.top))<65);if(target)$('selection').textContent=target.name+' · '+target.info;});
scene.addEventListener('pointercancel', () => {drag=null;});
scene.addEventListener('wheel', e => {e.preventDefault();zoom=Math.max(.6,Math.min(2,zoom-e.deltaY*.001));},{passive:false});
function drawScene() {
  if (!latest || (view !== 'all' && view !== 'mine')) {requestAnimationFrame(drawScene);return;}
  const {ctx,w,h}=setupCanvas('scene'), faces=[], annotations=[];
  const cy=Math.cos(yaw), sy=Math.sin(yaw), cp=Math.cos(pitch), sp=Math.sin(pitch);
  const project=([x,y,z])=>{const rx=x*cy-z*sy, rz=x*sy+z*cy;return {x:w/2+rx*zoom*w/420,y:h*.55+(-y*cp+rz*sp)*zoom*w/420,depth:y*sp+rz*cp};};
  const poly=(points,color)=>{const pts=points.map(project);faces.push({pts,color,d:pts.reduce((a,p)=>a+p.depth,0)/pts.length});};
  const line=(a,b,color,width=1)=>{const p=project(a),q=project(b);ctx.strokeStyle=color;ctx.lineWidth=width;ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.stroke();};
  const box=(x,z,sx,syy,sz,color)=>{
    const a=[x-sx/2,0,z-sz/2],b=[x+sx/2,0,z-sz/2],c=[x+sx/2,0,z+sz/2],d=[x-sx/2,0,z+sz/2];
    const top=p=>[p[0],syy,p[2]];
    poly([a,b,top(b),top(a)],color);poly([b,c,top(c),top(b)],color);poly([c,d,top(d),top(c)],color);poly([d,a,top(a),top(d)],color);poly([top(a),top(b),top(c),top(d)],color);
  };
  for(let i=-160;i<=160;i+=20){line([i,0,-110],[i,0,110],'#203847');line([-160,0,i*.6875],[160,0,i*.6875],'#203847');}
  line([-120,.2,-50],[0,.2,-15],'#688075',5);line([0,.2,-15],[80,.2,45],'#688075',5);line([100,.2,55],[150,.2,100],'#9ea9aa',3);
  box(-120,-50,38,4,32,'#546771');box(-123,-51,12,13,9,'#b99e66');
  box(0,-15,35,21,25,'#466577');box(12,-27,10,36,10,'#668391');
  box(108,55,10,5,60,'#45555c');
  const cx=66,cz=30,height=latest.audit.height;
  for(let i=0;i<40;i++) {
    const a=i/40*Math.PI*2,b=(i+1)/40*Math.PI*2;
    const base=t=>[cx+25*Math.cos(t),0,cz+15*Math.sin(t)];
    const shade=Math.floor(75+35*Math.cos(a+.6));
    poly([base(a),base(b),[cx,height,cz]],`rgb(${shade},${shade+55},${shade+48})`);
    if(latest.fraud){const extra=height*(latest.audit.book_stock/latest.audit.physical_stock-1);poly([[cx,height,cz],[cx+25*Math.cos(a),extra,cz+15*Math.sin(a)],[cx+25*Math.cos(b),extra,cz+15*Math.sin(b)]],'rgba(255,90,120,.2)');}
  }
  const truckPos=name=>{const f=(val(name,'lat')-52.10)/.02;return [-120+120*f,-50+35*f];};
  for(const name of ['Truck_01','Truck_02']){const [x,z]=truckPos(name);box(x,z,11,6,7,'#d2ad68');if(val(name,'weight')>0)box(x,z,7,8,5,'#857c65');annotations.push({name,world:[x,12,z],info:`${val(name,'state')} / ${number(val(name,'weight'))} т`});}
  for(let i=0;i<5;i++)box(108,36+i*10,7,5,8,latest.twins.Wagon_01.state.properties.value==='loading'?'#7c9cac':'#33434f');
  faces.sort((a,b)=>b.d-a.d);
  for(const f of faces){ctx.beginPath();f.pts.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.closePath();ctx.fillStyle=f.color;ctx.fill();ctx.strokeStyle='#ffffff15';ctx.lineWidth=.7;ctx.stroke();}
  const sweep=(Date.now()/1500)%(2*Math.PI);line([cx,height+22,cz],[cx+25*Math.cos(sweep),0,cz+15*Math.sin(sweep)],'#70ffe2',1.5);
  annotations.push({name:'Excavator_01',world:[-120,21,-50],info:'Ковш 22.5 т / синтетические весы'}, {name:'Processing_Plant',world:[0,43,-15],info:'Fe 43–45% → 55% / извлечение 83.5%'},{name:'Stockpile_01',world:[cx,height+12,cz],info:`${number(latest.audit.physical_stock)} т / ${number(latest.audit.scan_volume)} м³`},{name:'Rail_Siding',world:[108,12,65],info:`Отгружено ${number(val('Rail_Siding','shipped'))} т`});
  labels=annotations.map(a=>({...project(a.world),...a}));ctx.font='10px system-ui';ctx.textAlign='center';
  for(const l of labels){ctx.fillStyle='#c8e5e9';ctx.fillText(l.name,l.x,l.y);}
  requestAnimationFrame(drawScene);
}
function drawChart(){
  const {ctx,w,h}=setupCanvas('chart'),rows=latest.history;if(!rows.length)return;
  const lo=Math.min(...rows.map(r=>r.physical_stock))*.98,hi=Math.max(...rows.map(r=>r.book_stock))*1.02;
  ctx.font='10px system-ui';ctx.fillStyle='#91a8b6';ctx.textAlign='left';
  for(let i=0;i<4;i++){const y=20+i*(h-45)/3;ctx.strokeStyle='#243443';ctx.beginPath();ctx.moveTo(60,y);ctx.lineTo(w-20,y);ctx.stroke();ctx.fillText(number(hi-(hi-lo)*i/3),8,y+4);}
  for(const [key,color] of [['physical_stock','#66dfcc'],['book_stock','#ff798b']]){ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();rows.forEach((r,i)=>{const x=60+i/Math.max(1,rows.length-1)*(w-80),y=20+(hi-r[key])/Math.max(1,hi-lo)*(h-45);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.stroke();}
}
function drawMap(){
  if(!latest)return;const {ctx,w,h}=setupCanvas('map'),rail=$('extent').value==='rail';
  const bounds=rail?[52.105,52.72,63.10,63.17]:[52.095,52.125,63.09,63.145];
  const xscale=(w-90)/((bounds[3]-bounds[2])*Math.cos(52.1*Math.PI/180)),yscale=(h-70)/(bounds[1]-bounds[0]),scale=Math.min(xscale,yscale);
  const project=p=>[w/2+(p[1]-(bounds[2]+bounds[3])/2)*Math.cos(52.1*Math.PI/180)*scale,h/2-(p[0]-(bounds[0]+bounds[1])/2)*scale];
  const route=rail?[[52.12,63.13],[52.12+65/111.32,63.132]]:[[52.1,63.1],[52.12,63.13]];
  ctx.font='10px system-ui';ctx.fillStyle='#91a8b6';
  const delta=rail?.1:.005;
  for(let lat=Math.ceil(bounds[0]/delta)*delta;lat<bounds[1];lat+=delta){const y=project([lat,bounds[2]])[1];ctx.strokeStyle='#233947';ctx.beginPath();ctx.moveTo(40,y);ctx.lineTo(w-10,y);ctx.stroke();ctx.fillText(lat.toFixed(3)+'°N',4,y-4);}
  const [a,b]=route.map(project);ctx.lineCap='round';ctx.strokeStyle='#265955';ctx.lineWidth=300/111320*scale;ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();ctx.strokeStyle='#799c9c';ctx.lineWidth=2;ctx.setLineDash([6,5]);ctx.stroke();ctx.setLineDash([]);
  route.forEach((p,i)=>{const [x,y]=project(p);ctx.fillStyle='#a9bcc6';ctx.fillText((rail?['Весовая','Разгрузка 65 км']:['Забой','Фабрика'])[i],x+12,y-12);});
  let ci=0;for(const [name,trail]of Object.entries(trails)){if(name.startsWith('Wagon')!==rail)continue;const color=colors[ci++%colors.length];ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.beginPath();trail.forEach((p,i)=>{const q=project(p);i?ctx.lineTo(...q):ctx.moveTo(...q);});ctx.stroke();const [x,y]=project([val(name,'lat'),val(name,'lon')]);ctx.fillStyle=val(name,'geofence')==='INSIDE'?color:'#ff4064';ctx.beginPath();ctx.arc(x,y,5,0,Math.PI*2);ctx.fill();ctx.fillText(name,x+10,y+12+ci*3);}
}
$('extent').addEventListener('change',drawMap);
window.addEventListener('resize',()=>{if(latest){if(view==='all'||view==='map')drawMap();if(view==='all')drawChart();}});
refresh();requestAnimationFrame(drawScene);
