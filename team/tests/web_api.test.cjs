// Run with Node 18+: node --test team/tests/web_api.test.cjs
const assert=require('node:assert/strict');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const test=require('node:test');
const moduleURL=name=>pathToFileURL(path.resolve(__dirname,'../web',name)).href;
const modules=Promise.all([import(moduleURL('api.js')),import(moduleURL('controls.js')),import(moduleURL('telemetry.js'))]);

test('cascade controls retain CORS POST and wait for the named Ditto scenario',async t=>{
  const {setScenario}=await import(moduleURL('api.js'));
  const {ScenarioControl}=await import(moduleURL('controls.js'));
  const original=global.fetch,calls=[];
  global.fetch=async(url,options)=>{calls.push({url,options});return Response.json({accepted:1},{status:202});};
  t.after(()=>{global.fetch=original;});
  const element=()=>({disabled:false,textContent:'',dataset:{},addEventListener(){}});
  const start=element(),reset=element(),status=element(),message=element();
  const control=new ScenarioControl({name:'plant_erp',start,reset,status,message,send:setScenario});
  control.render({mqtt_connected:true,telemetry_age:0,audit:{scenario_plant_erp:0}});
  await control.command(1);
  assert.equal(calls[0].url,'/stepnoe/api/scenarios');
  assert.equal(calls[0].options.method,'POST');assert.equal(calls[0].options.credentials,'same-origin');
  assert.equal(calls[0].options.mode,'cors');assert.equal(calls[0].options.headers['Content-Type'],'application/json');
  assert.deepEqual(JSON.parse(calls[0].options.body),{scenario:'plant_erp',value:1});
  assert.equal(control.pending,true);
  control.render({mqtt_connected:true,telemetry_age:0,audit:{scenario_plant_erp:1}});
  assert.equal(control.pending,false);assert.equal(reset.disabled,false);assert.equal(start.disabled,true);
});

test('chart series distinguish measured LiDAR, ERP and optional simulation truth',async()=>{
  const {historySeries}=await import(moduleURL('charts.js'));
  assert.deepEqual(historySeries().map(s=>s.key),['lidar_dry_stock','book_stock']);
  assert.deepEqual(historySeries(true).map(s=>s.key),['lidar_dry_stock','book_stock','ground_truth_mass']);
});

async function controlFor(t,response) {
  const [{apiJSON},{FraudControl}]=await modules;
  const calls=[],original=global.fetch;
  global.fetch=async(url,options)=>{calls.push({url,options});return response();};
  t.after(()=>{global.fetch=original;});
  const element=()=>({checked:false,disabled:false,textContent:'',classList:{toggle(){}},addEventListener(){}});
  const input=element(),reset=element(),label=element(),message=element(),mode={...element(),value:'1'};
  const control=new FraudControl({input,reset,label,message,mode});
  control.render({fraud:false,mqtt_connected:true,telemetry_age:0});
  return {apiJSON,control,input,reset,label,message,mode,calls};
}

