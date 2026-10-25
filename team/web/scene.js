import * as THREE from './vendor/three.module.js';
import {OrbitControls} from './vendor/OrbitControls.js';
import {createTruck, createExcavator, createPlant, createStockpile, createWagon, createTerrain,
  createLamp, createConveyor, createRail, createRoadMaterial, mesh, material, palette} from './models.js';
import {value, number, stateName, displayNames, interpolationAlpha, stockDisplayScales, isFresh} from './telemetry.js';
import {mineLayout, haulLayout, wagonPosition} from './layout.js';
import {updateEquipmentTarget,advanceEquipment,synchronizeEquipment} from './motion.js';
import {createHaulRoad} from './haul-road.js';

export class MineScene {
  constructor({canvas, stage, labels, tooltip, onSelect}) {
    Object.assign(this, {canvas, stage, labels, tooltip, onSelect});
    this.entities = new Map();
    this.pickables = [];
    this.data = null;
    this.selected = 'Stockpile_01';
    this.hovered = null;
    this.pointer = new THREE.Vector2(99,99);
    this.raycaster = new THREE.Raycaster();
    this.renderer = new THREE.WebGLRenderer({canvas, antialias:true, alpha:true, powerPreference:'high-performance'});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1,2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.12;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(-140,140,75,-75,.1,700);
    this.cameraHome = new THREE.Vector3(180,160,180); // 45 degrees around Y.
    this.camera.position.copy(this.cameraHome);
    this.camera.lookAt(0,0,0);
    this.controls = new OrbitControls(this.camera,canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = .08;
    this.controls.enablePan = false;
    this.controls.minPolarAngle = .35;
    this.controls.maxPolarAngle = 1.25;
    this.controls.minZoom = .6;
    this.controls.maxZoom = 2.8;
    this.scene.add(new THREE.AmbientLight(0xb5ccdd,.65));
    this.scene.add(new THREE.HemisphereLight(0xcfe7f3,0x485361,1.35));
    const sun = new THREE.DirectionalLight(0xffedd2,3.2);
    sun.position.set(-45,130,75);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048,2048);
    Object.assign(sun.shadow.camera,{left:-140,right:140,top:125,bottom:-125,near:1,far:350});
    sun.shadow.bias = -.00035;
    sun.shadow.normalBias = .15;
    sun.shadow.radius = 3;
    this.scene.add(sun,sun.target);
    this.road=createHaulRoad();
    createTerrain(this.scene,this.road);
    for(const point of [[-54,18],[19,-38],[81,17],[-20,48]])createLamp(this.scene,...point);
    this.buildRoad();
    this.register('Excavator_01',createExcavator(),mineLayout.excavator);
    this.register('Processing_Plant',createPlant(),mineLayout.plant);
    this.register('Stockpile_01',createStockpile(),mineLayout.stock);
    this.register('Truck_01',createTruck(),[-63,-4.2,-25]);
    this.register('Truck_02',createTruck(),[-57,-3.5,-24]);
    this.register('Rail_Siding',createRail(),mineLayout.rail,false);
    for(let i=0;i<5;i++) {
      const wagon=createWagon();wagon.rotation.y=Math.PI/2;
      this.register(`Wagon_0${i+1}`,wagon,wagonPosition(i));
    }
    this.conveyor = createConveyor(this.scene);
    this.halo = mesh(new THREE.RingGeometry(1,1.035,64),new THREE.MeshBasicMaterial({color:palette.cyan,transparent:true,opacity:.35,side:THREE.DoubleSide,depthWrite:false}),this.scene);
    this.halo.rotation.x=-Math.PI/2;this.halo.castShadow=false;
    this.bindInteraction();
    this.resizeObserver = new ResizeObserver(()=>this.resize());
    this.resizeObserver.observe(stage);
    this.resize();
    this.clock=new THREE.Clock(false);this.elapsed=0;this.running=false;this.active=true;
    this.frame=this.frame.bind(this);
    this.onVisibilityChange=()=>this.setRunning(this.active&&!document.hidden);
    document.addEventListener('visibilitychange',this.onVisibilityChange);
    this.setRunning(!document.hidden);
    canvas.dataset.engine='three';canvas.dataset.camera='orthographic';
    canvas.addEventListener('webglcontextlost',event=>{
      event.preventDefault();this.running=false;this.contextLost=true;this.clock.stop();
      stage.dispatchEvent(new CustomEvent('scene-error',{detail:'Графический контекст потерян. Обновите страницу; телеметрия и управление продолжают работать.'}));
    });
  }

