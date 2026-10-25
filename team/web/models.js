import * as THREE from './vendor/three.module.js';
import {mineLayout} from './layout.js';
import {createHaulRoad,benchHeight} from './haul-road.js';

export const palette = {
  sand: 0x89988f, edge: 0x526471, strata: 0x354854, soil: 0x687c77,
  yellow: 0xe8b857, yellowDark: 0xb68c3c, frame: 0x34424a, tire: 0x202b33,
  steel: 0x9cabb6, roof: 0x526d80, window: 0x304d5b, ore: 0x496f69, cyan: 0x75e5d2
};
export const material = (color, options = {}) => new THREE.MeshStandardMaterial({color, roughness: .82, metalness: .08, flatShading: true, ...options});

/** Road-width UVs and a repeating 10 m tile keep paint readable at any length. */
export function createRoadMaterial(length,canvas=document.createElement('canvas')) {
  canvas.width=512;canvas.height=512;
  const ctx=canvas.getContext('2d');
  ctx.fillStyle='#48515c';ctx.fillRect(0,0,512,512);
  ctx.fillStyle='rgba(255,255,255,.045)';
  for(let i=0;i<1800;i++)ctx.fillRect((i*73)%512,(i*151)%512,1,1);
  ctx.fillStyle='#dce2e8';ctx.fillRect(18,0,4,512);ctx.fillRect(490,0,4,512);
  ctx.fillStyle='#f2d37c';ctx.fillRect(253,64,6,256);
  const texture=new THREE.CanvasTexture(canvas);
  texture.colorSpace=THREE.SRGBColorSpace;texture.wrapS=texture.wrapT=THREE.RepeatWrapping;
  texture.repeat.set(1,length/10);texture.anisotropy=4;
  return material(0xffffff,{map:texture,roughness:.96,side:THREE.DoubleSide,
    polygonOffset:true,polygonOffsetFactor:-1,polygonOffsetUnits:-1});
}
const mats = Object.fromEntries(Object.entries(palette).map(([name, color]) => [name, material(color)]));
const litWindow = new THREE.MeshBasicMaterial({color: 0xd1e8df});
const warmWindow = new THREE.MeshBasicMaterial({color: 0xffdca4});

export function mesh(geometry, mat, parent, position = [0, 0, 0]) {
  const object = new THREE.Mesh(geometry, mat);
  object.position.set(...position);
  object.castShadow = true;
  object.receiveShadow = true;
  parent?.add(object);
  return object;
}
export const box = (parent, size, position, mat = mats.steel) => mesh(new THREE.BoxGeometry(...size), mat, parent, position);
const cylinder = (parent, radius, height, position, mat, segments = 10) => mesh(new THREE.CylinderGeometry(radius, radius, height, segments), mat, parent, position);

function slopedBedGeometry() {
  const vertices = [-2,0,0, 2,0,0, 2,0,5.8, -2,0,5.8, -2.9,1.7,-.7, 2.9,1.7,-.7, 2.9,1.7,6.2, -2.9,1.7,6.2];
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
  g.setIndex([0,2,1,0,3,2,4,5,6,4,6,7,0,1,5,0,5,4,1,2,6,1,6,5,2,3,7,2,7,6,3,0,4,3,4,7]);
  g.computeVertexNormals();
  return g;
}

