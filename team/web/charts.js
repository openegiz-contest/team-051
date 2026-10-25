import {number, value,isFresh} from './telemetry.js';
import {TruckRoute,loopPoint} from './routes.js';

const colors=['#66ceb8','#d9b373','#91b4e7','#bda8df','#dba5b4','#9bc5a6','#b6bfd4'];
const smoothAlpha=dt=>1-Math.exp(-5*Math.max(0,Math.min(dt,.1)));

/** Geographic rail markers follow the same snapshot/RAF separation as trucks. */
export class GeoMarker {
  update(lat,lon){
    if(!Number.isFinite(lat)||!Number.isFinite(lon))return;
    this.target=[lat,lon];this.current??=[lat,lon];
  }
  advance(dt){
    if(!this.current)return;
    const alpha=smoothAlpha(dt);
    this.current=this.current.map((value,i)=>value+(this.target[i]-value)*alpha);
  }
  synchronize(){if(this.target)this.current=[...this.target];}
}

function markerLabel(ctx,text,x,y,w,color){
  ctx.font='600 13px Inter, Segoe UI, sans-serif';
  const width=ctx.measureText(text).width+18,height=28;
  const left=Math.max(6,Math.min(w-width-6,x-width/2));
  ctx.beginPath();ctx.roundRect(left,y-height/2,width,height,4);
  ctx.fillStyle='#101c2bf2';ctx.fill();ctx.strokeStyle=color;ctx.lineWidth=1;ctx.stroke();
  ctx.fillStyle='#edf3fc';ctx.textAlign='center';ctx.textBaseline='middle';
  ctx.fillText(text,left+width/2,y);ctx.textBaseline='alphabetic';
}
function surface(canvas) {
  const ratio=Math.min(window.devicePixelRatio||1,2),w=canvas.clientWidth,h=canvas.clientHeight;
  if(!w||!h)return null;
  if(canvas.width!==Math.round(w*ratio)||canvas.height!==Math.round(h*ratio)){canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);}
  const ctx=canvas.getContext('2d');ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,w,h);
  return {ctx,w,h};
}
export function drawSparkline(element,rows,key) {
  if(!rows.length)return;
  const values=rows.slice(-60).map(r=>r[key]),min=Math.min(...values),max=Math.max(...values);
  const points=values.length===1?'0,15 94,15':values.map((v,i)=>`${i/(values.length-1)*94},${21-(v-min)/Math.max(1,max-min)*17}`).join(' ');
  const line=document.createElementNS('http://www.w3.org/2000/svg','polyline');line.setAttribute('points',points);element.replaceChildren(line);
}

export function drawHistory(canvas,rows) {
  const s=surface(canvas);if(!s)return;const {ctx,w,h}=s;
  const pad={left:w<500?72:88,right:25,top:27,bottom:38};
  ctx.font='13px Inter, Segoe UI, sans-serif';
  if(!rows.length){ctx.fillStyle='#a5b7cc';ctx.fillText('Ожидаем первые измерения',20,h/2);return;}
  const min=Math.min(...rows.flatMap(r=>[r.physical_stock,r.book_stock]))*.96,max=Math.max(...rows.flatMap(r=>[r.physical_stock,r.book_stock]))*1.04;
  const low=Math.max(0,min),high=Math.max(max,low+20),plotW=w-pad.left-pad.right,plotH=h-pad.top-pad.bottom;
  ctx.textAlign='right';
  for(let i=0;i<4;i++) {
    const y=pad.top+i*plotH/3;ctx.strokeStyle='#b4cdea0c';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(pad.left,y);ctx.lineTo(w-pad.right,y);ctx.stroke();
    ctx.fillStyle='#9aadc4';ctx.fillText(number(high-i*(high-low)/3,0),pad.left-12,y+4);
  }
  const time0=rows[0].time,time1=Math.max(time0+1,rows.at(-1).time);
  const point=(r,key)=>[pad.left+(r.time-time0)/(time1-time0)*plotW,pad.top+(high-r[key])/(high-low)*plotH];
  const trace=key=>{ctx.beginPath();rows.forEach((r,i)=>{const p=point(r,key);i?ctx.lineTo(...p):ctx.moveTo(...p);});};
  const gradient=ctx.createLinearGradient(0,pad.top,0,h);gradient.addColorStop(0,'#5ed5bb18');gradient.addColorStop(1,'#5ed5bb00');
  trace('physical_stock');const end=point(rows.at(-1),'physical_stock');ctx.lineTo(end[0],h-pad.bottom);ctx.lineTo(pad.left,h-pad.bottom);ctx.closePath();ctx.fillStyle=gradient;ctx.fill();
  for(const [key,color]of [['physical_stock','#69cbb6'],['book_stock','#e59aa5']]) {
    trace(key);ctx.strokeStyle=color;ctx.lineWidth=1.8;ctx.lineJoin='round';ctx.stroke();
    const p=point(rows.at(-1),key);ctx.beginPath();ctx.arc(...p,3,0,Math.PI*2);ctx.fillStyle=color;ctx.fill();
  }
  ctx.textAlign='center';ctx.fillStyle='#9aadc4';
  for(let i=0;i<4;i++){const t=time0+(time1-time0)*i/3;ctx.fillText(`${Math.floor(t/3600)}:${String(Math.floor(t%3600/60)).padStart(2,'0')}`,pad.left+i*plotW/3,h-10);}
}