test('standalone and embedded UI use the unchanged CORS POST contract',async t=>{
  const ui=await controlFor(t,()=>Response.json({accepted:1},{status:202}));
  await ui.control.command(1);
  const {url,options}=ui.calls[0];
  assert.equal(url,'/stepnoe/api/fraud');assert.equal(options.method,'POST');
  assert.equal(options.credentials,'same-origin');assert.equal(options.mode,'cors');
  assert.equal(options.redirect,'manual');assert.equal(options.headers['Content-Type'],'application/json');
  assert.deepEqual(JSON.parse(options.body),{value:1});
  assert.equal(ui.control.pending,true); // MQTT ACK is not Ditto confirmation.
  ui.control.render({fraud:true,mqtt_connected:true,telemetry_age:0});
  assert.equal(ui.control.pending,false);assert.equal(ui.input.checked,true);
  assert.match(ui.message.textContent,/подтверждено телеметрией/);
});
test('reset publishes integer zero to the same endpoint',async t=>{
  const ui=await controlFor(t,()=>Response.json({accepted:0},{status:202}));
  await ui.control.command(0);
  assert.deepEqual(JSON.parse(ui.calls[0].options.body),{value:0});
});
test('changing the fraud direction waits for the matching signed Ditto state',async t=>{
  const ui=await controlFor(t,()=>Response.json({accepted:-1},{status:202}));
  ui.control.render({fraud:true,audit:{fraud_offset:300},mqtt_connected:true,telemetry_age:0});
  ui.mode.value='-1';await ui.control.command(-1);
  assert.deepEqual(JSON.parse(ui.calls[0].options.body),{value:-1});
  assert.equal(ui.mode.disabled,true);
  ui.control.render({fraud:true,audit:{fraud_offset:300},mqtt_connected:true,telemetry_age:0});
  assert.equal(ui.control.pending,true);
  ui.control.render({fraud:true,audit:{fraud_offset:-300},mqtt_connected:true,telemetry_age:0});
  assert.equal(ui.control.pending,false);assert.equal(ui.input.checked,true);assert.equal(ui.mode.value,'-1');
});
test('failed direction changes restore the last confirmed selection',async t=>{
  const ui=await controlFor(t,()=>Response.json({error:'MQTT unavailable'},{status:503}));
  ui.control.render({fraud:true,audit:{fraud_offset:300},mqtt_connected:true,telemetry_age:0});
  ui.mode.value='-1';await ui.control.command(-1);
  assert.equal(ui.mode.value,'1');assert.equal(ui.input.checked,true);
});
test('HTML fallback is reported without JSON parse failure and rolls the toggle back',async t=>{
  const ui=await controlFor(t,()=>new Response('<html>Grafana login</html>',{headers:{'Content-Type':'text/html'}}));
  ui.input.checked=true;await ui.control.command(1);
  assert.match(ui.message.textContent,/ожидался JSON/);assert.doesNotMatch(ui.message.textContent,/Unexpected token/);
  assert.equal(ui.input.checked,false);assert.equal(ui.input.disabled,false);assert.equal(ui.label.textContent,'OFF');
});
test('login failures and redirects remain readable',async t=>{
  const ui=await controlFor(t,()=>({status:0,type:'opaqueredirect'}));
  await ui.control.command(1);assert.match(ui.message.textContent,/Войдите в Grafana/);
});
test('backend JSON failures preserve the useful reason',async t=>{
  const ui=await controlFor(t,()=>Response.json({error:'MQTT/model not ready'},{status:503}));
  await ui.control.command(1);assert.match(ui.message.textContent,/MQTT\/model not ready/);
});
test('stale telemetry disables the control',async t=>{
  const ui=await controlFor(t,()=>Response.json({}));
  ui.control.render({fraud:false,mqtt_connected:true,telemetry_age:20});
  assert.equal(ui.input.disabled,true);assert.equal(ui.reset.disabled,true);
});
test('motion damping is independent of display refresh rate',async()=>{
  const [,,{interpolationAlpha}]=await modules;
  assert.ok(Math.abs(interpolationAlpha(1/60)-.04)<1e-12);
  const integrate=fps=>{let position=0;for(let i=0;i<fps;i++)position+=(1-position)*interpolationAlpha(1/fps);return position;};
  assert.ok(Math.abs(integrate(30)-integrate(60))<1e-10);
});
test('virtual time and signed values retain their meaning',async()=>{
  const [,,{virtualTime,signedNumber}]=await modules;
  assert.equal(virtualTime(8040),'2 ч 14 мин');assert.equal(virtualTime(0),'0 ч 0 мин');
  assert.equal(virtualTime(undefined),'—');assert.equal(signedNumber(-300),'-300');assert.equal(signedNumber(300),'+300');
});
test('physical and ghost heights preserve the mass-to-volume contract',async()=>{
  const [,,{stockHeight}]=await modules;
  assert.ok(Math.abs(Math.PI*25*15*stockHeight(3000)/3*2.7-3000)<1e-9);
  assert.ok(Math.abs(stockHeight(3300)/stockHeight(3000)-1.1)<1e-12);
});
test('fleet models contain component geometry with shadow support',async()=>{
  const {createTruck,createExcavator,createPlant,createWagon,createLocomotive}=await import(moduleURL('models.js'));
  for(const factory of [createTruck,createExcavator,createPlant,createWagon,createLocomotive]){
    const group=factory();let count=0;
    assert.equal(group.isGroup,true);
    group.traverse(node=>{if(node.isMesh){count++;assert.equal(node.castShadow,true);assert.equal(node.receiveShadow,true);}});
    assert.ok(count>=10,factory.name+' must be a composite model');
  }
});
test('stacker discharge stays centered above the physical cone',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createConveyor}=await import(moduleURL('models.js'));
  const {mineLayout}=await import(moduleURL('layout.js'));
  const conveyor=createConveyor(new THREE.Scene());
  for(const height of [0,3,8.3]){
    conveyor.setDropHeight(height);
    assert.equal(conveyor.end.x,mineLayout.stock[0]);assert.equal(conveyor.end.z,mineLayout.stock[2]);
    assert.ok(conveyor.end.y>mineLayout.stock[1]+height);
    assert.equal(conveyor.railDrop.x,mineLayout.rail[0]);
    assert.equal(conveyor.railDrop.z,mineLayout.rail[2]);
    assert.equal(conveyor.railDrop.y,10);
    assert.ok(conveyor.path.getPointAt(0).distanceTo(conveyor.start)<1e-9);
    assert.ok(conveyor.path.getPointAt(1).distanceTo(conveyor.railDrop)<1e-9);
  }
});
test('telemetry changes only equipment targets and the loading train stays fixed',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {updateEquipmentTarget,advanceEquipment}=await import(moduleURL('motion.js'));
  const {createHaulRoad,wrapProgress}=await import(moduleURL('haul-road.js'));
  const {mineLayout,wagonPosition}=await import(moduleURL('layout.js'));
  const entries=new Map();
  const register=(name,position)=>{
    const object=new THREE.Group();object.position.set(...position);
    entries.set(name,{object,targetPosition:object.position.clone(),targetQuaternion:object.quaternion.clone()});
  };
  register('Excavator_01',mineLayout.excavator);register('Truck_01',[0,0,0]);register('Rail_Siding',mineLayout.rail);
  for(let i=0;i<5;i++)register(`Wagon_0${i+1}`,wagonPosition(i));
  const road=createHaulRoad();
  const feature=n=>({properties:{value:n}});
  const snapshot=(lat,lon)=>({twins:{
    Truck_01:{lat:feature(lat),lon:feature(lon),state:feature('hauling')},
    Excavator_01:{lat:feature(lat),lon:feature(lon),heading:feature(90)},
    ...Object.fromEntries(Array.from({length:5},(_,i)=>[`Wagon_0${i+1}`,{lat:feature(lat+1),weight:feature(70)}]))
  }});
  const update=data=>{for(const [name,entry]of entries)updateEquipmentTarget(name,entry,data,road);};
  update(snapshot(52.10,63.10));
  for(const entry of entries.values()){entry.object.position.copy(entry.targetPosition);entry.object.quaternion.copy(entry.targetQuaternion);}
  const transforms=()=>JSON.stringify([...entries].filter(([name])=>/Wagon|Rail/.test(name)).map(([,e])=>[e.object.position.toArray(),e.object.quaternion.toArray()]));
  const fixed=transforms(),truck=entries.get('Truck_01'),excavator=entries.get('Excavator_01');
  const before=truck.object.position.clone(),excavatorBefore=excavator.object.position.clone();
  update(snapshot(52.12,63.13));
  assert.deepEqual(truck.object.position.toArray(),before.toArray());
  assert.deepEqual(excavator.object.position.toArray(),excavatorBefore.toArray());
  const frame=()=>{for(const [name,entry]of entries)advanceEquipment(name,entry,1/60);};
  frame();
  assert.ok(truck.object.position.distanceTo(road.curve.getPointAt(wrapProgress(truck.motion.progress)))<1e-10);
  assert.ok(truck.object.position.distanceTo(before)<18/60);
  assert.ok(excavator.object.position.distanceTo(excavatorBefore)>0);
  for(let i=0;i<100;i++)frame();
  assert.equal(transforms(),fixed);
});
test('loop lanes, arcs and headings join continuously at all four boundaries',async()=>{
  const {loopPoint}=await import(moduleURL('routes.js'));
  assert.ok(loopPoint(.5).y<0);assert.ok(loopPoint(2.5).y>0);
  assert.ok(loopPoint(1.5).x>1);assert.ok(loopPoint(3.5).x<0);
  for(const boundary of [0,1,2,3,4]){
    const before=loopPoint(boundary-1e-7),after=loopPoint(boundary+1e-7);
    assert.ok(Math.hypot(before.x-after.x,before.y-after.y)<1e-6);
    assert.ok(before.dx*after.dx+before.dy*after.dy>.999999);
  }
  assert.deepEqual(loopPoint(4.5),loopPoint(.5));
  const queue=loopPoint(3,1,.15,1),loading=loopPoint(3);
  assert.ok(Math.hypot(queue.x-loading.x,queue.y-loading.y)>.15);
});
test('truck phases stay monotonic through a complete cycle and duplicate GPS',async()=>{
  const {TruckRoute}=await import(moduleURL('routes.js'));
  const route=new TruckRoute(),f=n=>({properties:{value:n}});
  const update=(time,state,p,weight,trips)=>{
    route.update({virtual_time:time,twins:{Truck_01:{state:f(state),lat:f(52.1+p*.02),lon:f(63.1+p*.03),weight:f(weight),trips:f(trips)}}},'Truck_01');
    return route.targetPhase;
  };
  const targets=[update(90,'loading',0,67.5,0),update(120,'hauling',0,90,0),update(300,'hauling',.5,90,0)];
  const before=route.targetPhase;update(301,'hauling',.49999,90,0);assert.equal(route.targetPhase,before);
  targets.push(update(480,'unloading',1,90,0),update(500,'unloading',1,90,0),update(520,'returning',1,0,1),update(880,'returning',0,0,1),update(900,'loading',0,0,1),update(990,'loading',0,67.5,1),update(1000,'hauling',0,90,1));
  assert.equal(targets.at(-1),4);
  assert.ok(targets.every((v,i)=>!i||v>=targets[i-1]));
  assert.equal(route.phase,targets[0],'snapshots must not overwrite the displayed phase');
  route.advance(1/60);assert.ok(route.phase>targets[0]&&route.phase<route.targetPhase);
});
test('exact end-of-loading snapshot does not add an extra circuit',async()=>{
  const {TruckRoute}=await import(moduleURL('routes.js'));
  const f=n=>({properties:{value:n}}),route=new TruckRoute();
  route.update({twins:{Truck_01:{state:f('loading'),weight:f(90)}}},'Truck_01');
  assert.equal(route.targetPhase,4);
  route.update({twins:{Truck_01:{state:f('hauling'),lat:f(52.1),lon:f(63.1)}}},'Truck_01');
  assert.equal(route.targetPhase,4);
});
test('excavator quaternion smoothing takes the short turn across the angle seam',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {advanceEquipment}=await import(moduleURL('motion.js'));
  const object=new THREE.Object3D(),axis=new THREE.Vector3(0,1,0);
  object.quaternion.setFromAxisAngle(axis,Math.PI-.01);
  const initial=object.quaternion.clone(),targetQuaternion=new THREE.Quaternion().setFromAxisAngle(axis,-Math.PI+.01);
  advanceEquipment('Excavator_01',{object,targetPosition:object.position.clone(),targetQuaternion},1/60);
  assert.ok(Math.abs(initial.angleTo(object.quaternion)-.02*.04)<1e-8);
});
test('navigation stays visible for standalone view URLs and is hidden only in frames',async()=>{
  const {navigationLayout}=await import(moduleURL('layout.js'));
  assert.deepEqual(navigationLayout(''),{view:'mine',tabs:true,embedded:false});
  for(const view of ['mine','map','audit']) {
    assert.deepEqual(navigationLayout(`?view=${view}`),{view,tabs:true,embedded:false});
    assert.deepEqual(navigationLayout(`?view=${view}`,true),{view,tabs:false,embedded:true});
  }
  assert.deepEqual(navigationLayout('?view=audit&full=1'),{view:'audit',tabs:true,embedded:false});
  assert.deepEqual(navigationLayout('?view=audit&full=1',true),{view:'audit',tabs:false,embedded:true});
  assert.deepEqual(navigationLayout('',true),{view:'mine',tabs:false,embedded:true});
  assert.deepEqual(navigationLayout('?view=unknown'),{view:'mine',tabs:true,embedded:false});
});
test('3D truck progress remains continuous through snapshots, turns and loop seams',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createHaulRoad,wrapProgress}=await import(moduleURL('haul-road.js'));
  const {updateEquipmentTarget,advanceEquipment}=await import(moduleURL('motion.js'));
  const road=createHaulRoad(),object=new THREE.Group();
  const entry={object,targetPosition:new THREE.Vector3(),targetQuaternion:new THREE.Quaternion()};
  const f=n=>({properties:{value:n}});
  const snapshot=(state,trips,p=0)=>({virtual_time:trips*940,twins:{Truck_01:{state:f(state),trips:f(trips),lat:f(52.1+p*.02),lon:f(63.1+p*.03),weight:f(state==='loading'?67.5:90)}}});
  updateEquipmentTarget('Truck_01',entry,snapshot('hauling',0),road);
  object.position.copy(entry.targetPosition);
  object.lookAt(road.curve.getPointAt(wrapProgress(entry.motion.progress+.005)));
  for(const [state,trips,p]of [['unloading',0,1],['returning',1,1],['loading',1,0],['hauling',2,0]]){
    const before=object.position.clone(),progress=entry.motion.progress;
    updateEquipmentTarget('Truck_01',entry,snapshot(state,trips,p),road);
    assert.equal(entry.motion.progress,progress,'state changes must not reset t');
    assert.deepEqual(object.position.toArray(),before.toArray());
  }
  let previous=entry.motion.progress,seams=0;
  for(let i=0;i<2000;i++){
    const before=object.position.clone(),rotation=object.quaternion.clone();
    advanceEquipment('Truck_01',entry,1/60);
    assert.ok(entry.motion.progress>=previous);
    if(Math.floor(previous)!==Math.floor(entry.motion.progress))seams++;
    assert.ok(before.distanceTo(object.position)<=18/60+.001,'a packet must not cause a spatial jump');
    assert.ok(rotation.angleTo(object.quaternion)<.25,'heading must remain continuous');
    assert.ok(object.position.distanceTo(road.curve.getPointAt(wrapProgress(entry.motion.progress)))<1e-9);
    previous=entry.motion.progress;
  }
  assert.ok(seams>=1,'the regression must exercise the 1 -> 0 seam');
});
test('excavated ramp stays below the driven road and has a monotonic uphill grade',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createHaulRoad}=await import(moduleURL('haul-road.js'));
  const {createTerrain}=await import(moduleURL('models.js'));
  const road=createHaulRoad(),terrain=createTerrain(new THREE.Scene(),road),ray=new THREE.Raycaster();
  terrain.updateMatrixWorld(true);
  let previous=-5;
  for(let i=0;i<=100;i++){
    const p=road.centerline.getPointAt(i/100);
    assert.ok(p.y>=previous-.001);previous=p.y;
  }
  const {haulLayout}=await import(moduleURL('layout.js'));
  for(let i=1;i<499;i++){
    const a=road.centerline.getPointAt((i-1)/500),b=road.centerline.getPointAt(i/500),c=road.centerline.getPointAt((i+1)/500);
    const u=b.clone().sub(a).setY(0),v=c.clone().sub(b).setY(0),angle=u.angleTo(v);
    if(angle>1e-6)assert.ok((u.length()+v.length())/2/angle>haulLayout.halfWidth,'inner road edge must not fold over itself');
  }
  for(let i=0;i<100;i++){
    const p=road.curve.getPointAt(i/100);
    ray.set(new THREE.Vector3(p.x,30,p.z),new THREE.Vector3(0,-1,0));
    const hit=ray.intersectObject(terrain.userData.surface)[0];
    assert.ok(hit,'terrain must support the entire loop');
    assert.ok(p.y-hit.point.y>.10,'road must clear the actual terrain triangles');
  }
});
test('conveyor gallery clears the factory walls, roofs and silos at all stack heights',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createPlant,createConveyor}=await import(moduleURL('models.js'));
  const {mineLayout}=await import(moduleURL('layout.js'));
  const scene=new THREE.Scene(),plant=createPlant();plant.position.set(...mineLayout.plant);scene.add(plant);
  const conveyor=createConveyor(scene),obstacles=[];
  plant.updateMatrixWorld(true);
  plant.traverse(obj=>{if(obj.isMesh&&!obj.userData.conveyorDock)obstacles.push(new THREE.Box3().setFromObject(obj));});
  for(const height of [0,3,25,40]){
    conveyor.setDropHeight(height);scene.updateMatrixWorld(true);
    for(let i=0;i<=100;i++)for(const x of [-1.7,0,1.7])for(const y of [-.25,.25]){
      const p=conveyor.belt.localToWorld(new THREE.Vector3(x,y,i/100-.5));
      assert.equal(obstacles.some(box=>box.containsPoint(p)),false,'gallery penetrates a factory structure');
    }
  }
});
test('resuming synchronizes truck progress, heading and velocity without replaying laps',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createHaulRoad}=await import(moduleURL('haul-road.js'));
  const {updateEquipmentTarget,advanceEquipment,synchronizeEquipment}=await import(moduleURL('motion.js'));
  const f=n=>({properties:{value:n}}),road=createHaulRoad();
  const entry={object:new THREE.Group(),targetPosition:new THREE.Vector3(),targetQuaternion:new THREE.Quaternion()};
  const data=trips=>({virtual_time:trips*940,twins:{Truck_01:{state:f('hauling'),trips:f(trips),lat:f(52.11),lon:f(63.115),weight:f(90)}}});
  updateEquipmentTarget('Truck_01',entry,data(0),road);synchronizeEquipment('Truck_01',entry);
  const before=entry.object.position.clone();entry.motion.speed=18;
  updateEquipmentTarget('Truck_01',entry,data(80),road);
  assert.ok(entry.motion.targetProgress-entry.motion.progress>70);
  assert.deepEqual(entry.object.position.toArray(),before.toArray());
  synchronizeEquipment('Truck_01',entry);
  assert.equal(entry.motion.progress,entry.motion.targetProgress);assert.equal(entry.motion.speed,0);
  assert.ok(entry.object.position.distanceTo(entry.targetPosition)<1e-9);
  assert.ok(entry.object.quaternion.angleTo(entry.targetQuaternion)<1e-7);
  const synchronized=entry.object.position.clone();advanceEquipment('Truck_01',entry,600);
  assert.ok(entry.object.position.distanceTo(synchronized)<1e-9);
  updateEquipmentTarget('Truck_01',entry,data(81),road);entry.motion.speed=18;
  advanceEquipment('Truck_01',entry,600);
  assert.ok(entry.object.position.distanceTo(synchronized)<=1.801,'large delta must be clamped');
});
test('2D route resume drops old laps and the stale waiting-bay interpolation',async()=>{
  const {TruckRoute}=await import(moduleURL('routes.js'));
  const route=new TruckRoute(),f=n=>({properties:{value:n}});
  route.update({twins:{Truck_01:{state:f('queued'),trips:f(0)}}},'Truck_01');
  route.update({twins:{Truck_01:{state:f('hauling'),trips:f(50),lat:f(52.11),lon:f(63.115)}}},'Truck_01');
  assert.notEqual(route.phase,route.targetPhase);
  route.synchronize();assert.equal(route.phase,route.targetPhase);assert.equal(route.queue,0);
  const phase=route.phase;route.advance(600);assert.equal(route.phase,phase);
});
test('truck envelope clears the factory and conveyors throughout the entire loop',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createHaulRoad}=await import(moduleURL('haul-road.js'));
  const {createPlant,createTruck,createConveyor}=await import(moduleURL('models.js'));
  const {mineLayout}=await import(moduleURL('layout.js'));
  const scene=new THREE.Scene(),road=createHaulRoad(),plant=createPlant(),truck=createTruck();
  plant.position.set(...mineLayout.plant);scene.add(plant,truck);
  const conveyor=createConveyor(scene);scene.updateMatrixWorld(true);
  const obstacles=[];
  for(const group of [plant,conveyor.group])group.traverse(o=>{if(o.isMesh)obstacles.push(new THREE.Box3().setFromObject(o));});
  for(let i=0;i<1000;i++){
    truck.position.copy(road.curve.getPointAt(i/1000));truck.lookAt(road.curve.getPointAt((i/1000+.005)%1));truck.updateMatrixWorld(true);
    const envelope=new THREE.Box3().setFromObject(truck);
    assert.equal(obstacles.some(box=>envelope.intersectsBox(box)),false,`truck intersects factory infrastructure at t=${i/1000}`);
  }
});
test('road texture repeats by physical distance and includes center and edge paint',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createRoadMaterial}=await import(moduleURL('models.js'));
  const calls=[],ctx={fillStyle:'',fillRect(...rect){calls.push({color:this.fillStyle,rect});}};
  const canvas={getContext:()=>ctx},material=createRoadMaterial(120,canvas);
  assert.equal(material.map.isCanvasTexture,true);assert.equal(material.map.colorSpace,THREE.SRGBColorSpace);
  assert.equal(material.map.wrapT,THREE.RepeatWrapping);assert.equal(material.map.repeat.y,12);
  assert.equal(material.polygonOffset,true);
  assert.ok(calls.some(c=>c.color==='#f2d37c'&&c.rect[3]<canvas.height));
  assert.equal(calls.filter(c=>c.color==='#dce2e8'&&c.rect[3]===canvas.height).length,2);
  material.map.dispose();material.dispose();
});
test('unloading is a fixed receiving station, held until departure without a progress reset',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createHaulRoad}=await import(moduleURL('haul-road.js'));
  const {mineLayout}=await import(moduleURL('layout.js'));
  const {updateEquipmentTarget,advanceEquipment}=await import(moduleURL('motion.js'));
  const road=createHaulRoad(),f=n=>({properties:{value:n}});
  const entry={object:new THREE.Group(),targetPosition:new THREE.Vector3(),targetQuaternion:new THREE.Quaternion()};
  const snapshot=(state,elapsed=0)=>({virtual_time:500+elapsed,twins:{Truck_01:{state:f(state),elapsed:f(elapsed),trips:f(state==='returning'?1:0),lat:f(52.12),lon:f(63.13),weight:f(90)}}});
  updateEquipmentTarget('Truck_01',entry,snapshot('hauling'),road);
  entry.object.position.copy(entry.targetPosition);
  for(let age=0;age<40;age++){
    updateEquipmentTarget('Truck_01',entry,snapshot('unloading',age),road);
    advanceEquipment('Truck_01',entry,1/60);
    assert.equal(entry.motion.targetProgress,road.dock);
    assert.ok(entry.targetPosition.distanceTo(new THREE.Vector3(...mineLayout.unloading))<.005);
  }
  assert.equal(entry.motion.unloading,true);assert.equal(entry.motion.speed,0);
  entry.object.updateMatrixWorld(true);
  const tail=entry.object.localToWorld(new THREE.Vector3(0,0,-4));
  assert.ok(Math.abs(tail.x-mineLayout.unloading[0])<4.5);
  assert.ok(Math.abs(tail.z-mineLayout.unloading[2])<2,'rear discharge must be above the receiving grizzly');
  const parked=entry.object.position.clone(),progress=entry.motion.progress;
  for(let i=0;i<200;i++)advanceEquipment('Truck_01',entry,1/60);
  assert.ok(entry.object.position.distanceTo(parked)<1e-9);
  updateEquipmentTarget('Truck_01',entry,snapshot('returning'),road);
  for(let i=0;i<20;i++)advanceEquipment('Truck_01',entry,1/60);
  assert.ok(entry.motion.progress>progress);assert.equal(entry.motion.unloading,false);
});
test('missed unloading telemetry still produces one smooth stop at the receiver',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createHaulRoad}=await import(moduleURL('haul-road.js'));
  const {updateEquipmentTarget,advanceEquipment}=await import(moduleURL('motion.js'));
  const road=createHaulRoad(),f=n=>({properties:{value:n}});
  const entry={object:new THREE.Group(),targetPosition:new THREE.Vector3(),targetQuaternion:new THREE.Quaternion()};
  const snapshot=(state,trips,p)=>({twins:{Truck_01:{state:f(state),trips:f(trips),lat:f(52.1+p*.02),lon:f(63.1+p*.03)}}});
  updateEquipmentTarget('Truck_01',entry,snapshot('hauling',0,.9),road);entry.object.position.copy(entry.targetPosition);
  updateEquipmentTarget('Truck_01',entry,snapshot('returning',1,.5),road);
  let stopped=0,departed=false;
  for(let i=0;i<900;i++){
    const previous=entry.object.position.clone();advanceEquipment('Truck_01',entry,1/60);
    assert.ok(entry.object.position.distanceTo(previous)<.301);
    if(entry.motion.unloading){stopped++;assert.ok(Math.abs(entry.motion.progress-road.dock)<1e-9);}
    if(stopped&&entry.motion.progress>road.dock+.001){departed=true;break;}
  }
  assert.ok(stopped>=90);assert.equal(departed,true);
});
test('warehouse normalization preserves volume ratios and never changes physical scale for fraud',async()=>{
  const [,,{stockDisplayScales}]=await modules;
  for(const physical of [0,300,3000,6000]){
    const [base]=stockDisplayScales(physical,physical);
    for(const offset of [-300,300]){
      const book=Math.max(0,physical+offset),[p,b]=stockDisplayScales(physical,book);
      assert.deepEqual(p,base);
      assert.ok(p[0]<=19&&p[1]<=8&&p[2]<=11);
      assert.ok(b[0]<22&&b[1]<16&&b[2]<15,'both volumes must fit the hangar');
      if(physical>0)assert.ok(Math.abs((b[0]*b[1]*b[2])/(p[0]*p[1]*p[2])-book/physical)<1e-9);
    }
  }
});