export function createTruck() {
  const group = new THREE.Group();
  box(group, [5.4,.65,8.8], [0,1.7,0], mats.frame);
  box(group, [3.6,.4,7.2], [0,1.2,0], mats.frame);
  const wheels = [];
  for (const x of [-2.8,2.8]) for (const z of [-2.8,2.7]) {
    const wheel = cylinder(group, 1.15, .9, [x,1.15,z], mats.tire, 12);
    wheel.rotation.z = Math.PI / 2;
    wheels.push(wheel);
    const hub = cylinder(group,.48,.94,[x,1.15,z],mats.yellowDark,10);
    hub.rotation.z = Math.PI / 2;
    wheels.push(hub);
  }
  box(group,[4.9,2.65,2.5],[0,3.3,3.15],mats.yellow);
  box(group,[4.35,1.05,.1],[0,3.8,4.43],mats.window);
  box(group,[.1,1.05,1.8],[-2.48,3.8,3.4],mats.window);
  box(group,[.1,1.05,1.8],[2.48,3.8,3.4],mats.window);
  box(group,[5.3,.22,2.8],[0,4.73,3.2],mats.yellow);
  box(group,[5.6,.45,.65],[0,1.7,4.6],mats.steel);
  for(const x of [-1.8,1.8])box(group,[.6,.35,.12],[x,2.4,4.46],warmWindow);
  const bed = new THREE.Group();bed.position.set(0,2.25,-3.1);group.add(bed);
  mesh(slopedBedGeometry(),mats.yellow,bed);
  box(bed,[5.6,.25,6.4],[0,1.72,2.8],mats.yellowDark);
  for(const x of [-2.8,2.8])box(bed,[.23,.65,6.6],[x,2,2.7],mats.yellow);
  box(bed,[5.7,.65,.23],[0,2,-.5],mats.yellow);
  const cargo = mesh(new THREE.SphereGeometry(2.4,8,4,0,Math.PI*2,0,Math.PI/2),mats.ore,bed,[0,1.9,2.55]);
  cargo.scale.set(1.06,.45,1.15);
  const discharge=[];
  for(let i=0;i<5;i++){
    const ore=mesh(new THREE.IcosahedronGeometry(.22,0),mats.ore,group,[0,1,-3.9]);
    ore.visible=false;discharge.push(ore);
  }
  for(let i=0;i<4;i++)box(group,[.7,.15,.25],[-3.02,1.9+i*.55,3.8],mats.frame);
  group.userData = {wheels,bed,cargo,discharge,labelHeight:7};
  return group;
}

export function createExcavator() {
  const group = new THREE.Group();
  for(const x of [-2.7,2.7]) {
    box(group,[1.65,1.8,8.7],[x,1,0],mats.tire);
    for(let i=0;i<6;i++) {
      const roller=cylinder(group,.6,1.75,[x,1,-3.1+i*1.25],mats.frame);
      roller.rotation.z=Math.PI/2;
      box(group,[1.8,.2,.75],[x,1.95,-3.3+i*1.3],mats.frame);
    }
  }
  cylinder(group,2.35,1.1,[0,2.3,0],mats.yellowDark,12);
  const cab=new THREE.Group();cab.position.y=2.8;group.add(cab);
  box(cab,[5.7,2.2,5.7],[0,1.1,0],mats.yellow);
  box(cab,[2.5,2.8,3],[1.6,3.1,1.4],mats.yellow);
  box(cab,[2.1,1.5,.15],[1.6,3.4,2.98],mats.window);
  box(cab,[.15,1.5,2.5],[2.9,3.4,1.4],mats.window);
  box(cab,[5.7,2,2],[0,1.5,-3.1],mats.yellowDark);
  const boom=new THREE.Group();boom.position.set(-1.3,2.4,1.3);boom.rotation.x=.55;cab.add(boom);
  box(boom,[1.35,8.5,1.5],[0,4,0],mats.yellow);
  const piston=cylinder(boom,.23,6.2,[.8,2.8,.8],mats.steel);piston.rotation.x=-.12;
  const arm=new THREE.Group();arm.position.set(0,7.8,0);arm.rotation.x=1.45;boom.add(arm);
  box(arm,[1.15,6.8,1.25],[0,3.1,0],mats.yellow);
  const bucket=box(arm,[3.8,2.1,3.1],[0,6.5,.8],mats.frame);bucket.rotation.x=.6;
  for(let i=0;i<5;i++)box(arm,[.38,.8,.8],[-1.5+i*.75,7.45,2.1],mats.yellowDark);
  group.userData={cab,boom,arm,labelHeight:16};
  return group;
}

