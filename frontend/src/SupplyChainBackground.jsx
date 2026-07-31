/**
 * SupplyChainBackground — a layered 3D container-yard scene behind the chat.
 *
 * Built from CSS 3D transforms rather than WebGL: three.js would add several
 * hundred KB plus a render loop to what is otherwise a ~195KB text app, for
 * a background nobody interacts with. Real `perspective` + `rotateX/rotateY`
 * + `translateZ` gives genuine depth at ~2KB, and every animation drives
 * only transform/opacity so it stays on the GPU and never triggers layout.
 *
 * The scene reads as a port/warehouse yard — stacked freight containers,
 * racking, a receding floor grid — with a glowing node network layered over
 * it for the "intelligence" half of Intelligence Reporting System.
 *
 * Composition: density is pushed to the LEFT and RIGHT thirds, because the
 * chat column occupies the middle 820px. That keeps the periphery full
 * without ever putting busy geometry behind the text being read.
 *
 * Decorative only: aria-hidden, pointer-events:none, and fully static under
 * prefers-reduced-motion.
 */

// Freight container stacks. `tone` picks the face palette; `z` sets depth,
// which drives both apparent size and atmospheric haze.
const STACKS = [
  // -- left yard -------------------------------------------------------
  { x: "1%", y: "52%", w: 132, boxes: 3, tone: "navy", z: -120, dur: 34, delay: 0 },
  { x: "9%", y: "70%", w: 104, boxes: 2, tone: "gold", z: -230, dur: 29, delay: -7 },
  { x: "3%", y: "20%", w: 92, boxes: 2, tone: "teal", z: -330, dur: 38, delay: -14 },
  { x: "14%", y: "34%", w: 76, boxes: 3, tone: "navy", z: -400, dur: 31, delay: -3 },
  { x: "19%", y: "82%", w: 68, boxes: 1, tone: "slate", z: -470, dur: 36, delay: -19 },
  { x: "7%", y: "88%", w: 88, boxes: 1, tone: "teal", z: -180, dur: 27, delay: -11 },
  // -- right yard ------------------------------------------------------
  { x: "84%", y: "48%", w: 138, boxes: 3, tone: "gold", z: -110, dur: 32, delay: -5 },
  { x: "77%", y: "72%", w: 100, boxes: 2, tone: "navy", z: -250, dur: 37, delay: -16 },
  { x: "90%", y: "24%", w: 86, boxes: 2, tone: "navy", z: -350, dur: 30, delay: -9 },
  { x: "73%", y: "26%", w: 72, boxes: 1, tone: "teal", z: -450, dur: 35, delay: -22 },
  { x: "88%", y: "86%", w: 94, boxes: 2, tone: "slate", z: -200, dur: 28, delay: -13 },
  { x: "68%", y: "88%", w: 64, boxes: 1, tone: "gold", z: -500, dur: 33, delay: -2 },
  // -- deep centre, far enough back to sit behind the chat panel -------
  { x: "38%", y: "12%", w: 54, boxes: 2, tone: "navy", z: -680, dur: 40, delay: -6 },
  { x: "58%", y: "10%", w: 48, boxes: 1, tone: "teal", z: -720, dur: 42, delay: -18 },
];

// Warehouse racking silhouettes — vertical uprights with shelf beams, set
// far back so they read as structure rather than detail.
const RACKS = [
  { x: "24%", y: "44%", w: 120, h: 150, z: -620, delay: 0 },
  { x: "63%", y: "40%", w: 140, h: 170, z: -660, delay: -8 },
  { x: "12%", y: "6%", w: 100, h: 120, z: -700, delay: -15 },
];

// Glowing network nodes + the links between them (the "intelligence" layer).
const NODES = [
  { x: 6, y: 30 }, { x: 15, y: 58 }, { x: 4, y: 78 }, { x: 21, y: 20 },
  { x: 24, y: 68 }, { x: 79, y: 26 }, { x: 92, y: 44 }, { x: 83, y: 66 },
  { x: 70, y: 52 }, { x: 95, y: 76 }, { x: 33, y: 88 }, { x: 66, y: 84 },
];
const LINKS = [
  [0, 1], [1, 2], [0, 3], [1, 4], [3, 4], [2, 10],
  [5, 6], [6, 7], [5, 8], [8, 7], [7, 9], [8, 11], [10, 11],
];

function Box({ w, tone, i }) {
  const d = w * 0.58;
  const h = w * 0.46;
  return (
    <div
      className={`box tone-${tone}`}
      style={{ width: `${w}px`, height: `${h}px`, "--d": `${d}px`, "--i": i }}
    >
      <span className="face front">
        <span className="ribs" />
      </span>
      <span className="face side" />
      <span className="face top" />
    </div>
  );
}

function Stack({ x, y, w, boxes, tone, z, dur, delay }) {
  return (
    <div
      className="stack"
      style={{
        left: x,
        top: y,
        "--z": `${z}px`,
        animationDuration: `${dur}s`,
        animationDelay: `${delay}s`,
      }}
    >
      {Array.from({ length: boxes }, (_, i) => (
        <Box key={i} w={w} tone={tone} i={i} />
      ))}
    </div>
  );
}

function Rack({ x, y, w, h, z, delay }) {
  return (
    <div
      className="rack"
      style={{ left: x, top: y, width: `${w}px`, height: `${h}px`, "--z": `${z}px`, animationDelay: `${delay}s` }}
    >
      <span className="beam" />
      <span className="beam" />
      <span className="beam" />
      <span className="upright left" />
      <span className="upright right" />
    </div>
  );
}

export default function SupplyChainBackground() {
  return (
    <div className="scene" aria-hidden="true">
      <div className="scene-base" />
      <div className="grid-floor" />
      <div className="grid-ceiling" />

      <span className="orb orb-a" />
      <span className="orb orb-b" />
      <span className="orb orb-c" />

      {RACKS.map((r, i) => (
        <Rack key={`r${i}`} {...r} />
      ))}

      <svg className="net" viewBox="0 0 100 100" preserveAspectRatio="none">
        {LINKS.map(([a, b], i) => (
          <line
            key={i}
            x1={NODES[a].x}
            y1={NODES[a].y}
            x2={NODES[b].x}
            y2={NODES[b].y}
            style={{ animationDelay: `${-i * 0.7}s` }}
          />
        ))}
        {NODES.map((n, i) => (
          <circle key={i} cx={n.x} cy={n.y} r="0.45" style={{ animationDelay: `${-i * 0.5}s` }} />
        ))}
      </svg>

      {STACKS.map((s, i) => (
        <Stack key={`s${i}`} {...s} />
      ))}

      <div className="haze" />
      <div className="vignette" />
    </div>
  );
}
