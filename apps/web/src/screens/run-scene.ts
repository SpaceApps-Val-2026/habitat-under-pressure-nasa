// The network of the run screen as data: which nodes there are, what state each is in and where it sits.
// Pure functions, so the layout can be tested for overlaps without a browser.
import { absenceOf, countOf, type StepState } from "./run-model";

export type SceneState = "idle" | "active" | "done" | "error" | "absent";
export type NodeKind = "question" | "zone" | "data" | "compute" | "write" | "review";

export type PlanStepInfo = {
  id: string;
  operation: string;
  type: string;
  source: string | null;
  variable: string | null;
};

export type SceneNode = {
  id: string;
  kind: NodeKind;
  tone: string;
  name: string;
  count: number | null;
  detail: string;
  state: SceneState;
  steps: string[];
  reason: string | null;
};

export type PlacedNode = SceneNode & {
  ring: "outer" | "inner" | "single";
  x: number;
  y: number;
  r: number;
  anchor: "start" | "middle" | "end";
  lx: number;
  ly: number;
  lines: string[];
};

// Wider than tall, so the stage fits under the question at 1366×768 without shrinking its type too much.
export const VIEW = { w: 1160, h: 560, cx: 580, cy: 280, core: 54 };

// Above this many reads, the reads of one source share a node with a counter.
export const GROUP_READS_ABOVE = 6;
// Up to this many nodes, one ring; above, data outside and the rest inside.
const SINGLE_RING_MAX = 8;
// Characters per label line; two lines at most, broken between words.
export const LABEL_WIDTH = 16;
// Label type in scene units (the SVG scales to about 0.85 at 1366 px, so 14 reads as 12 px).
export const LABEL_LINE = 18;
const CHAR_WIDTH = 8.3;

// The colour of a data node is the theme of what it reads (catalogue `layer`).
export function toneOfLayer(layer: string | null | undefined): string {
  switch (layer) {
    case "water":
    case "sea":
      return "tone-water";
    case "green":
      return "tone-veg";
    case "land":
    case "population":
    case "risk":
    case "energy":
      return "tone-pressure";
    case "climate":
    case "heat":
      return "tone-climate";
    default:
      return "tone-veg";
  }
}

const KIND_TONE: Record<Exclude<NodeKind, "data">, string> = {
  question: "tone-signal",
  zone: "tone-signal",
  compute: "tone-compute",
  write: "tone-signal",
  review: "tone-review",
};

export function kindOfStep(type: string, operation: string): NodeKind {
  if (type === "ingest" || operation.startsWith("read_")) return "data";
  if (type === "write" || operation.startsWith("write_")) return "write";
  if (type === "review" || operation.startsWith("review_")) return "review";
  return "compute";
}

// A short name a label can carry: the whole name when it fits, else its first words.
export function shortName(name: string, width = LABEL_WIDTH): string {
  const clean = name.split(" (")[0].trim();
  if (clean.length <= width) return clean;
  let out = "";
  for (const word of clean.split(/\s+/)) {
    const next = out ? `${out} ${word}` : word;
    if (next.length > width) break;
    out = next;
  }
  return out || clean.slice(0, width);
}

// At most two lines of about `width` characters, broken between words; a longer text ends with «…»
// after a whole word.
export function labelLines(value: string, width = LABEL_WIDTH): string[] {
  const words = value.trim().split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let current = "";
  let used = 0;
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (next.length <= width || !current) {
      current = next;
      used += 1;
      continue;
    }
    lines.push(current);
    current = "";
    if (lines.length === 2) break;
    current = word;
    used += 1;
  }
  if (current && lines.length < 2) lines.push(current);
  if (used < words.length) lines[lines.length - 1] = `${lines[lines.length - 1].replace(/[,.;:·]$/, "").trim()}…`;
  return lines;
}

function stepState(step: StepState | undefined): SceneState {
  if (!step) return "idle";
  if (step.error) return "error";
  if (absenceOf(step)) return "absent";
  if (step.finished) return "done";
  if (step.started) return "active";
  return "idle";
}

