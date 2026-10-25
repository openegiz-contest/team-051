import {apiJSON} from './api.js';
import {FraudControl,ScenarioControl} from './controls.js';
import {value,number,signedNumber,virtualTime,displayNames,stateName,isFresh,lidarMass,MAX_CAPACITY} from './telemetry.js';
import {drawHistory,drawSparkline,LogisticsMap} from './charts.js';
import {navigationLayout,views} from './layout.js';

const $=id=>document.getElementById(id);
const isIframe=window.self!==window.top;
const navigation=navigationLayout(location.search,isIframe);
let view=navigation.view;
document.body.dataset.view=view;
document.body.dataset.embed=String(navigation.embedded);
document.body.dataset.navigation=String(navigation.tabs);
$('view-tabs').hidden=!navigation.tabs;

const control=new FraudControl({input:$('fraud'),reset:$('reset'),label:$('toggle-state'),message:$('command'),mode:$('fraud-mode')});
const scenarios=['plant_erp','sensor_bias','rain'].map(name=>new ScenarioControl({name,
  start:$(`${name}-start`),reset:$(`${name}-reset`),status:$(`${name}-state`),message:$(`${name}-message`)}));
const map=new LogisticsMap($('map'),$('extent'));
let latest=null,scene=null,sceneLoading=null,selected='Stockpile_01',polls=0,stopped=false,sceneFailed=false;