test('fixed warehouse scale grows beyond 3000 t and uses LiDAR rather than hidden truth',async()=>{
  const {stockDisplayScales,lidarMass,MAX_CAPACITY}=await import(moduleURL('telemetry.js'));
  assert.equal(MAX_CAPACITY,6000);
  const [half]=stockDisplayScales(3000,3000),[full]=stockDisplayScales(6000,6000);
  assert.ok(full[1]>half[1]);assert.ok(Math.abs(full[1]**3/half[1]**3-2)<1e-12);
  assert.equal(lidarMass({audit:{physical_stock:99999,lidar_volume_reading:1000}}),2700);
  assert.equal(lidarMass({twins:{LiDAR_01:{volume:{properties:{value:1100}}}},audit:{physical_stock:99999,lidar_volume_reading:1000}}),2970);
});
test('enlarged mill, receiving grizzly, translucent hangar and three aimed LiDAR heads are present',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createPlant,createStockpile}=await import(moduleURL('models.js'));
  const {mineLayout}=await import(moduleURL('layout.js'));
  const plant=createPlant(),stock=createStockpile();stock.updateMatrixWorld(true);
  assert.equal(plant.children[0].scale.x,1.6);
  assert.ok(plant.getObjectByName('receiving-hopper'));
  assert.equal(mineLayout.hopper[0],mineLayout.unloading[0]);
  assert.equal(mineLayout.hopper[2],mineLayout.unloading[2]);
  const grate=plant.getObjectByName('receiving-grizzly');plant.position.set(...mineLayout.plant);plant.updateMatrixWorld(true);
  const point=grate.getWorldPosition(new THREE.Vector3());
  assert.equal(point.x,mineLayout.unloading[0]);assert.equal(point.z,mineLayout.unloading[2]);
  assert.equal(stock.userData.lidars.length,3);
  for(const scanner of stock.userData.lidars){
    const direction=scanner.getWorldDirection(new THREE.Vector3());
    const expected=new THREE.Vector3(0,4,0).sub(scanner.position).normalize();
    assert.ok(direction.dot(expected)>.999);
  }
  let panels=0;
  stock.userData.hangar.traverse(obj=>{if(obj.name==='translucent-cladding'){
    panels++;assert.equal(obj.material.transparent,true);assert.ok(obj.material.opacity>=.3&&obj.material.opacity<=.4);assert.equal(obj.material.depthWrite,false);
  }});
  assert.equal(panels,4);assert.equal(stock.userData.ghost.material.depthTest,false);
});
test('transfer gallery passes the open upper hangar bays without touching roof or trusses',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createStockpile,createConveyor}=await import(moduleURL('models.js'));
  const {mineLayout}=await import(moduleURL('layout.js'));
  const scene=new THREE.Scene(),stock=createStockpile();stock.position.set(...mineLayout.stock);scene.add(stock);
  const conveyor=createConveyor(scene);scene.updateMatrixWorld(true);
  const ray=new THREE.Raycaster();
  for(let i=1;i<conveyor.nodes.length;i++)for(const offset of [-1.6,0,1.6])for(const dy of [-.25,2]){
    const a=conveyor.nodes[i-1].clone().add(new THREE.Vector3(offset,dy,0)),b=conveyor.nodes[i].clone().add(new THREE.Vector3(offset,dy,0));
    ray.set(a,b.clone().sub(a).normalize());ray.far=a.distanceTo(b);
    const hits=ray.intersectObject(stock.userData.hangar,true).filter(hit=>hit.object.parent.name!=='lidar-scanner');
    assert.equal(hits.length,0,`gallery segment ${i} intersects warehouse structure`);
  }
});
test('model clock prediction ignores duplicate HTTP snapshots and follows simulation speed',async()=>{
  const {TruckRoute}=await import(moduleURL('routes.js'));
  const route=new TruckRoute(),f=n=>({properties:{value:n}});
  const snapshot=time=>({virtual_time:time,twins:{Truck_01:{state:f('hauling'),speed:f(30),lat:f(52.105),lon:f(63.1075)}}});
  route.update(snapshot(0),'Truck_01',0);
  route.update(snapshot(30),'Truck_01',1);assert.equal(route.clockRate,30);
  route.update(snapshot(30),'Truck_01',1.9);
  route.update(snapshot(60),'Truck_01',2);assert.equal(route.clockRate,30);
  route.synchronize();route.update(snapshot(3060),'Truck_01',102);
  assert.equal(route.clockRate,30,'resume must not infer a huge catch-up rate');
});
test('3D trucks cruise beyond the last snapshot without packet-boundary pauses or reverse correction',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createHaulRoad}=await import(moduleURL('haul-road.js'));
  const {updateEquipmentTarget,advanceEquipment}=await import(moduleURL('motion.js'));
  const road=createHaulRoad(),f=n=>({properties:{value:n}});
  const entry={object:new THREE.Group(),targetPosition:new THREE.Vector3(),targetQuaternion:new THREE.Quaternion()};
  const snapshot=(p,speed=30)=>({virtual_time:72,twins:{Truck_01:{state:f('hauling'),trips:f(0),lat:f(52.1+p*.02),lon:f(63.1+p*.03),speed:f(speed)}}});
  updateEquipmentTarget('Truck_01',entry,snapshot(.2),road);entry.route.clockRate=30;
  entry.object.position.copy(entry.targetPosition);entry.object.quaternion.copy(entry.targetQuaternion);
  for(let i=0;i<240;i++){
    const previous=entry.motion.progress;advanceEquipment('Truck_01',entry,1/60);
    assert.ok(entry.motion.progress>previous,'moving telemetry must keep the truck moving between packets');
  }
  assert.ok(entry.motion.progress>entry.motion.targetProgress+.08);
  const before=entry.object.position.clone(),quaternion=entry.object.quaternion.clone(),progress=entry.motion.progress;
  updateEquipmentTarget('Truck_01',entry,snapshot(.3),road);
  assert.deepEqual(entry.object.position.toArray(),before.toArray());
  assert.deepEqual(entry.object.quaternion.toArray(),quaternion.toArray());
  for(let i=0;i<60;i++){advanceEquipment('Truck_01',entry,1/60);assert.ok(entry.motion.speed>1);}
  assert.ok(entry.motion.progress>progress);
  updateEquipmentTarget('Truck_01',entry,snapshot(.3,0),road);
  for(let i=0;i<120;i++)advanceEquipment('Truck_01',entry,1/60);
  assert.equal(entry.motion.speed,0,'an explicit zero speed is a real stop');
  const stopped=entry.object.position.clone();advanceEquipment('Truck_01',entry,.1);
  assert.ok(stopped.distanceTo(entry.object.position)<1e-9);
});
test('3D cruise prediction is consistent across frame rates',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createHaulRoad}=await import(moduleURL('haul-road.js'));
  const {updateEquipmentTarget,advanceEquipment}=await import(moduleURL('motion.js'));
  const road=createHaulRoad(),f=n=>({properties:{value:n}}),positions=[];
  for(const fps of [30,60,144]){
    const entry={object:new THREE.Group(),targetPosition:new THREE.Vector3(),targetQuaternion:new THREE.Quaternion()};
    updateEquipmentTarget('Truck_01',entry,{twins:{Truck_01:{state:f('hauling'),lat:f(52.1),lon:f(63.1),speed:f(30)}}},road);
    entry.route.clockRate=30;
    for(let i=0;i<fps*3;i++)advanceEquipment('Truck_01',entry,1/fps);
    positions.push(entry.object.position.clone());
  }
  assert.ok(positions[0].distanceTo(positions[2])<.3);
});
test('2D currentP interpolates through a full-lap seam equally at 30 and 144 FPS',async()=>{
  const {TruckRoute,loopPoint}=await import(moduleURL('routes.js'));
  const f=n=>({properties:{value:n}}),routes=[];
  for(const fps of [30,144]){
    const route=new TruckRoute();
    route.update({twins:{Truck_01:{state:f('loading'),trips:f(1),weight:f(89)}}},'Truck_01');
    const before=route.currentP;
    route.update({twins:{Truck_01:{state:f('hauling'),trips:f(1),lat:f(52.102),lon:f(63.103)}}},'Truck_01');
    assert.equal(route.currentP,before);assert.ok(route.targetP>4);
    for(let i=0;i<fps;i++){
      const p=route.currentP,point=loopPoint(p);route.advance(1/fps);
      assert.ok(route.currentP>=p&&route.currentP<=route.targetP);
      const next=loopPoint(route.currentP);assert.ok(Math.hypot(next.x-point.x,next.y-point.y)<.05);
    }
    routes.push(route.currentP);
  }
  assert.ok(Math.abs(routes[0]-routes[1])<1e-12);
});
test('2D rail updates change targets only, with frame-independent smoothing and explicit resume',async()=>{
  const {GeoMarker}=await import(moduleURL('charts.js'));
  const outcomes=[];
  for(const fps of [30,144]){
    const marker=new GeoMarker();marker.update(52.12,63.13);marker.update(52.7,63.132);
    assert.deepEqual(marker.current,[52.12,63.13]);
    for(let i=0;i<fps;i++)marker.advance(1/fps);
    assert.ok(marker.current[0]>52.12&&marker.current[0]<52.7);outcomes.push(marker.current);
    marker.synchronize();assert.deepEqual(marker.current,marker.target);
  }
  assert.ok(Math.abs(outcomes[0][0]-outcomes[1][0])<1e-12);
});
test('HTTP map updates do not render or change marker positions; RAF advances both fleets',async t=>{
  const {LogisticsMap}=await import(moduleURL('charts.js'));
  const originals={document:global.document,requestAnimationFrame:global.requestAnimationFrame,cancelAnimationFrame:global.cancelAnimationFrame};
  Object.assign(global,{document:{hidden:false,addEventListener(){},removeEventListener(){}},requestAnimationFrame:()=>1,cancelAnimationFrame(){}});
  const map=new LogisticsMap({}, {addEventListener(){},removeEventListener(){}}),f=n=>({properties:{value:n}});
  t.after(()=>{map.dispose();Object.assign(global,originals);});let draws=0;map.draw=()=>draws++;
  const snapshot=(p,revision)=>({revision,mqtt_connected:true,telemetry_age:0,twins:{
    Truck_01:{state:f('hauling'),lat:f(52.1+p*.02),lon:f(63.1+p*.03)},
    Wagon_01:{lat:f(52.12+p*.58),lon:f(63.13)}
  }});
  map.update(snapshot(0,1));
  map.update(snapshot(.5,2));
  assert.equal(draws,0);assert.equal(map.trucks.get('Truck_01').currentP,0);assert.equal(map.railMarkers.get('Wagon_01').current[0],52.12);
  map.tick(map.lastFrame+1000/60);
  assert.equal(draws,1);
  assert.ok(map.trucks.get('Truck_01').currentP>0&&map.trucks.get('Truck_01').currentP<.5);
  assert.ok(map.railMarkers.get('Wagon_01').current[0]>52.12&&map.railMarkers.get('Wagon_01').current[0]<52.41);
});