function merged(states: SceneState[]): SceneState {
  if (states.includes("error")) return "error";
  if (states.includes("active")) return "active";
  if (states.length > 0 && states.every((state) => state === "absent")) return "absent";
  if (states.length > 0 && states.every((state) => state === "done" || state === "absent")) return "done";
  return "idle";
}

export type SceneInput = {
  steps: PlanStepInfo[];
  states: Map<string, StepState>;
  planReady: boolean;
  placeName: string | null;
  labels: { question: string; place: string };
  sourceName: (id: string) => string;
  variableName: (id: string) => string | null;
  variableTone: (id: string) => string;
  /** What a step does, in words (its catalogue phrase, filled): the tooltip and the list. */
  stepLabel: (step: PlanStepInfo, state: StepState | undefined) => string;
  /** The node's own short name (two or three words): the operation's name, or the kind of step. */
  stepShort: (step: PlanStepInfo) => string;
  absenceText: (step: StepState) => string | null;
};

export function sceneNodes(input: SceneInput): SceneNode[] {
  const { steps, states } = input;
  const nodes: SceneNode[] = [
    {
      id: "question",
      kind: "question",
      tone: KIND_TONE.question,
      name: input.labels.question,
      count: null,
      detail: input.labels.question,
      state: input.planReady ? "done" : "active",
      steps: [],
      reason: null,
    },
    {
      id: "place",
      kind: "zone",
      tone: KIND_TONE.zone,
      name: input.placeName?.split(",")[0].trim() || input.labels.place,
      count: null,
      detail: input.placeName || input.labels.place,
      state: input.planReady && input.placeName ? "done" : "idle",
      steps: [],
      reason: null,
    },
  ];

  const reads = steps.filter((step) => kindOfStep(step.type, step.operation) === "data");
  const group = reads.length > GROUP_READS_ABOVE;
  const groups = new Map<string, PlanStepInfo[]>();
  for (const step of reads) {
    const read = states.get(step.id)?.dataReads.find((item) => item.source)?.source ?? null;
    const key = group ? (step.source ?? read ?? step.id) : step.id;
    groups.set(key, [...(groups.get(key) ?? []), step]);
  }

  for (const [key, members] of groups) {
    const memberStates = members.map((step) => states.get(step.id));
    const counts = memberStates.map((state) => (state ? countOf(state) : null)).filter((n): n is number => n !== null);
    const first = members[0];
    const readSource =
      memberStates.flatMap((state) => state?.dataReads ?? []).find((read) => read.source)?.source ?? null;
    const source = first.source ?? readSource;
    const sourceLabel = source ? input.sourceName(source) : null;
    const variableLabel = first.variable ? input.variableName(first.variable) : null;
    const name = group || !variableLabel ? (sourceLabel ?? variableLabel ?? "") : variableLabel;
    const absent = memberStates.find((state) => state && absenceOf(state));
    nodes.push({
      id: `data-${key}`,
      kind: "data",
      tone: first.variable ? input.variableTone(first.variable) : "tone-veg",
      name: name || input.stepLabel(first, memberStates[0]),
      count: counts.length > 0 ? counts.reduce((sum, n) => sum + n, 0) : null,
      detail:
        members.length > 1
          ? `${sourceLabel ?? ""}: ${members
              .map((step) => (step.variable ? input.variableName(step.variable) : null) ?? step.operation)
              .filter((value, index, all) => all.indexOf(value) === index)
              .join(", ")}`
          : input.stepLabel(first, memberStates[0]),
      state: merged(memberStates.map(stepState)),
      steps: members.map((step) => step.id),
      reason: absent ? input.absenceText(absent) : null,
    });
  }

  for (const step of steps) {
    const kind = kindOfStep(step.type, step.operation);
    if (kind === "data") continue;
    const state = states.get(step.id);
    const label = input.stepLabel(step, state);
    nodes.push({
      id: `step-${step.id}`,
      kind,
      tone: KIND_TONE[kind],
      name: input.stepShort(step),
      count: null,
      detail: label,
      state: stepState(state),
      steps: [step.id],
      reason: state && absenceOf(state) ? input.absenceText(state) : null,
    });
  }
  return nodes;
}

