import {value,truckProgress,interpolationAlpha} from './telemetry.js';

const clamp = p => Math.max(0,Math.min(1,p));
const segments = {hauling:0,unloading:1,returning:2,loading:3,queued:3};
export const routeSegment = phase => {
  const wrapped=((phase%4)+4)%4;
  return {segment:Math.floor(wrapped),p:wrapped%1};
};

/** A continuous clockwise stadium, with a separate waiting bay at the pit.
 * phase 0..1 = loaded lane; 1..2 = plant turn; 2..3 = empty lane;
 * 3..4 = pit turn. Coordinates remain schematic; GPS supplies progress.
 */
export function loopPoint(phase,length=1,radius=.15,queue=0) {
  const {segment,p}=routeSegment(phase);
  let x,y,dx,dy;
  if(segment===0){x=p*length;y=-radius;dx=1;dy=0;}
  else if(segment===2){x=(1-p)*length;y=radius;dx=-1;dy=0;}
  else {
    const angle=(segment===1?-Math.PI/2:Math.PI/2)+p*Math.PI;
    x=(segment===1?length:0)+radius*Math.cos(angle);y=radius*Math.sin(angle);
    dx=-Math.sin(angle);dy=Math.cos(angle);
  }
  // Only a waiting vehicle uses this bay. It rejoins by interpolation.
  if(queue){x-=radius*.8*queue;y+=radius*1.25*queue;}
  return {x,y,dx,dy};
}

export class TruckRoute {
  // Keep unwrapped phases across laps: lerping wrapped 0..1 values cuts across
  // the entire loop at its seam. Snapshots never overwrite currentP.
  get phase(){return this.currentP;}
  set phase(p){this.currentP=p;}
  get targetPhase(){return this.targetP;}
  set targetPhase(p){this.targetP=p;}
  resetClock(){this.clockSample=null;}
  update(data,name,now=performance.now()/1000) {
    const reported=value(data,name,'state','loading');
    this.maintenance=String(reported).toLowerCase()==='maintenance';
    const state=this.maintenance?value(data,name,'operating_state',this.state||'loading'):reported;
    const time=Number(data.virtual_time??value(data,name,'simulation_time',0));
    this.speedKmh=value(data,name,'speed',null);
    // Infer the accelerated model clock from distinct platform snapshots, not
    // HTTP poll frequency. Repeated revisions must not inflate this rate.
    const sample=this.clockSample;
    if(sample&&time>sample.time&&now-sample.wall>=.1&&now-sample.wall<15){
      const rate=Math.max(.1,Math.min(3600,(time-sample.time)/(now-sample.wall)));
      this.clockRate=this.clockRate===undefined?rate:this.clockRate+(rate-this.clockRate)*.25;
    }
    if(!sample||time!==sample.time)this.clockSample={time,wall:now};
    if(state!==this.state)this.since=time;
    const elapsed=value(data,name,'elapsed',null);
    const age=Number.isFinite(elapsed)?Math.max(0,elapsed):Math.max(0,time-this.since);
    const gps=truckProgress(value(data,name,'lat',52.10),value(data,name,'lon',63.10));
    let p=0;
    if(state==='hauling')p=gps;
    else if(state==='returning')p=1-gps;
    else if(state==='unloading')p=clamp(age/40);
    else if(state==='loading')p=clamp(value(data,name,'weight')/90+(Number.isFinite(elapsed)?elapsed/120:0));
    const segment=segments[state]??3;
    const raw=segment+p;
    const trips=value(data,name,'trips',null);
    let target=raw;
    if(Number.isFinite(trips)) {
      target+=4*(trips-(segment>=2?1:0));
      if(this.lastTime!==undefined&&time<this.lastTime)this.cycleOffset=4*Math.ceil((this.targetPhase-target)/4);
      target+=this.cycleOffset||0;
    } else if(this.targetPhase!==undefined) {
      target+=4*Math.floor(this.targetPhase/4);
      if(target<this.targetPhase-1e-8&&segment<(segments[this.state]??3))target+=4;
    }
    // Duplicate or slightly noisy GPS snapshots must never send a truck back.
    this.targetPhase=this.targetPhase===undefined?target:Math.max(this.targetPhase,target);
    this.phase??=this.targetPhase;
    this.targetQueue=state==='queued'?1:0;
    this.queue??=this.targetQueue;
    this.state=state;this.lastTime=time;
  }
  advance(dt) {
    if(this.maintenance)return;
    const delta=Math.max(0,Math.min(dt,.1)),alpha=1-Math.exp(-5*delta);
    this.currentP+=(this.targetP-this.currentP)*alpha;
    this.queue+=(this.targetQueue-this.queue)*interpolationAlpha(delta);
  }
  synchronize() {
    if(this.targetPhase===undefined)return;
    this.phase=this.targetPhase;this.queue=this.targetQueue;
    this.resetClock();
  }
}