export function createPlant() {
  const root=new THREE.Group(),group=new THREE.Group();root.add(group);
  group.scale.setScalar(mineLayout.plantScale);
  box(group,[22,.7,19],[0,.35,0],mats.frame);
  box(group,[17,12,15],[0,6.7,0],mats.steel);
  box(group,[18,1,16],[0,13.2,0],mats.roof);
  box(group,[8,9,9],[-4,17,-2],mats.roof);
  box(group,[8.5,.6,9.7],[-4,21.7,-2],mats.frame);
  for(let floor=0;floor<3;floor++)for(let col=0;col<5;col++) {
    box(group,[1.4,1.5,.12],[-6+col*3,3+floor*3.2,7.56],litWindow);
    box(group,[.12,1.5,1.4],[8.56,3+floor*3.2,-5+col*2.5],litWindow);
  }
  for(const x of [-12,13]) {
    cylinder(group,2.7,13,[x,7,-4],mats.steel,12);
    mesh(new THREE.ConeGeometry(2.8,3,12),mats.roof,group,[x,15,-4]);
    for(let i=0;i<3;i++)cylinder(group,2.75,.16,[x,3+i*4,-4],mats.roof,12);
  }
  // One stack supported by the tower roof (roof top 22, stack bottom 22).
  cylinder(group,.9,6,[-5,25,-3],mats.steel,8);
  box(group,[7,4,.3],[0,2.6,7.7],mats.window);
  for(const x of [-9,9])for(const z of [-8.5,8.5])cylinder(group,.13,3,[x,14.5,z],mats.steel,6);
  box(group,[18,.16,.16],[0,15.9,8.5],mats.steel);
  // External discharge socket: gallery begins beyond its outer face.
  const dock=box(group,[3.8,2.1,1.4],[5,10,-9.2],mats.frame);
  dock.userData.conveyorDock=true;dock.name='external-conveyor-discharge';
  root.userData={labelHeight:29*mineLayout.plantScale};
  const receiver=new THREE.Group();receiver.name='receiving-hopper';root.add(receiver);
  receiver.position.set(mineLayout.hopper[0]-mineLayout.plant[0],mineLayout.hopper[1],mineLayout.hopper[2]-mineLayout.plant[2]);
  // Drive-over receiving pit: its top is below the tyres and its grizzly
  // extends under the rear discharge. The feed lift connects it to the mill.
  const pit=mesh(new THREE.CylinderGeometry(1,.35,2.6,4,1,true),mats.frame,receiver);
  pit.rotation.y=Math.PI/4;pit.scale.set(5.9,1,2.8);
  const intake=new THREE.Group();intake.name='mill-feed-intake';root.add(intake);
  intake.position.set(mineLayout.millIntake[0]-mineLayout.plant[0],mineLayout.millIntake[1],mineLayout.millIntake[2]-mineLayout.plant[2]);
  const bowl=mesh(new THREE.CylinderGeometry(2,1,1.3,4,1,true),mats.yellowDark,intake,[0,0,0]);
  bowl.rotation.y=Math.PI/4;
  box(intake,[4,.2,4],[0,-.7,0],mats.frame);
  for(const z of [-1.5,1.5])box(intake,[3,.18,.18],[0,.72,z],mats.yellow);
  // Enclosed feed duct joins the receiving pit to the front wall of the mill.
  box(root,[3,.8,6],[7,.5,13],mats.roof);
  const dockX=mineLayout.unloading[0]-mineLayout.plant[0];
  const apron=box(root,[15,.12,20],[dockX,.05,23.5],mats.frame);
  apron.userData.driveable=true;apron.name='unloading-apron';
  const grate=box(root,[9,.04,4],[dockX,.14,20.5],mats.tire);
  grate.name='receiving-grizzly';grate.userData.driveable=true;
  for(let i=0;i<15;i++){
    const bar=box(root,[.14,.02,4],[dockX-4.2+i*.6,.16,20.5],mats.steel);bar.userData.driveable=true;
  }
  for(const x of [-4.65,4.65])box(root,[.25,.02,4.5],[dockX+x,.16,20.5],mats.yellow);
  for(const z of [-2.2,2.2])box(root,[9.5,.02,.25],[dockX,.16,20.5+z],mats.yellow);
  root.userData.unloadingPoint=new THREE.Vector3(...mineLayout.unloading);
  for(const x of [-6.8,6.8])for(let i=0;i<6;i++)box(root,[.35,.03,1],[dockX+x,.13,22+i*1.1],mats.yellow);
  root.userData.hopper=receiver;
  return root;
}

export function createWagon() {
  const group=new THREE.Group();
  box(group,[4.2,.6,8],[0,1.25,0],mats.frame);
  box(group,[4.6,2.25,7.7],[0,2.55,0],mats.roof);
  const cargo=box(group,[4.15,.3,7.25],[0,3.75,0],mats.ore);
  for(const x of [-2.38,2.38])box(group,[.14,2.6,7.9],[x,2.8,0],mats.steel);
  for(const z of [-4,4])box(group,[4.8,2.6,.16],[0,2.8,z],mats.steel);
  const wheels=[];
  for(const z of [-2.5,2.5])for(const x of [-1.9,1.9]) {
    const wheel=cylinder(group,.65,.48,[x,.65,z],mats.tire,10);wheel.rotation.z=Math.PI/2;wheels.push(wheel);
  }
  for(const z of [-4.6,4.6])box(group,[.65,.4,1.1],[0,1.25,z],mats.frame);
  group.userData={cargo,wheels,labelHeight:6};
  return group;
}