// What a node's label says: the name, and the count once a read has arrived.
export function nodeLabel(node: SceneNode, format: (n: number) => string): string[] {
  const lines = labelLines(node.name);
  if (node.count === null) return lines;
  const tail = `· ${format(node.count)}`;
  const last = lines[lines.length - 1];
  if (`${last} ${tail}`.length <= LABEL_WIDTH + 6) lines[lines.length - 1] = `${last} ${tail}`;
  else if (lines.length < 2) lines.push(tail);
  else lines[1] = `${lines[1]} ${tail}`;
  return lines;
}

function ring(count: number, rx: number, ry: number, offset = 0) {
  return Array.from({ length: count }, (_, index) => {
    const angle = -Math.PI / 2 + offset + (index * 2 * Math.PI) / Math.max(count, 1);
    return { angle, x: VIEW.cx + rx * Math.cos(angle), y: VIEW.cy + ry * Math.sin(angle) };
  });
}

// Data outside, calculations inside; labels radially outward, anchored left or right on the sides and
// centred above or below at the top and bottom, so neighbours never write over each other.
export function placeNodes(nodes: SceneNode[], format: (n: number) => string): PlacedNode[] {
  const data = nodes.filter((node) => node.kind === "data");
  const rest = nodes.filter((node) => node.kind !== "data");
  const two = nodes.length > SINGLE_RING_MAX && data.length >= 3 && rest.length >= 2;

  const place = (
    list: SceneNode[],
    kind: PlacedNode["ring"],
    rx: number,
    ry: number,
    r: number,
    offset = 0,
  ): PlacedNode[] =>
    ring(list.length, rx, ry, offset).map(({ angle, x, y }, index) => {
      const node = list[index];
      const lines = nodeLabel(node, format);
      const cos = Math.cos(angle);
      const sin = Math.sin(angle);
      const side = kind !== "inner" && Math.abs(cos) > 0.35;
      const anchor: PlacedNode["anchor"] = side ? (cos > 0 ? "start" : "end") : "middle";
      const lineHeight = LABEL_LINE;
      const lx = side ? (cos > 0 ? r + 8 : -(r + 8)) : 0;
      const ly = side
        ? 4 - ((lines.length - 1) * lineHeight) / 2
        : sin < 0
          ? -(r + 10) - (lines.length - 1) * lineHeight
          : r + 18;
      return { ...node, ring: kind, x, y, r, anchor, lx, ly, lines };
    });

  if (!two) return place(nodes, "single", 360, 192, 24);
  const outerStep = (2 * Math.PI) / data.length;
  return [...place(data, "outer", 378, 206, 22), ...place(rest, "inner", 215, 116, 20, outerStep / 2)];
}

// The rough box a label covers, for the overlap check.
export function labelBox(node: PlacedNode) {
  const width = Math.max(...node.lines.map((line) => line.length)) * CHAR_WIDTH;
  const height = node.lines.length * LABEL_LINE;
  const left = node.anchor === "start" ? node.x + node.lx : node.anchor === "end" ? node.x + node.lx - width : node.x - width / 2;
  const top = node.y + node.ly - 14;
  return { left, top, right: left + width, bottom: top + height };
}

export function nodeBox(node: PlacedNode) {
  return { left: node.x - node.r, top: node.y - node.r, right: node.x + node.r, bottom: node.y + node.r };
}

export function overlaps(a: { left: number; top: number; right: number; bottom: number }, b: typeof a): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

// The two ends of a spoke: from the edge of the node to the edge of Delfos, so data travels inwards.
export function spoke(node: PlacedNode) {
  const dx = VIEW.cx - node.x;
  const dy = VIEW.cy - node.y;
  const length = Math.hypot(dx, dy) || 1;
  return {
    x1: node.x + (dx / length) * node.r,
    y1: node.y + (dy / length) * node.r,
    x2: VIEW.cx - (dx / length) * VIEW.core,
    y2: VIEW.cy - (dy / length) * VIEW.core,
  };
}