export class LogisticsMap {
  constructor(canvas,extent){
    this.canvas=canvas;this.extent=extent;this.trails=new Map();this.trucks=new Map();this.railMarkers=new Map();this.mineTrails=new Map();this.selected=null;
    this.onExtent=()=>this.draw();extent.addEventListener('change',this.onExtent);
    this.active=false;this.running=false;this.tick=this.tick.bind(this);
    this.onVisibilityChange=()=>this.setRunning(this.active&&!document.hidden);
    document.addEventListener('visibilitychange',this.onVisibilityChange);this.setActive(true);
  }
  update(data){
    this.data={...data,clientReceivedAt:Date.now()};
    for(const name of Object.keys(data.twins).filter(name=>/^(Truck|Wagon)_/.test(name))) {
      if(name.startsWith('Truck')){
        if(!this.trucks.has(name))this.trucks.set(name,new TruckRoute());
        this.trucks.get(name).update(data,name);
        continue;
      }
      if(!this.railMarkers.has(name))this.railMarkers.set(name,new GeoMarker());
      this.railMarkers.get(name).update(value(data,name,'lat'),value(data,name,'lon'));
    }
    if(this.syncOnNextUpdate&&isFresh(this.data)&&(data.revision===undefined||data.revision!==this.resumeRevision)){
      this.synchronize();this.syncOnNextUpdate=false;
    }
  }
  select(name){this.selected=name;this.draw();}
  setActive(active){
    this.active=active;this.setRunning(active&&!document.hidden);
  }
  synchronize(){
    for(const truck of this.trucks.values())truck.synchronize();
    for(const marker of this.railMarkers.values())marker.synchronize();
    this.mineTrails.clear();this.lastFrame=performance.now();
  }
  setRunning(active){
    if(this.running===active)return;
    this.running=active;
    if(active){this.resumeRevision=this.data?.revision;this.syncOnNextUpdate=true;this.synchronize();this.frameId=requestAnimationFrame(this.tick);}
    else cancelAnimationFrame(this.frameId);
  }
  tick(now){
    if(!this.running)return;
    const dt=Math.max(0,Math.min(.1,(now-this.lastFrame)/1000));this.lastFrame=now;
    if(this.data&&isFresh(this.data))for(const [name,truck]of this.trucks){
      truck.advance(dt);
      const trail=this.mineTrails.get(name)||[],last=trail.at(-1);
      if(!last||Math.abs(last.phase-truck.phase)+Math.abs(last.queue-truck.queue)>.008)trail.push({phase:truck.phase,queue:truck.queue});
      while(trail.length>1&&trail[0].phase<truck.phase-.65)trail.shift();
      if(trail.length>180)trail.shift();this.mineTrails.set(name,trail);
    }
    if(this.data&&isFresh(this.data))for(const [name,marker]of this.railMarkers){
      marker.advance(dt);
      if(!marker.current)continue;
      const line=this.trails.get(name)||[],last=line.at(-1);
      if(!last||last.some((v,i)=>Math.abs(v-marker.current[i])>1e-7))line.push([...marker.current]);
      if(line.length>900)line.shift();this.trails.set(name,line);
    }
    this.draw();this.frameId=requestAnimationFrame(this.tick);
  }
  dispose(){this.setActive(false);this.extent.removeEventListener('change',this.onExtent);document.removeEventListener('visibilitychange',this.onVisibilityChange);}
  drawMine(ctx,w,h){
    const radius=Math.min(52,Math.max(42,w*.065)),length=Math.max(70,w-2*(radius+60));
    const origin=[(w-length)/2,h/2],point=(phase,queue=0)=>{
      const p=loopPoint(phase,length,radius,queue);return {...p,x:p.x+origin[0],y:p.y+origin[1]};
    };
    ctx.font='500 14px Inter, Segoe UI, sans-serif';
    ctx.strokeStyle='#a8c8e408';ctx.lineWidth=1;
    for(let x=25;x<w;x+=40){ctx.beginPath();ctx.moveTo(x,20);ctx.lineTo(x,h-20);ctx.stroke();}
    for(let y=25;y<h;y+=40){ctx.beginPath();ctx.moveTo(15,y);ctx.lineTo(w-15,y);ctx.stroke();}
    const trace=()=>{
      ctx.beginPath();
      for(let i=0;i<=200;i++){const p=point(i/50);i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y);}
      ctx.closePath();
    };
    trace();ctx.strokeStyle='#5ed5bb12';ctx.lineWidth=20;ctx.stroke();
    trace();ctx.strokeStyle='#7bdccaa0';ctx.lineWidth=3;ctx.stroke();
    const arrow=(phase)=>{
      const p=point(phase);ctx.save();ctx.translate(p.x,p.y);ctx.rotate(Math.atan2(p.dy,p.dx));
      ctx.beginPath();ctx.moveTo(5,0);ctx.lineTo(-4,-3);ctx.lineTo(-4,3);ctx.closePath();ctx.fillStyle='#8bc9bd';ctx.fill();ctx.restore();
    };
    for(const phase of [.3,.7,1.5,2.3,2.7,3.5])arrow(phase);
    ctx.textAlign='center';ctx.fillStyle='#b1c7d8';
    ctx.fillText('С рудой →',w/2,origin[1]-radius-110);
    ctx.fillText('← Порожний рейс',w/2,origin[1]+radius+70);
    for(const [x,title,coords]of [[origin[0],'Карьер','52.100° · 63.100°'],[origin[0]+length,'Фабрика','52.120° · 63.130°']]){
      ctx.font='600 14px Inter, Segoe UI, sans-serif';ctx.fillStyle='#e3edf6';ctx.fillText(title,x,origin[1]-radius-88);
      ctx.font='13px Inter, Segoe UI, sans-serif';ctx.fillStyle='#9cb2c9';ctx.fillText(coords,x,origin[1]-radius-68);
      ctx.beginPath();ctx.arc(x,origin[1],8,0,Math.PI*2);ctx.fillStyle='#24424b';ctx.fill();ctx.strokeStyle='#76aca9';ctx.lineWidth=2.5;ctx.stroke();
    }
    let index=0;
    for(const [name,truck]of this.trucks){
      const color=colors[index++%colors.length],trail=this.mineTrails.get(name)||[];
      ctx.beginPath();
      trail.forEach((sample,i)=>{
        if(!i){const p=point(sample.phase,sample.queue);ctx.moveTo(p.x,p.y);return;}
        const previous=trail[i-1],steps=Math.max(1,Math.ceil((sample.phase-previous.phase)/.015));
        for(let step=1;step<=steps;step++){
          const t=step/steps,p=point(previous.phase+(sample.phase-previous.phase)*t,previous.queue+(sample.queue-previous.queue)*t);
          ctx.lineTo(p.x,p.y);
        }
      });
      ctx.strokeStyle=color;ctx.globalAlpha=.45;ctx.lineWidth=3;ctx.stroke();ctx.globalAlpha=1;
      const p=point(truck.currentP,truck.queue),outside=value(this.data,name,'geofence')==='OUTSIDE';
      ctx.save();ctx.translate(p.x,p.y);ctx.rotate(Math.atan2(p.dy,p.dx));
      ctx.fillStyle=outside?'#f7848f':color;ctx.shadowColor=ctx.fillStyle;ctx.shadowBlur=8;
      ctx.beginPath();ctx.roundRect(-8,-5.5,16,11,3);ctx.fill();ctx.strokeStyle='#0e1a28';ctx.lineWidth=2.5;ctx.stroke();ctx.shadowBlur=0;
      ctx.fillStyle='#152533';ctx.fillRect(2,-3,3,6);ctx.restore();
      if(this.selected===name){ctx.beginPath();ctx.arc(p.x,p.y,14,0,Math.PI*2);ctx.strokeStyle=color;ctx.lineWidth=3;ctx.stroke();}
      // Stable per-vehicle label sides also avoid a 180° label flip mid-turn.
      markerLabel(ctx,name.toLowerCase().replace('_','-'),p.x,p.y+(index%2?-28:28),w,color);
    }
  }
  draw(){
    const s=surface(this.canvas);if(!s||!this.data)return;
    const {ctx,w,h}=s,rail=this.extent.value==='rail';
    if(!rail){this.drawMine(ctx,w,h);return;}
    const bounds=rail?[52.10,52.73,63.105,63.16]:[52.096,52.125,63.091,63.145];
    const cos=Math.cos(52.1*Math.PI/180),scale=Math.min((w-100)/((bounds[3]-bounds[2])*cos),(h-60)/(bounds[1]-bounds[0]));
    const project=([lat,lon])=>[w/2+(lon-(bounds[2]+bounds[3])/2)*cos*scale,h/2-(lat-(bounds[0]+bounds[1])/2)*scale];
    ctx.font='13px Inter, Segoe UI, sans-serif';ctx.textAlign='left';
    const step=rail?.1:.005;
    for(let lat=Math.ceil(bounds[0]/step)*step;lat<bounds[1];lat+=step){const y=project([lat,bounds[2]])[1];ctx.strokeStyle='#a8c8e40a';ctx.lineWidth=1;ctx.beginPath();ctx.moveTo(72,y);ctx.lineTo(w-18,y);ctx.stroke();ctx.fillStyle='#91aac3';ctx.fillText(lat.toFixed(3)+'°',12,y+4);}
    const route=rail?[[52.12,63.13],[52.12+65/111.32,63.132]]:[[52.10,63.10],[52.12,63.13]];
    const [start,end]=route.map(project),stroke=(color,width)=>{ctx.strokeStyle=color;ctx.lineWidth=width;ctx.lineCap='round';ctx.beginPath();ctx.moveTo(...start);ctx.lineTo(...end);ctx.stroke();};
    stroke('#5ed5bb12',Math.max(12,300/111320*scale));stroke('#5ed5bb99',3);
    ctx.setLineDash([3,7]);stroke('#b6ddd877',1);ctx.setLineDash([]);
    route.forEach((p,i)=>{const [x,y]=project(p);ctx.beginPath();ctx.arc(x,y,6,0,Math.PI*2);ctx.fillStyle='#254e51';ctx.fill();ctx.strokeStyle='#80c9c4';ctx.lineWidth=3;ctx.stroke();ctx.fillStyle='#d0e1ef';ctx.fillText((rail?['ЖД-весовая','Пункт разгрузки · 65 км']:['Забой','Фабрика'])[i],Math.max(74,Math.min(w-190,x+12)),y-16);});
    let index=0;
    for(const [name,trail]of this.trails){
      if(name.startsWith('Wagon')!==rail)continue;
      const color=colors[index++%colors.length];ctx.strokeStyle=color;ctx.lineWidth=2.5;ctx.globalAlpha=.5;ctx.beginPath();trail.forEach((p,i)=>i?ctx.lineTo(...project(p)):ctx.moveTo(...project(p)));ctx.stroke();ctx.globalAlpha=1;
      const [x,y]=project(this.railMarkers.get(name).current);
      ctx.beginPath();ctx.arc(x,y,this.selected===name?8:5,0,Math.PI*2);ctx.fillStyle=value(this.data,name,'geofence')==='OUTSIDE'?'#f7848f':color;ctx.shadowColor=color;ctx.shadowBlur=10;ctx.fill();ctx.shadowBlur=0;
      markerLabel(ctx,name.toLowerCase().replace('_','-'),x+(index%2?62:-62),Math.max(18,Math.min(h-18,y+((index-1)%3-1)*32)),w,color);
    }
  }
}