export function createLocomotive() {
  const group = new THREE.Group();
  box(group,[4.8,.8,10.8],[0,1.4,0],mats.frame);
  box(group,[4.5,3,6.5],[0,3.2,-1.8],mats.roof);
  box(group,[4.7,4.1,3.4],[0,3.8,3.3],mats.yellow);
  box(group,[3.8,1.4,.15],[0,4.6,5.05],mats.window);
  for(const x of [-2.4,2.4])box(group,[.12,1.3,2.5],[x,4.6,3.4],mats.window);
  for(const z of [-3.6,-2.3,-1,.3])box(group,[4.6,.12,.65],[0,4.9,z],mats.frame);
  for(const z of [-3.4,-1.8,2.1,3.7])for(const x of [-1.9,1.9]) {
    const wheel=cylinder(group,.7,.5,[x,.7,z],mats.tire,10);
    wheel.rotation.z=Math.PI/2;
  }
  for(const x of [-1.5,1.5])box(group,[.5,.4,.15],[x,3.5,5.1],warmWindow);
  box(group,[.7,.5,1.4],[0,1.4,-6],mats.frame);
  return group;
}

export function createStockpile() {
  const group=new THREE.Group();
  const geometry=new THREE.ConeGeometry(1,1,32,1,true);
  geometry.translate(0,.5,0); // Base at zero, apex at height: stable mass scaling.
  const physical=mesh(geometry,material(0x557f72,{metalness:.02}),group);
  // depthTest:false also reveals the smaller book volume during concealment.
  const ghost=mesh(geometry.clone(),new THREE.MeshBasicMaterial({color:0xff7b8e,wireframe:true,transparent:true,opacity:.55,depthWrite:false,depthTest:false}),group);
  ghost.renderOrder=3;
  ghost.castShadow=false;ghost.visible=false;
  const footprint=mesh(new THREE.CylinderGeometry(1,1,.12,48),material(0x617e79),group,[0,.05,0]);
  footprint.scale.set(19.7,1,11.7);
  const hangar=new THREE.Group();hangar.name='stockpile-hangar';group.add(hangar);
  const {width,depth,eaves,ridge}=mineLayout.hangar,half=width/2;
  const beam=(a,b,thickness=.25)=>{
    const start=new THREE.Vector3(...a),end=new THREE.Vector3(...b);
    const member=box(hangar,[thickness,thickness,start.distanceTo(end)],start.clone().lerp(end,.5).toArray(),mats.frame);
    member.lookAt(end);return member;
  };
  for(const z of [-depth/2,0,depth/2]){
    for(const x of [-half,half])beam([x,0,z],[x,eaves,z],.4);
    beam([-half,eaves,z],[0,ridge,z],.4);beam([0,ridge,z],[half,eaves,z],.4);
    beam([-half,eaves,z],[half,eaves,z]);
    for(const x of [-11,11])beam([x,eaves,z],[0,ridge,z]);
  }
  for(const x of [-half,half])for(const y of [eaves,3])beam([x,y,-depth/2],[x,y,depth/2]);
  const shellMaterial=material(0x89bcb8,{transparent:true,opacity:.32,depthWrite:false,side:THREE.DoubleSide,metalness:.05});
  const panel=(size,position,rotation=0)=>{
    const shell=mesh(new THREE.PlaneGeometry(...size),shellMaterial,hangar,position);
    shell.rotation.x=rotation;shell.castShadow=false;shell.renderOrder=1;shell.name='translucent-cladding';return shell;
  };
  for(const x of [-half,half]){const wall=panel([depth,eaves],[x,eaves/2,0]);wall.rotation.y=Math.PI/2;}
  const roofLength=Math.hypot(half,ridge-eaves),pitch=Math.atan2(ridge-eaves,half);
  for(const side of [-1,1]){
    const roof=panel([roofLength,depth],[side*half/2,(eaves+ridge)/2,0],-Math.PI/2);
    roof.rotation.y=side*pitch;
  }
  // End bays remain open for the elevated transfer gallery and visibility.
  const lidars=[];
  for(const position of [[-19,14,-12],[19,14,-12],[0,15,12]]){
    const scanner=new THREE.Group();scanner.name='lidar-scanner';scanner.position.set(...position);hangar.add(scanner);
    scanner.lookAt(0,4,0);
    box(scanner,[1.5,.7,.8],[0,0,0],mats.steel);
    const dome=mesh(new THREE.SphereGeometry(.65,12,8,0,Math.PI*2,0,Math.PI/2),mats.roof,scanner,[0,.3,0]);
    dome.rotation.x=Math.PI/2;
    mesh(new THREE.CylinderGeometry(.4,.4,.2,12),litWindow,scanner,[0,0,.65]).rotation.x=Math.PI/2;
    const ray=mesh(new THREE.ConeGeometry(2.1,8,12,1,true),new THREE.MeshBasicMaterial({color:palette.cyan,transparent:true,opacity:.035,depthWrite:false,side:THREE.DoubleSide}),scanner,[0,0,4.8]);
    ray.rotation.x=-Math.PI/2;ray.castShadow=false;
    lidars.push(scanner);
  }
  const scanRing=mesh(new THREE.TorusGeometry(1,.025,3,64),new THREE.MeshBasicMaterial({color:palette.cyan,transparent:true,opacity:.35}),group,[0,.15,0]);
  scanRing.rotation.x=Math.PI/2;scanRing.scale.set(19.4,11.4,1);scanRing.castShadow=false;
  group.userData={physical,ghost,scanRing,hangar,lidars,labelHeight:25};
  return group;
}

