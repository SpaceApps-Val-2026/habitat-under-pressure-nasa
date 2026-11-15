import { useEffect, useRef, useState, type CSSProperties } from "react";

import { useI18n } from "../i18n";

// A layer frame drawn as a still image: the report is read, not explored, and it prints. The COG is read
// at the overview nearest to a screen width, coloured with the design system's ramp for its scale, and
// pixels without data are left blank, never coloured. The interactive map stays in the data panel.
const RAMPS: Record<string, string[]> = {
  heat: ["--heat-1", "--heat-2", "--heat-3", "--heat-4", "--heat-5"],
  green: ["--veg-1", "--veg-2", "--veg-3", "--veg-4", "--veg-5"],
  water: ["--wet-1", "--wet-2", "--wet-3", "--wet-4", "--wet-5"],
};

export function rampOf(scale: string | undefined): string[] {
  return RAMPS[scale ?? ""] ?? RAMPS.water;
}

function rgb(token: string): [number, number, number] | null {
  const value = getComputedStyle(document.documentElement).getPropertyValue(token).trim();
  const match = /^#([0-9a-f]{6})$/i.exec(value);
  if (!match) return null;
  const hex = parseInt(match[1], 16);
  return [(hex >> 16) & 255, (hex >> 8) & 255, hex & 255];
}

const TARGET_WIDTH = 640;

async function draw(canvas: HTMLCanvasElement, url: string, range: [number, number], scale: string | undefined) {
  // The reader is the one the panel's raster control already ships; loaded only when a report has a map.
  const { GeoTIFF, assembleTiles } = await import("@developmentseed/geotiff");
  const tiff = await GeoTIFF.fromUrl(new URL(url, window.location.href));
  // Overviews go from finest to coarsest: keep the coarsest still at least a screen wide.
  let level: Pick<typeof tiff, "width" | "height" | "tileWidth" | "tileHeight" | "tileCount" | "fetchTiles"> = tiff;
  for (const overview of tiff.overviews) if (overview.width >= TARGET_WIDTH) level = overview;
  const { x: cols, y: rows } = level.tileCount;
  const xy: [number, number][] = [];
  for (let row = 0; row < rows; row += 1) for (let col = 0; col < cols; col += 1) xy.push([col, row]);
  const tiles = await level.fetchTiles(xy, { boundless: true });
  const raster = assembleTiles(tiles, {
    width: cols * level.tileWidth,
    height: rows * level.tileHeight,
    tileWidth: level.tileWidth,
    tileHeight: level.tileHeight,
    minCol: 0,
    minRow: 0,
  });
  const values = raster.layout === "band-separate" ? raster.bands[0] : raster.data;
  const stride = raster.layout === "band-separate" ? 1 : raster.count;
  const ramp = rampOf(scale).map(rgb);
  if (ramp.some((stop) => stop === null)) throw new Error("ramp tokens missing");
  const stops = ramp as [number, number, number][];
  const width = level.width;
  const height = level.height;
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("no 2d context");
  const image = context.createImageData(width, height);
  const [low, high] = range;
  const span = high - low || 1;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const source = y * raster.width + x;
      const value = values[source * stride];
      const masked = raster.mask ? raster.mask[source] === 0 : false;
      if (masked || !Number.isFinite(value) || (raster.nodata !== null && value === raster.nodata)) continue;
      const position = Math.min(1, Math.max(0, (value - low) / span)) * (stops.length - 1);
      const index = Math.min(stops.length - 2, Math.floor(position));
      const share = position - index;
      const target = (y * width + x) * 4;
      for (let channel = 0; channel < 3; channel += 1) {
        image.data[target + channel] = stops[index][channel] + (stops[index + 1][channel] - stops[index][channel]) * share;
      }
      image.data[target + 3] = 255;
    }
  }
  context.putImageData(image, 0, 0);
}

export function RasterImage({
  url,
  range,
  scale,
  alt,
  label,
}: {
  url: string;
  range: [number, number];
  scale?: string;
  alt: string;
  label?: string;
}) {
  const { t } = useI18n();
  const canvas = useRef<HTMLCanvasElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const node = canvas.current;
    if (!node) return;
    let live = true;
    setFailed(false);
    draw(node, url, range, scale).catch(() => {
      if (live) setFailed(true);
    });
    return () => {
      live = false;
    };
  }, [url, range, scale]);

  return (
    <div className="report-img">
      <canvas ref={canvas} role="img" aria-label={alt} hidden={failed} />
      {failed && <p className="small text-muted">{t("panel.layerFailed")}</p>}
      {label && <span className="report-img-label">{label}</span>}
    </div>
  );
}

// The colour ramp of a layer with its ends, the way the draft's map legends read.
export function RampLegend({ scale, low, high }: { scale?: string; low: string; high: string }) {
  const gradient = `linear-gradient(90deg, ${rampOf(scale)
    .map((token) => `var(${token})`)
    .join(", ")})`;
  return (
    <div className="report-legend">
      <span className="report-ramp" style={{ background: gradient }} />
      <span className="report-ends">
        <span>{low}</span>
        <span>{high}</span>
      </span>
    </div>
  );
}

// Before and after in one frame: the "after" frame below, the "before" frame on top clipped at a divider
// the reader drags (pointer) or moves with the arrow keys (the range input). Printed, the two frames
// stand side by side. The same pattern as the panel's map curtain, on still images.
export function RasterCurtain({
  frames,
  range,
  scale,
  name,
}: {
  frames: { url: string; label: string }[];
  range: [number, number];
  scale?: string;
  name: string;
}) {
  const [split, setSplit] = useState(50);
  const stage = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const [before, after] = frames;
  const move = (clientX: number) => {
    const rect = stage.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return;
    setSplit(Math.min(100, Math.max(0, Math.round(((clientX - rect.left) / rect.width) * 100))));
  };
  return (
    <div className="report-curtain" style={{ "--split": `${split}%` } as CSSProperties}>
      <div
        ref={stage}
        className="report-curtain-stage"
        onPointerDown={(event) => {
          dragging.current = true;
          event.currentTarget.setPointerCapture(event.pointerId);
          move(event.clientX);
        }}
        onPointerMove={(event) => {
          if (dragging.current) move(event.clientX);
        }}
        onPointerUp={() => {
          dragging.current = false;
        }}
        onPointerCancel={() => {
          dragging.current = false;
        }}
      >
        <div className="report-curtain-after">
          <RasterImage url={after.url} range={range} scale={scale} alt={`${name}, ${after.label}`} label={after.label} />
        </div>
        <div className="report-curtain-before">
          <RasterImage url={before.url} range={range} scale={scale} alt={`${name}, ${before.label}`} label={before.label} />
        </div>
        <span className="report-curtain-divider" aria-hidden="true">
          <span className="report-curtain-handle">◀▶</span>
        </span>
      </div>
      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={split}
        onChange={(event) => setSplit(Number(event.target.value))}
        aria-label={`${before.label} / ${after.label}`}
        className="report-curtain-range sr-only"
      />
    </div>
  );
}