function setView(next,push=false) {
  if(!views.includes(next))next='mine';
  view=next;document.body.dataset.view=view;
  for(const button of document.querySelectorAll('[data-view-button]')) {
    const active=button.dataset.viewButton===view;
    button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));
  }
  for(const link of document.querySelectorAll('[data-nav]'))link.classList.toggle('active',link.dataset.nav===view);
  if(push)window.history.pushState(null,'',`?view=${view}`);
  scene?.setActive(view!=='map');map.setActive(view==='map');
  if(view!=='map')initializeScene();
  if(view==='audit')polls=0;
  requestAnimationFrame(()=>{scene?.resize();if(latest){drawHistory($('chart'),latest.history);map.draw();}});
}
if(navigation.tabs) {
  for(const button of document.querySelectorAll('[data-view-button]'))button.addEventListener('click',()=>setView(button.dataset.viewButton,true));
  for(const link of document.querySelectorAll('[data-nav]'))link.addEventListener('click',event=>{
    if(event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
    event.preventDefault();setView(link.dataset.nav,true);
  });
  window.addEventListener('popstate',()=>setView(navigationLayout(location.search).view));
}

function renderInspector() {
  if(!latest)return;
  $('selection').textContent=displayNames[selected]||selected;
  $('selection-id').textContent=selected;
  let rows;
  if(selected==='Stockpile_01') {
    rows=[['Весовой канал',number(latest.audit.measured_stock)+' т'],['Объем LiDAR',number(latest.audit.scan_volume)+' м³'],['Сухая насыпная плотность','2.7 т/м³'],['Учетный остаток',number(latest.audit.book_stock)+' т']];
    if($('truth-toggle').checked)rows.push(['Эталон симуляции (debug)',number(latest.audit.ground_truth_mass)+' т']);
  }
  else if(/Truck|Wagon/.test(selected))rows=[['Статус',stateName(value(latest,selected,'state'))],['Скорость',number(value(latest,selected,'speed'),0)+' км/ч'],['Груз',number(value(latest,selected,'weight'))+' т'],['Геозона',value(latest,selected,'geofence')==='INSIDE'?'В пределах маршрута':'За пределами маршрута']];
  else if(selected==='Processing_Plant')rows=[['Переработано',number(value(latest,selected,'raw_inflow'))+' т'],['Концентрат',number(value(latest,selected,'concentrate'))+' т'],['Содержание Fe','55%'],['Извлечение','83.5%']];
  else if(selected==='Rail_Siding')rows=[['Отгружено',number(value(latest,selected,'shipped'))+' т'],['Загружено',number(value(latest,selected,'loaded'))+' т'],['Составов отправлено',value(latest,selected,'departures')]];
  else rows=[['Статус',stateName(value(latest,selected,'state'))],['Масса ковша',number(value(latest,selected,'bucket_weight'))+' т'],['Циклов погрузки',value(latest,selected,'passes')]];
  $('asset-details').replaceChildren(...rows.map(([label,text])=>{
    const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;
    const match=String(text).match(/^(.*) (км\/ч|т\/м³|м³|т)$/);
    dd.textContent=match?match[1]:text;
    if(match){const unit=document.createElement('span');unit.className='unit-badge';unit.textContent=match[2];dd.append(unit);}
    row.append(dt,dd);return row;
  }));
}
function select(name) {
  selected=name;scene?.select(name);map.select(name);renderInspector();
  $('inspector').classList.add('revealed');
  for(const row of $('vehicles').children)row.classList.toggle('selected',row.dataset.twin===name);
}
function renderFleet(data) {
  const names=Object.keys(data.twins).filter(name=>/^(Truck|Wagon)_/.test(name));
  $('fleet-count').textContent=names.length+' объектов';
  $('vehicles').replaceChildren(...names.map(name=>{
    const row=document.createElement('button');row.type='button';row.className='vehicle'+(name.startsWith('Wagon')?' wagon':'');row.dataset.twin=name;
    const symbol=document.createElement('span');symbol.className='vehicle-symbol';symbol.textContent=name.startsWith('Truck')?'▱':'▥';
    const detail=document.createElement('span'),title=document.createElement('strong'),state=document.createElement('small');title.textContent=displayNames[name];state.textContent=stateName(value(data,name,'state'))+' · '+number(value(data,name,'speed'),0)+' км/ч';detail.append(title,state);
    const right=document.createElement('span');right.className='vehicle-weight';right.textContent=number(value(data,name,'weight'));
    const unit=document.createElement('small');unit.className='unit-badge';unit.textContent='т';right.append(unit);
    row.append(symbol,detail,right);row.addEventListener('click',()=>select(name));return row;
  }));
}
function render(data) {
  latest={...data,clientReceivedAt:Date.now()};
  const a=data.audit,critical=!!a.anomaly;
  $('physical').textContent=number(a.measured_stock??a.physical_stock);$('book').textContent=number(a.book_stock);
  $('offset').textContent=signedNumber(a.audit_residual??a.fraud_offset);
  $('residual').textContent=number(a.audit_residual??a.physical_residual,2);
  $('percent').textContent=critical?'За пределами порога и погрешности':'В пределах погрешности измерений';
  $('audit-badge').textContent=critical?'Проверить':'Подтверждено';
  $('discrepancy-card').classList.toggle('anomaly',critical);
  $('controls').classList.toggle('critical',critical);$('balance-pill').classList.toggle('critical',critical);
  $('balance-title').textContent=critical?'ANOMALY DETECTED':'BALANCE OK';
  $('balance-subtitle').textContent=a.recommendation||(critical?'Учетный остаток требует проверки':'Физические потоки согласованы');
  $('diagnosis').textContent=a.recommendation||'Ожидание измерений';
  $('sensor-evidence').textContent=`Весы: ${number(a.weight_scale_reading)} т · LiDAR: ${number(a.lidar_dry_stock)} ±${number(a.lidar_uncertainty_t)} т. Возраст измерений: ${number(data.virtual_time-a.scale_timestamp,0)} / ${number(data.virtual_time-a.lidar_timestamp,0)} с модели.`;
  $('lidar-age').textContent=number(Math.max(0,data.virtual_time-value(data,'LiDAR_01','sample_timestamp',a.lidar_timestamp)),0)+' с модели';
  $('lidar-uncertainty').textContent='±'+number(value(data,'LiDAR_01','uncertainty_pct',a.lidar_noise_bound_pct??.5),2)+'%';
  $('stock-range').hidden=lidarMass(data)<=MAX_CAPACITY;
  $('stock-range').textContent=`За диапазоном 3D 0–6000 т: скан ${number(lidarMass(data))} т. Конус ограничен границей шкалы; показания не обрезаны.`;
  $('buffer-progress').value=a.buffer_fill_pct||0;
  $('buffer-label').textContent=`Буфер ${number(a.buffer_fill_pct,0)}% · очередь ${a.truck_queue_length||0} · экскаватор ${a.excavator_interlock?'IDLE':'в работе / ожидании'}`;
  $('rain-evidence').textContent=`Датчик влажности: ${number(value(data,'Moisture_Sensor_01','moisture_pct',a.moisture_pct))}% · ERP: ${number(value(data,'ERP_Register','registered_moisture_pct',a.erp_moisture_pct))}% · ложный сухой остаток: +${number(a.moisture_erp_offset)} т`;
  for(const scenario of scenarios)scenario.render(latest);
  $('balance-pill').querySelector('.status-symbol').textContent=critical?'!':'✓';
  $('status').textContent=a.status;
  $('clock').textContent=virtualTime(data.virtual_time);
  const fresh=isFresh(latest);$('connection').classList.toggle('stale',!fresh);$('connection').dataset.state=fresh?'live':'stale';
  $('connection-text').textContent=fresh?'DITTO LIVE':'Нет свежих данных';
  $('mode').textContent=data.fraud_mode==='drift'?`Накопительный снос ±${number(a.drift_rate_tph??5)} т за виртуальный час. Тревога при расхождении больше 1 т.`:data.fraud_mode==='percent'?'Измените отчет на ±10%. Система обнаружит расхождение в обе стороны.':'Измените отчет на ±300 тонн. Система обнаружит расхождение в обе стороны.';
  const options=$('fraud-mode').options,amount=data.fraud_mode==='drift'?`${number(a.drift_rate_tph??5)} т/ч`:data.fraud_mode==='percent'?'10%':'300 т';
  options[0].textContent=`Приписка в отчете (+${amount})`;options[1].textContent=`Сокрытие остатка (−${amount})`;
  $('compare-physical').textContent=number(a.measured_stock??a.physical_stock)+' т';$('compare-book').textContent=number(a.book_stock)+' т';
  const scale=Math.max(a.book_stock,a.measured_stock??a.physical_stock,1)*1.04;
  $('physical-bar').style.width=(a.measured_stock??a.physical_stock)/scale*100+'%';$('book-bar').style.width=a.book_stock/scale*100+'%';
  const paperDifference=(a.fraud_offset||0)+(a.erp_offset||0)+(a.moisture_erp_offset||0);
  $('ghost-callout').hidden=Math.abs(paperDifference)<.01;$('ghost-mass').textContent=signedNumber(paperDifference)+' т';
  $('ghost-description').textContent=paperDifference<0?'Есть на складе. Скрыто в отчете.':'Есть в отчете. Нет на складе.';
  $('calibration-note').textContent=`Вероятность совпадения ошибок: ${number((a.false_alarm_probability??0)*100,6)}% на скан. Расхождение требует проверки и не доказывает кражу.`;
  for(const [id,thing,field]of [['flow-mined','Stepnoe','mined'],['flow-feed','Processing_Plant','raw_inflow'],['flow-product','Processing_Plant','concentrate'],['flow-shipped','Rail_Siding','shipped']])$(id).textContent=number(value(data,thing,field),0)+' т';
  control.render(latest);renderDirection();renderInspector();renderFleet(data);
  drawSparkline($('spark-physical'),data.history,'measured_stock');drawSparkline($('spark-book'),data.history,'book_stock');
  drawHistory($('chart'),data.history);map.update(data);
  if(scene&&!sceneFailed){scene.update(latest);$('scene-loading').hidden=true;$('scene-stage').dataset.ready='true';}
}
function renderEvents(events) {
  $('events-empty').hidden=events.length>0;
  $('events').replaceChildren(...events.map(event=>{
    const row=document.createElement('tr');
    const title=event.scenario?`${{plant_erp:'Фабрика / ERP',sensor_bias:'Смещение весов',rain:'Дождь / влажность'}[event.scenario]}: ${event.enabled?'запуск':'сброс'}`:event.command<0?'Сокрытие включено':event.command>0?'Приписка включена':'Фрод сброшен';
    const values=[(event.scenario?'S':'#')+String(event.id).padStart(4,'0'),new Date(event.timestamp*1000).toLocaleString('ru-RU',{timeZone:'UTC',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}),title,number(event.measured??event.physical)+' т',number(event.book)+' т',event.source];
    values.forEach((text,index)=>{const cell=document.createElement('td');if(index===2){const badge=document.createElement('span');badge.className=event.command?'event-on':'event-off';badge.textContent=text;cell.append(badge);}else cell.textContent=text;row.append(cell);});
    return row;
  }));
}
function renderDirection() {
  $('fraud-direction').textContent=Number($('fraud-mode').value)<0?'Физический остаток больше учета':'Учет больше физического остатка';
}
$('fraud-mode').addEventListener('change',renderDirection);
$('truth-toggle').addEventListener('change',()=>{
  $('chart').dataset.truth=String($('truth-toggle').checked);
  $('truth-legend').hidden=!$('truth-toggle').checked;
  renderInspector();
  if(latest)drawHistory($('chart'),latest.history);
});
async function poll() {
  if(stopped)return;
  try {
    render(await apiJSON('state'));
    if(polls++%5===0&&view==='audit'){
      const [fraud,scenario]=await Promise.all([apiJSON('audit'),apiJSON('scenarios')]);
      renderEvents([...fraud,...scenario.events].sort((a,b)=>b.timestamp-a.timestamp).slice(0,100));
    }
  } catch(error) {
    $('connection').classList.add('stale');$('connection').dataset.state='stale';$('connection-text').textContent='Связь прервана';
    control.unavailable(error.message);
    for(const scenario of scenarios)scenario.render({mqtt_connected:false});
    if(latest){latest={...latest,mqtt_connected:false};scene?.update(latest);}
  } finally {if(!stopped)setTimeout(poll,1000);}
}
async function initializeScene() {
  if(view==='map'||scene||sceneFailed)return;
  if(sceneLoading)return sceneLoading;
  sceneLoading=(async()=>{try {
    const {MineScene}=await import('./scene.js');
    scene=new MineScene({canvas:$('scene'),stage:$('scene-stage'),labels:$('scene-labels'),tooltip:$('object-tooltip'),onSelect:name=>{selected=name;renderInspector();map.select(name);$('inspector').classList.add('revealed');}});
    if(latest){scene.update(latest);$('scene-loading').hidden=true;$('scene-stage').dataset.ready='true';}
    scene.setActive(view!=='map');
    $('camera-reset').addEventListener('click',()=>scene.resetView());
    $('camera-stock').addEventListener('click',()=>scene.focusStock());
  } catch(error) {
    sceneFailed=true;
    $('scene-loading').classList.add('error');
    $('scene-loading').textContent='3D недоступен: нужен браузер с WebGL 2. Показатели и управление сценарием продолжают работать.';
    console.error('Stepnoe 3D:',error);
  }})();
  try {await sceneLoading;}finally{sceneLoading=null;}
}
$('scene-stage').addEventListener('scene-error',event=>{sceneFailed=true;$('scene-loading').hidden=false;$('scene-loading').textContent=event.detail;$('scene-loading').classList.add('error');});
$('fullscreen').addEventListener('click',async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await $('mine').requestFullscreen();}catch{control.setMessage('Браузер не разрешил полноэкранный режим.');}});
window.addEventListener('resize',()=>{if(latest){drawHistory($('chart'),latest.history);map.draw();}});
const freshnessTimer=setInterval(()=>{if(latest&&!isFresh(latest)){$('connection').classList.add('stale');$('connection-text').textContent='Нет свежих данных';control.render(latest);}},1000);
window.addEventListener('pagehide',()=>{stopped=true;clearInterval(freshnessTimer);scene?.dispose();map.dispose();},{once:true});
setView(view);poll();