export function createTerrain(scene,road=createHaulRoad()) {
  const group=new THREE.Group();scene.add(group);
  box(group,[214,6,134],[0,-10,0],mats.strata);
  box(group,[213,1.7,133],[0,-6.15,0],mats.edge);
  const rim=[[-105,-65],[105,-65],[105,65],[-105,65]],skirt=[],skirtIndices=[];
  for(let i=0;i<4;i++){
    const [x,z]=rim[i],[nextX,nextZ]=rim[(i+1)%4],k=i*4;
    skirt.push(x,0,z,nextX,0,nextZ,x,-5.3,z,nextX,-5.3,nextZ);
    skirtIndices.push(k,k+1,k+2,k+1,k+3,k+2);
  }
  const skirtGeometry=new THREE.BufferGeometry();skirtGeometry.setAttribute('position',new THREE.Float32BufferAttribute(skirt,3));skirtGeometry.setIndex(skirtIndices);skirtGeometry.computeVertexNormals();
  mesh(skirtGeometry,material(0x788b83,{side:THREE.DoubleSide}),group);
  // A single excavated surface replaces overlapping rings and a solid wall.
  // The entire ramp footprint is cut/filled to the same grade as the road.
  const nx=144,nz=90,positions=[],colors=[],indices=[];
  const terrainColors=[new THREE.Color(palette.sand),new THREE.Color(0x7c8c82),new THREE.Color(0x6c8079),new THREE.Color(0x5c726d),new THREE.Color(palette.soil)];
  for(let row=0;row<=nz;row++)for(let col=0;col<=nx;col++){
    const x=-105+210*col/nx,z=-65+130*row/nz,y=road.groundHeight(x,z);
    positions.push(x,y,z);
    const bench=benchHeight(x,z),index=bench===0?0:bench===-1.3?1:bench===-2.75?2:bench===-4.2?3:4;
    colors.push(...terrainColors[index].toArray());
    if(row<nz&&col<nx){const a=row*(nx+1)+col,b=a+1,c=a+nx+1,d=c+1;indices.push(a,c,b,b,c,d);}
  }
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
  geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));geometry.setIndex(indices);geometry.computeVertexNormals();
  const surface=mesh(geometry,material(0xffffff,{vertexColors:true}),group);surface.name='excavated-terrain';
  group.userData.surface=surface;
  for(let i=0;i<30;i++) {
    const angle=i*2.399963,x=-73+Math.cos(angle)*(3+i%3),z=-25+Math.sin(angle)*(3+i%3);
    const rock=mesh(new THREE.IcosahedronGeometry(.4+(i%4)*.22,0),material(i%2?0x536b67:0x879383),group,[x,road.groundHeight(x,z)+.15,z]);
    rock.scale.y=.5;rock.rotation.set(i,.2*i,.1*i);
  }
  for(const [x,z]of [[-93,44],[-86,48],[-91,36],[84,-46],[91,-42],[75,-49],[87,30]]){
    mesh(new THREE.DodecahedronGeometry(1.6,0),material(0x56796d),group,[x,1.2,z]).scale.y=.7;
    cylinder(group,.13,2.3,[x+3,1.1,z],mats.yellow,6);
  }
  return group;
}