  register(name,object,position,label=true) {
    object.name=name;object.position.set(...position);this.scene.add(object);
    object.traverse(child=>{if(child.isMesh){child.userData.entity=name;this.pickables.push(child);}});
    let element=null;
    if(label) {
      element=document.createElement('button');element.type='button';
      element.className='twin-label'+(/Truck|Wagon/.test(name)?'':' facility')+(name.startsWith('Wagon')?' wagon':'');
      element.dataset.twin=name;element.title=displayNames[name];
      const dot=document.createElement('i');dot.className='label-dot';
      const text=document.createElement('span');text.textContent=name.startsWith('Truck')?name.toLowerCase().replace('_','-'):name.startsWith('Wagon')?'В-'+name.slice(-1):name==='Stockpile_01'?'Склад концентрата':name==='Processing_Plant'?'Фабрика':'Экскаватор';
      element.append(dot,text);this.labels.append(element);
      element.addEventListener('click',()=>this.select(name));
    }
    this.entities.set(name,{object,label:element,targetPosition:object.position.clone(),targetQuaternion:object.quaternion.clone(),initialized:false,labelPoint:new THREE.Vector3()});
  }

  buildRoad() {
    const vertices=[],uvs=[],indices=[],edges=[[],[]];
    for(let i=0;i<=90;i++) {
      const t=i/90,p=this.road.centerline.getPointAt(t),normal=this.road.normalAt(t);
      for(let side=0;side<2;side++) {
        const edge=p.clone().addScaledVector(normal,(side?1:-1)*haulLayout.halfWidth);edge.y+=haulLayout.roadLift;
        vertices.push(edge.x,edge.y,edge.z);uvs.push(side,t);edges[side].push(edge.clone().add(new THREE.Vector3(0,.04,0)));
      }
      if(i<90){const k=i*2;indices.push(k,k+1,k+2,k+1,k+3,k+2);}
    }
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));geometry.setIndex(indices);geometry.computeVertexNormals();
    geometry.setAttribute('uv',new THREE.Float32BufferAttribute(uvs,2));
    const roadMaterial=createRoadMaterial(this.road.centerline.getLength());
    const turnMaterial=roadMaterial.clone();turnMaterial.map=null;turnMaterial.color.set(0x48515c);
    const surface=mesh(geometry,roadMaterial,this.scene);surface.name='haul-road-surface';surface.castShadow=false;
    for(const end of [0,1]){
      const point=this.road.centerline.getPointAt(end);point.y+=haulLayout.roadLift;
      const direction=this.road.centerline.getTangentAt(end).setY(0).normalize(),normal=this.road.normalAt(end);
      const positions=[...point.toArray()],triangles=[],capEdge=[];
      for(let i=0;i<=32;i++){
        const angle=Math.PI*i/32,p=point.clone().addScaledVector(normal,haulLayout.halfWidth*Math.cos(angle))
          .addScaledVector(direction,(end?1:-1)*haulLayout.halfWidth*Math.sin(angle));
        positions.push(...p.toArray());capEdge.push(p.clone().add(new THREE.Vector3(0,.04,0)));
        if(i)triangles.push(...(end?[0,i,i+1]:[0,i+1,i]));
      }
      const capGeometry=new THREE.BufferGeometry();capGeometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));capGeometry.setIndex(triangles);capGeometry.computeVertexNormals();
      const cap=mesh(capGeometry,turnMaterial,this.scene);cap.name='haul-road-turn';cap.castShadow=false;
      edges.push(capEdge);
    }
    for(const points of edges) {
      const curve=new THREE.CatmullRomCurve3(points);
      mesh(new THREE.TubeGeometry(curve,100,.065,4,false),new THREE.MeshBasicMaterial({color:palette.cyan,transparent:true,opacity:.85,toneMapped:false}),this.scene).castShadow=false;
      mesh(new THREE.TubeGeometry(curve,100,.18,4,false),new THREE.MeshBasicMaterial({color:palette.cyan,transparent:true,opacity:.09,blending:THREE.AdditiveBlending,depthWrite:false,toneMapped:false}),this.scene).castShadow=false;
    }
  }

  update(data) {
    const previous=this.data;this.data=data;
    this.productionRunning=!value(data,'Processing_Plant','maintenance')&&(value(data,'Processing_Plant','buffer')>.1 || value(data,'Processing_Plant','raw_inflow')>value(previous,'Processing_Plant','raw_inflow'));
    this.excavatorTargetPhase=value(data,'Excavator_01','passes')*Math.PI*2;
    if(this.excavatorPhase===undefined)this.excavatorPhase=this.excavatorTargetPhase;
    for(const [name,entry] of this.entities) {
      updateEquipmentTarget(name,entry,data,this.road);
      // Initial placement precedes display; later snapshots only change targets.
      if(!entry.initialized) {
        entry.object.position.copy(entry.targetPosition);entry.object.quaternion.copy(entry.targetQuaternion);
        entry.initialized=true;
      }
    }
    const stock=this.entities.get('Stockpile_01').object;
    const [physical,book]=stockDisplayScales(data.audit.physical_stock,data.audit.book_stock);
    this.physicalScale=new THREE.Vector3(...physical);
    this.bookScale=new THREE.Vector3(...book);
    if(!this.stockInitialized){stock.userData.physical.scale.copy(this.physicalScale);stock.userData.ghost.scale.copy(this.bookScale);stock.userData.ghost.material.opacity=0;this.stockInitialized=true;}
    stock.userData.labelHeight=25;
    stock.userData.ghost.visible=!!data.audit.fraud_offset || stock.userData.ghost.material.opacity>.01;
    if(this.syncOnNextUpdate&&isFresh(data)&&(data.revision===undefined||data.revision!==this.resumeRevision)){
      this.synchronize();this.syncOnNextUpdate=false;
    }
  }

  bindInteraction() {
    let start=null;
    this.canvas.addEventListener('pointerdown',event=>{start=[event.clientX,event.clientY];});
    this.stage.addEventListener('pointermove',event=>{
      const rect=this.canvas.getBoundingClientRect();
      this.pointer.set((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1);
      this.labelHover=event.target.closest('.twin-label')?.dataset.twin||null;
    });
    this.stage.addEventListener('pointerleave',()=>{this.pointer.set(99,99);this.tooltip.hidden=true;this.hovered=null;this.labelHover=null;});
    this.canvas.addEventListener('pointerup',event=>{
      if(start&&Math.hypot(event.clientX-start[0],event.clientY-start[1])<5&&this.hovered)this.select(this.hovered);
      start=null;
    });
  }

  select(name) {
    this.selected=name;this.onSelect?.(name);
    for(const [id,entry]of this.entities)entry.label?.classList.toggle('active',id===name);
  }
  resetView() {
    this.camera.position.copy(this.cameraHome);this.controls.target.set(0,0,0);this.camera.zoom=1;
    this.camera.updateProjectionMatrix();this.controls.update();
  }
  focusStock() {
    const center=this.entities.get('Stockpile_01').object.position;
    this.controls.target.copy(center);this.camera.position.copy(center).add(new THREE.Vector3(110,105,110));
    this.camera.zoom=1.65;this.camera.updateProjectionMatrix();this.controls.update();this.select('Stockpile_01');
  }
  resize() {
    const width=Math.max(1,this.stage.clientWidth),height=Math.max(1,this.stage.clientHeight);
    const aspect=width/height,size=Math.max(148,260/aspect);
    this.camera.left=-size*aspect/2;this.camera.right=size*aspect/2;
    this.camera.top=size/2;this.camera.bottom=-size/2;this.camera.updateProjectionMatrix();
    this.renderer.setSize(width,height,false);
  }

  updateTooltip() {
    this.raycaster.setFromCamera(this.pointer,this.camera);
    const hit=this.raycaster.intersectObjects(this.pickables,false).find(hit=>hit.object.visible&&hit.object.parent?.visible);
    const name=this.labelHover||hit?.object.userData.entity||null;
    if(name!==this.hovered || this.tooltipRevision!==this.data?.revision) {
      this.hovered=name;
      this.tooltipRevision=this.data?.revision;
      if(name&&this.data) {
        const title=document.createElement('strong');title.textContent=displayNames[name]||name;
        const body=document.createElement('p');
        body.textContent=name.startsWith('Wagon')
          ?`Статичная схема погрузки · Груз: ${number(value(this.data,name,'weight'))} т · Маршрут — на карте`
          :name.startsWith('Truck')?`Скорость: ${number(value(this.data,name,'speed'),0)} км/ч · Груз: ${number(value(this.data,name,'weight'))} т`
          :name==='Rail_Siding'?'Погрузочный узел · состав закреплен на схеме'
          :name==='Stockpile_01'?`Физически: ${number(this.data.audit.physical_stock)} т · Объем: ${number(this.data.audit.scan_volume)} м³`
          :name==='Processing_Plant'?`Концентрат: Fe 55% · Извлечение: 83.5%`
          :stateName(value(this.data,name,'state','idle'));
        this.tooltip.replaceChildren(title,body);
      }
    }
    this.tooltip.hidden=!name||!this.data;
    if(name) {
      const entry=this.entities.get(name),point=entry.object.position.clone();point.y+=(entry.object.userData.labelHeight||10)+4;
      point.project(this.camera);
      const x=Math.max(110,Math.min(this.stage.clientWidth-110,(point.x*.5+.5)*this.stage.clientWidth));
      const y=Math.max(90,(-point.y*.5+.5)*this.stage.clientHeight);
      this.tooltip.style.left=`${x}px`;this.tooltip.style.top=`${y}px`;
    }
  }

  setActive(active) {
    this.active=active;this.setRunning(active&&!document.hidden);
  }

  synchronize() {
    for(const [name,entry]of this.entities)synchronizeEquipment(name,entry);
    this.excavatorPhase=this.excavatorTargetPhase;
  }

  setRunning(active) {
    if(this.disposed||this.contextLost||this.running===active)return;
    this.running=active;
    if(active){
      this.clock.start();this.clock.getDelta();
      this.resumeRevision=this.data?.revision;this.syncOnNextUpdate=true;
      this.synchronize();this.resize();this.frameId=requestAnimationFrame(this.frame);
    }
    else {cancelAnimationFrame(this.frameId);this.clock.stop();}
  }

  frame() {
    if(!this.running)return;
    const dt=Math.min(.1,this.clock.getDelta());this.elapsed+=dt;
    const alpha=interpolationAlpha(dt),fresh=isFresh(this.data);
    for(const [name,entry]of this.entities) {
      const object=entry.object,previous=object.position.clone();
      if(fresh)advanceEquipment(name,entry,dt);
      const distance=previous.distanceTo(object.position);
      if(object.userData.wheels)for(const wheel of object.userData.wheels)wheel.rotateY(distance/(name.startsWith('Truck')?1.15:.65));
      if(name.startsWith('Truck')&&this.data) {
        const amount=value(this.data,name,'weight')/90;
        // Keep the rendered load until the displayed truck actually reaches
        // the receiver; a newer weight snapshot may already say "empty".
        const arriving=entry.motion.nextDock<=entry.motion.targetProgress+1e-8;
        entry.cargoLoad=entry.motion.unloading?0:arriving?Math.max(amount,entry.cargoLoad??amount):amount;
        object.userData.cargo.scale.y+=(entry.cargoLoad*.45-object.userData.cargo.scale.y)*alpha;
        object.userData.cargo.visible=object.userData.cargo.scale.y>.01;
        const angle=entry.motion?.unloading?-.65:0;
        object.userData.bed.rotation.x+=(angle-object.userData.bed.rotation.x)*alpha;
        object.userData.discharge.forEach((ore,i)=>{
          const phase=(this.elapsed*1.5+i/5)%1;
          ore.visible=fresh&&entry.motion.unloading&&object.userData.cargo.visible;
          ore.position.set(.4*Math.sin(i*2.4),2.3*(1-phase),-3.8-.4*phase);
        });
      }
      if(name.startsWith('Wagon')&&this.data) {
        object.userData.cargo.scale.y+=(value(this.data,name,'weight')/70-object.userData.cargo.scale.y)*alpha;
        object.userData.cargo.visible=object.userData.cargo.scale.y>.02;
      }
      if(entry.label) {
        entry.labelPoint.copy(object.position);entry.labelPoint.y+=object.userData.labelHeight||9;entry.labelPoint.project(this.camera);
        const x=(entry.labelPoint.x*.5+.5)*this.stage.clientWidth,y=(-entry.labelPoint.y*.5+.5)*this.stage.clientHeight;
        entry.label.hidden=entry.labelPoint.z>1||x<10||x>this.stage.clientWidth-10||y<8||y>this.stage.clientHeight-12;
        entry.label.style.transform=`translate(${x}px,${y}px) translate(-50%,-100%)`;
      }
    }
    const excavator=this.entities.get('Excavator_01').object;
    const loading=fresh&&value(this.data,'Excavator_01','state')==='loading';
    if(loading) {
      this.excavatorPhase+=(this.excavatorTargetPhase-this.excavatorPhase)*alpha;
      excavator.userData.cab.rotation.y+=(Math.PI/2+.18*Math.sin(this.excavatorPhase*.5)-excavator.userData.cab.rotation.y)*alpha;
      excavator.userData.boom.rotation.x=.55+.08*Math.sin(this.excavatorPhase);
      excavator.userData.arm.rotation.x=1.45+.12*Math.sin(this.excavatorPhase+.7);
    }
    if(this.stockInitialized) {
      const stock=this.entities.get('Stockpile_01').object.userData;
      stock.physical.scale.lerp(this.physicalScale,alpha);stock.ghost.scale.lerp(this.bookScale,alpha);
      const target=this.data.audit.fraud_offset!==0?.58:0;
      stock.ghost.material.opacity+=(target-stock.ghost.material.opacity)*alpha;
      stock.ghost.visible=stock.ghost.material.opacity>.01;
      stock.scanRing.material.opacity=.28+.1*Math.sin(this.elapsed);
      this.conveyor.setDropHeight(stock.physical.scale.y);
    }
    for(let i=0;i<this.conveyor.ores.length;i++) {
      const ore=this.conveyor.ores[i];ore.visible=fresh&&this.productionRunning;
      const phase=(this.elapsed*.14+i/10)%1;
      this.conveyor.path.getPointAt(phase,ore.position);ore.position.y+=.65;
    }
    const active=this.entities.get(this.hovered||this.selected);
    if(active) {
      this.halo.position.copy(active.object.position);this.halo.position.y+=.2;
      const size=(this.hovered||this.selected)==='Stockpile_01'?27:7;
      this.halo.scale.set(size,size*.66,1);
    }
    this.controls.update();
    this.scene.updateMatrixWorld();this.camera.updateMatrixWorld();
    if(this.pointer.x<90)this.updateTooltip();
    this.renderer.render(this.scene,this.camera);
    this.frameId=requestAnimationFrame(this.frame);
  }

  dispose() {
    this.running=false;this.disposed=true;this.clock.stop();cancelAnimationFrame(this.frameId);this.resizeObserver.disconnect();this.controls.dispose();
    document.removeEventListener('visibilitychange',this.onVisibilityChange);
    const geometries=new Set(),materials=new Set();
    this.scene.traverse(object=>{if(object.geometry)geometries.add(object.geometry);if(object.material)for(const mat of Array.isArray(object.material)?object.material:[object.material])materials.add(mat);});
    const textures=new Set();materials.forEach(m=>{if(m.map)textures.add(m.map);m.dispose();});
    textures.forEach(t=>t.dispose());geometries.forEach(g=>g.dispose());this.renderer.dispose();
  }
}
