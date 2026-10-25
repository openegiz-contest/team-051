/** Schematic site coordinates, shared by geometry, telemetry targets and tests. */
export const mineLayout = Object.freeze({
  plant: [5, 0, -20],
  plantScale: 1.6,
  stock: [54, .17, -17],
  hangar: {width:44,depth:30,eaves:16,ridge:22},
  unloading: [0, .175, .5],
  hopper: [0, -1.15, .5],
  millIntake: [12, .9, -8],
  excavator: [-76, -4.5, -25],
  conveyorStart: [13, 16, -35.9],
  conveyorEntry: [54, 19, -35.9],
  conveyorDrop: [54, 19, -17],
  conveyorExit: [54, 19, -1],
  rail: [54, 0, 21],
  wagonSpacing: 9.4,
  locomotive: [31, .25, 0]
});

export const haulLayout=Object.freeze({
  laneOffset:5.5,
  halfWidth:9.2,
  roadLift:.15,
  floor:-4.9,
  pitCenter:[-62,-24],
  pitRadii:[34,29],
  // Broad northern/eastern switchback: bend radius exceeds road half-width.
  ramp:[[-64,-4.9,-23],[-64,-4.7,-33],[-59,-4.2,-42],[-49,-3.5,-46],
    [-39,-2.5,-43],[-33,-1.3,-35],[-33.5,-.3,-24],[-39,0,-12],
    [-39,0,8],[-39,0,18],[-36.39,0,27.75],[-29.25,0,34.89],
    [-19.5,0,37.5],[-9.75,0,34.89],[-2.61,0,27.75],[0,0,18],[0,0,6]]
});

export const wagonPosition = index => [
  mineLayout.rail[0] + 18.8 - index * mineLayout.wagonSpacing,
  .25,
  mineLayout.rail[2]
];

export const views=Object.freeze(['mine','map','audit']);
export function navigationLayout(search,inFrame=false) {
  const params=new URLSearchParams(search),requested=params.get('view');
  return {view:views.includes(requested)?requested:'mine',
    tabs:!inFrame,
    embedded:inFrame};
}