export function createLamp(scene,x,z) {
  const group=new THREE.Group();group.position.set(x,0,z);scene.add(group);
  cylinder(group,.2,11,[0,5.5,0],mats.frame,8);
  box(group,[3.2,.25,.3],[1.45,10.7,0],mats.frame);
  box(group,[1.8,.45,1.1],[2.5,10.4,0],warmWindow);
  const light=new THREE.PointLight(0xffd8a3,55,30,2);light.position.set(2.5,9.7,0);group.add(light);
  return group;
}

export function createConveyor(scene) {
  const start=new THREE.Vector3(...mineLayout.conveyorStart),end=new THREE.Vector3(...mineLayout.conveyorDrop);
  const group=new THREE.Group();scene.add(group);
  const exit=new THREE.Vector3(...mineLayout.conveyorExit);
  const railDrop=new THREE.Vector3(mineLayout.rail[0],10,mineLayout.rail[2]);
  const nodes=[start,new THREE.Vector3(...mineLayout.conveyorEntry),end,exit,railDrop],belts=[];
  for(let i=1;i<nodes.length;i++){
    const a=nodes[i-1],b=nodes[i],mid=a.clone().lerp(b,.5),length=a.distanceTo(b);
    const belt=box(group,[2.8,.5,length],mid.toArray(),mats.frame);belt.lookAt(b);belts.push(belt);
    const cover=box(group,[3.2,.35,length],mid.clone().add(new THREE.Vector3(0,1.8,0)).toArray(),mats.roof);cover.lookAt(b.clone().add(new THREE.Vector3(0,1.8,0)));
    for(const fraction of [.3,.7]){
      const p=a.clone().lerp(b,fraction);
      for(const side of [-2,2])box(group,[.22,p.y,.22],[p.x+side,p.y/2,p.z],mats.steel);
    }
  }
  const chute=box(group,[2.2,1,2.2],[end.x,end.y-1,end.z],mats.yellowDark);
  const dropTube=box(group,[1.4,1,1.4],[end.x,12,end.z],material(0x92bbb0,{transparent:true,opacity:.38,depthWrite:false}));
  const setDropHeight=height=>{
    const bottom=Math.min(end.y-2,mineLayout.stock[1]+height+1);
    dropTube.scale.y=end.y-1-bottom;dropTube.position.y=(end.y-1+bottom)/2;
  };
  setDropHeight(0);
  const ores=[];
  for(let i=0;i<10;i++)ores.push(mesh(new THREE.IcosahedronGeometry(.4,0),mats.ore,group));
  const path=new THREE.CurvePath();
  for(let i=1;i<nodes.length;i++)path.add(new THREE.LineCurve3(nodes[i-1],nodes[i]));
  ores.forEach((ore,i)=>{path.getPointAt(i/ores.length,ore.position);ore.position.y+=.65;});
  return {start,end,exit,railDrop,ores,setDropHeight,belt:belts[0],belts,nodes,path,group,chute};
}

export function createRail() {
  const group=new THREE.Group();
  box(group,[76,.15,6],[0,.05,0],mats.soil);
  for(const z of [-1.5,1.5])box(group,[76,.22,.18],[0,.3,z],mats.steel);
  for(let i=0;i<35;i++)box(group,[.45,.17,5],[-37+i*2.2,.17,0],mats.frame);
  box(group,[48,.35,8],[-2,.25,0],material(0x6f8d93));
  // A compact loading bridge joins the stock edge to the track.
  for(const z of [-3.4,3.4])box(group,[.5,10,.5],[0,5,z],mats.steel);
  for(const x of [-2,2])box(group,[.4,.7,8],[x,10,0],mats.roof);
  for(const z of [-3.4,3.4])box(group,[4,.7,.4],[0,10,z],mats.roof);
  const hopperMaterial=mats.yellowDark.clone();hopperMaterial.side=THREE.DoubleSide;
  const hopper=mesh(new THREE.CylinderGeometry(1.8,.6,2.6,4,1,true),hopperMaterial,group,[0,8.6,0]);
  hopper.rotation.y=Math.PI/4;hopper.name='rail-loading-hopper';
  cylinder(group,.6,2.1,[0,6.3,0],mats.steel,8);
  const locomotive=createLocomotive();locomotive.position.set(...mineLayout.locomotive);locomotive.rotation.y=Math.PI/2;group.add(locomotive);
  group.userData.labelHeight=13;
  return group;
}