test('MAINTENANCE preserves the displayed route, stops both views and resumes without an extra lap',async()=>{
  const THREE=await import(moduleURL('vendor/three.module.js'));
  const {createHaulRoad}=await import(moduleURL('haul-road.js'));
  const {updateEquipmentTarget,advanceEquipment}=await import(moduleURL('motion.js'));
  const road=createHaulRoad(),f=n=>({properties:{value:n}});
  const entry={object:new THREE.Group(),targetPosition:new THREE.Vector3(),targetQuaternion:new THREE.Quaternion()};
  const snapshot=paused=>({virtual_time:144,twins:{Truck_01:{state:f(paused?'MAINTENANCE':'hauling'),operating_state:f('hauling'),elapsed:f(144),trips:f(0),lat:f(52.108),lon:f(63.112),speed:f(paused?0:30)}}});
  updateEquipmentTarget('Truck_01',entry,snapshot(false),road);
  entry.object.position.copy(entry.targetPosition);entry.motion.speed=10;
  const target=entry.motion.targetProgress,phase=entry.route.currentP;
  updateEquipmentTarget('Truck_01',entry,snapshot(true),road);
  assert.equal(entry.motion.targetProgress,target);
  for(let i=0;i<120;i++){advanceEquipment('Truck_01',entry,1/60);entry.route.advance(1/60);}
  assert.equal(entry.motion.speed,0);assert.equal(entry.route.currentP,phase);
  const parked=entry.object.position.clone();advanceEquipment('Truck_01',entry,.1);
  assert.ok(entry.object.position.distanceTo(parked)<1e-9);
  updateEquipmentTarget('Truck_01',entry,snapshot(false),road);
  for(let i=0;i<60;i++)advanceEquipment('Truck_01',entry,1/60);
  assert.ok(entry.object.position.distanceTo(parked)>0);assert.equal(entry.motion.targetProgress,target);
});
