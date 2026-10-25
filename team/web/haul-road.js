import * as THREE from './vendor/three.module.js';
import {haulLayout} from './layout.js';

const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
const smooth=p=>p*p*(3-2*p);
export const wrapProgress=t=>((t%1)+1)%1;

export function benchHeight(x,z) {
  const radius=Math.hypot((x-haulLayout.pitCenter[0])/haulLayout.pitRadii[0],(z-haulLayout.pitCenter[1])/haulLayout.pitRadii[1]);
  return radius>=1?0:radius>.82?-1.3:radius>.63?-2.75:radius>.45?-4.2:haulLayout.floor;
}

class RampCurve extends THREE.CatmullRomCurve3 {
  getPoint(t,target=new THREE.Vector3()) {
    super.getPoint(t,target);
    target.y=clamp(target.y,haulLayout.floor,0);
    return target;
  }
}

/** One geometry model supplies the terrain cut, road surface and truck path. */
export function createHaulRoad() {
  const centerline=new RampCurve(haulLayout.ramp.map(p=>new THREE.Vector3(...p)),false,'centripetal');
  centerline.arcLengthDivisions=800;
  const samples=centerline.getSpacedPoints(160);
  const normalAt=t=>{const d=centerline.getTangentAt(t);return new THREE.Vector3(-d.z,0,d.x).normalize();};
  const lane=(t,side)=>centerline.getPointAt(t).addScaledVector(normalAt(t),side*haulLayout.laneOffset).add(new THREE.Vector3(0,haulLayout.roadLift+.025,0));
  const n=80,cap=24,points=[];
  for(let i=0;i<=n;i++)points.push(lane(i/n,1));
  const turn=(end,side,i)=>{
    const point=centerline.getPointAt(end),normal=normalAt(end),direction=centerline.getTangentAt(end).setY(0).normalize();
    const angle=Math.PI*i/cap;
    return point.addScaledVector(normal,side*haulLayout.laneOffset*Math.cos(angle))
      .addScaledVector(direction,side*haulLayout.laneOffset*Math.sin(angle))
      .add(new THREE.Vector3(0,haulLayout.roadLift+.025,0));
  };
  for(let i=1;i<=cap;i++)points.push(turn(1,1,i));
  for(let i=n-1;i>=0;i--)points.push(lane(i/n,-1));
  for(let i=1;i<cap;i++)points.push(turn(0,-1,i));
  const curve=new THREE.CatmullRomCurve3(points,true,'centripetal');
  const resolution=64;
  curve.arcLengthDivisions=points.length*resolution;
  const lengths=curve.getLengths(),total=lengths.at(-1);
  // Hauling ends at the middle of the factory turn, beside the receiver.
  // The complete unloading phase is stationary; returning starts there too.
  const dock=lengths[(n+cap/2)*resolution]/total;
  const stops=[0,dock,dock,lengths[(2*n+cap)*resolution]/total,1];
  const phaseToProgress=phase=>{
    const cycle=Math.floor(phase/4),local=phase-cycle*4,index=Math.min(3,Math.floor(local));
    return cycle+stops[index]+(stops[index+1]-stops[index])*(local-index);
  };
  const groundHeight=(x,z)=>{
    let distance=Infinity,height=0;
    for(let i=1;i<samples.length;i++){
      const a=samples[i-1],b=samples[i],dx=b.x-a.x,dz=b.z-a.z;
      const p=clamp(((x-a.x)*dx+(z-a.z)*dz)/(dx*dx+dz*dz),0,1);
      const d=Math.hypot(x-a.x-dx*p,z-a.z-dz*p);
      if(d<distance){distance=d;height=a.y+(b.y-a.y)*p;}
    }
    const base=benchHeight(x,z),blend=1-smooth(clamp((distance-haulLayout.halfWidth)/3,0,1));
    return base+(height-base)*blend;
  };
  return {centerline,curve,phaseToProgress,groundHeight,normalAt,length:total,dock};
}
