import * as THREE from './vendor/three.module.js';
import {mineLayout} from './layout.js';
import {value,interpolationAlpha} from './telemetry.js';
import {TruckRoute} from './routes.js';
import {wrapProgress} from './haul-road.js';

const UP=new THREE.Vector3(0,1,0);
const lookAhead=new THREE.Vector3(),lookMatrix=new THREE.Matrix4();

function curvePose(motion,progress,position,quaternion) {
  const t=wrapProgress(progress),curve=motion.road.curve;
  curve.getPointAt(t,position);curve.getPointAt(wrapProgress(t+.005),lookAhead);
  lookMatrix.lookAt(lookAhead,position,UP);quaternion.setFromRotationMatrix(lookMatrix);
}

/** Resume from current telemetry, discarding both missed laps and velocity. */
export function synchronizeEquipment(name,entry) {
  if(name.startsWith('Truck')&&entry.motion){
    entry.motion.progress=entry.motion.targetProgress;entry.motion.speed=0;
    entry.motion.dwell=0;entry.motion.unloading=false;entry.motion.age=0;
    entry.route.resetClock();
    entry.motion.nextDock=Math.floor(entry.motion.progress-entry.motion.road.dock+1e-8)+1+entry.motion.road.dock;
    if(entry.route.state==='unloading'){
      entry.motion.nextDock=entry.motion.progress;entry.motion.unloading=true;
    }
    curvePose(entry.motion,entry.motion.progress,entry.targetPosition,entry.targetQuaternion);
  }
  if(name.startsWith('Truck')||name==='Excavator_01'){
    entry.object.position.copy(entry.targetPosition);
    entry.object.quaternion.copy(entry.targetQuaternion);
  }
}

/** Telemetry changes targets only; fixed loading-train geometry ignores GPS. */
export function updateEquipmentTarget(name,entry,data,road) {
  if(name.startsWith('Truck')) {
    entry.route??=new TruckRoute();entry.route.update(data,name);
    const target=road.phaseToProgress(entry.route.targetPhase-(entry.route.state==='queued'?.25:0));
    entry.motion??={progress:target,targetProgress:target,speed:0,age:0,road};
    entry.motion.nextDock??=Math.ceil(target-road.dock-1e-8)+road.dock;
    entry.motion.targetProgress=Math.max(entry.motion.targetProgress,target);
    // Re-anchor prediction only for a distinct telemetry snapshot. A duplicate
    // HTTP response must not repeatedly rewind the prediction clock.
    const stamp=`${entry.route.lastTime}:${target}:${entry.route.state}:${entry.route.speedKmh}`;
    if(stamp!==entry.motion.stamp){entry.motion.age=0;entry.motion.stamp=stamp;}
    // Telemetry updates destinations only. The displayed progress never resets.
    curvePose(entry.motion,entry.motion.targetProgress,entry.targetPosition,entry.targetQuaternion);
  } else if(name==='Excavator_01') {
    // Stationary in the present model; optional future GPS is relative to PIT.
    const lat=value(data,name,'lat',null),lon=value(data,name,'lon',null);
    if(Number.isFinite(lat)&&Number.isFinite(lon)) {
      entry.targetPosition.set(mineLayout.excavator[0]+(lon-63.10)*1600,mineLayout.excavator[1],mineLayout.excavator[2]+(lat-52.10)*1100);
    }
    const heading=value(data,name,'heading',null);
    if(Number.isFinite(heading))entry.targetQuaternion.setFromAxisAngle(UP,heading*Math.PI/180);
  }
}

export function advanceEquipment(name,entry,dt) {
  if(name.startsWith('Truck')&&entry.motion){
    const motion=entry.motion,delta=Math.max(0,Math.min(dt,.1)),route=entry.route;
    if(route.maintenance){
      motion.speed=Math.max(0,motion.speed-12*delta);motion.unloading=false;
      motion.progress+=Math.min(Math.max(0,motion.targetProgress-motion.progress),motion.speed*delta/motion.road.length);
      curvePose(motion,motion.progress,entry.object.position,entry.object.quaternion);return;
    }
    motion.age+=delta;
    const moving=route.state==='hauling'||route.state==='returning';
    const measured=Number.isFinite(route.speedKmh);
    const cycle=Math.floor(route.targetPhase/4);
    const start=motion.road.phaseToProgress(cycle*4+(route.state==='returning'?2:0));
    const end=motion.road.phaseToProgress(cycle*4+(route.state==='returning'?3:1));
    // Convert km/h to phase speed on this schematic route (360 model seconds
    // per leg at 30 km/h). The measured model clock also supports SIM_SPEEDUP.
    const rate=moving&&measured?(end-start)/360*(route.clockRate??1)*Math.max(0,route.speedKmh)/30:0;
    const predicted=moving&&measured?Math.min(end,motion.targetProgress+rate*Math.min(motion.age,2.5)):motion.targetProgress;
    // Every displayed loaded pass visits the receiver, even if a poll skipped
    // the short unloading state. Dwell belongs to the visual route, not mass accounting.
    const stopping=motion.nextDock<=motion.targetProgress+1e-8||(moving&&measured&&route.state==='hauling'&&motion.nextDock<=end+1e-8);
    const destination=stopping?motion.nextDock:moving&&measured?end:motion.targetProgress;
    const gap=Math.max(0,destination-motion.progress);
    motion.unloading=stopping&&gap*motion.road.length<.02;
    if(motion.unloading){
      motion.progress=destination;motion.speed=0;motion.dwell=(motion.dwell||0)+delta;
      if(motion.dwell>=1.6&&(route.state==='returning'||motion.targetProgress>destination+1e-8)){
        motion.nextDock+=1;motion.dwell=0;motion.unloading=false;
      }
      curvePose(motion,motion.progress,entry.object.position,entry.object.quaternion);return;
    }
    // Cruise between packets; position error only trims velocity. Unlike a
    // target chase, reaching the last observed GPS point does not brake to zero.
    const cruise=rate*motion.road.length,error=(predicted-motion.progress)*motion.road.length;
    const correction=Math.max(-cruise*.5,Math.min(cruise*.5,error*1.2));
    const desired=moving&&measured?Math.min(18,Math.max(0,cruise+correction),Math.sqrt(24*gap*motion.road.length)):Math.min(18,gap*motion.road.length*3);
    const maxChange=12*delta;
    motion.speed+=Math.max(-maxChange,Math.min(maxChange,desired-motion.speed));
    motion.progress+=Math.min(gap,motion.speed*delta/motion.road.length);
    curvePose(motion,motion.progress,entry.object.position,entry.object.quaternion);
  } else if(name==='Excavator_01') {
    const alpha=interpolationAlpha(dt);
    entry.object.position.lerp(entry.targetPosition,alpha);
    entry.object.quaternion.slerp(entry.targetQuaternion,alpha);
  }
}
