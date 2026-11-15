import { useState, type ReactNode } from "react";

import { useI18n } from "../i18n";
import { localized } from "./shared";

// The satellite view of the place (GET /api/places/image/info). Every field optional: the route is
// newer than the report, and a report without it reads the same.
export type PlaceImageInfo = {
  available?: boolean;
  source?: { id?: string; name?: Record<string, string> };
  period?: string | { start?: string; end?: string };
  resolution_m?: number;
  credit?: Record<string, string>;
  license?: Record<string, string>;
  url?: string;
  absence?: unknown;
};

type Zone = { name: string; bbox: number[]; geometry?: unknown };

export function bboxParam(bbox: number[]): string {
  return bbox.map((value) => String(value)).join(",");
}

// The image info for a zone, or null when the route is missing (404), refuses, or has no image.
export async function fetchPlaceImage(bbox: number[]): Promise<PlaceImageInfo | null> {
  if (bbox.length !== 4 || typeof fetch !== "function") return null;
  const response = await fetch(`/api/places/image/info?bbox=${encodeURIComponent(bboxParam(bbox))}`);
  if (!response.ok) return null;
  const info = (await response.json()) as PlaceImageInfo;
  return info.available ? info : null;
}

// Width over height of the zone's box on the ground, so the frame keeps its shape before the image loads.
function aspectOf(bbox: number[]): number {
  const [west, south, east, north] = bbox;
  const latitude = (((south + north) / 2) * Math.PI) / 180;
  const ratio = ((east - west) * Math.cos(latitude)) / (north - south || 1);
  return Math.min(2.4, Math.max(0.6, Number.isFinite(ratio) ? ratio : 1.5));
}

// The zone's outline in the box's own coordinates (0..1000 wide), when the zone carries a polygon.
function outline(zone: Zone, height: number): string[] {
  const geometry = zone.geometry as { type?: string; coordinates?: unknown } | undefined;
  if (!geometry?.coordinates) return [];
  const polygons =
    geometry.type === "Polygon"
      ? [geometry.coordinates as number[][][]]
      : geometry.type === "MultiPolygon"
        ? (geometry.coordinates as number[][][][])
        : [];
  const [west, south, east, north] = zone.bbox;
  const x = (lon: number) => ((lon - west) / (east - west || 1)) * 1000;
  const y = (lat: number) => ((north - lat) / (north - south || 1)) * height;
  return polygons.flatMap((polygon) =>
    polygon.slice(0, 1).map((ring) => ring.map(([lon, lat]) => `${x(lon).toFixed(1)},${y(lat).toFixed(1)}`).join(" ")),
  );
}

// The width every map figure of a report shares: the place photo's, capped so it fits the screen's height.
export function placeFigureWidth(bbox: number[]): string {
  return `min(100%, calc(70vh * ${aspectOf(bbox).toFixed(4)}))`;
}

export function periodLabel(period: PlaceImageInfo["period"]): string {
  if (!period) return "";
  if (typeof period === "string") return period;
  return [period.start, period.end].filter(Boolean).join(" – ");
}

export function PlaceImage({
  zone,
  info,
  notes,
  title,
}: {
  zone: Zone;
  info: PlaceImageInfo;
  notes?: ReactNode;
  title?: string;
}) {
  const { t, language } = useI18n();
  const [failed, setFailed] = useState(false);
  const place = zone.name.split(",")[0]?.trim() || zone.name;
  const aspect = aspectOf(zone.bbox);
  const height = 1000 / aspect;
  const rings = outline(zone, height);
  const src = `/api/places/image?bbox=${encodeURIComponent(bboxParam(zone.bbox))}&width=1200`;
  // An image that does not arrive leaves no empty frame.
  if (failed) return null;
  const caption = [
    info.source?.name ? localized(info.source.name, language) : "",
    periodLabel(info.period),
    info.resolution_m ? `${t("blocks.resolution")}: ${info.resolution_m >= 1000 ? `${info.resolution_m / 1000} km` : `${info.resolution_m} m`}` : "",
    info.credit ? localized(info.credit, language) : "",
  ].filter(Boolean);

  return (
    // The figure is as wide as the photo, so its title and its caption start and wrap at the photo's edges.
    <figure
      className="report-figure report-place"
      style={{ width: placeFigureWidth(zone.bbox) }}
    >
      <figcaption className="top">
        <b className="figure-number" /> {title ?? t("report.placeView").replace("{place}", place)}
      </figcaption>
      <div className="report-img" style={{ aspectRatio: String(aspect) }}>
        <img src={src} alt={t("report.placeAlt").replace("{place}", place)} loading="lazy" decoding="async" onError={() => setFailed(true)} />
        {rings.length > 0 && (
          <svg className="report-outline" viewBox={`0 0 1000 ${height.toFixed(1)}`} preserveAspectRatio="none" aria-hidden="true">
            {rings.map((points, index) => (
              <polygon key={index} points={points} />
            ))}
          </svg>
        )}
      </div>
      <p className="src">
        {caption.join(" · ")}
        {notes}
      </p>
    </figure>
  );
}
