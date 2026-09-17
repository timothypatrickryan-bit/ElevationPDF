/**
 * Sheet-space geometry for the measurement UI: path flattening, a flatbush
 * spatial index, and the snap engine (PLAN §9).
 *
 * All coordinates are sheet space (top-left origin, y down, PDF points) —
 * the same space the extractor emits. Never re-apply page rotation here.
 */

import Flatbush from 'flatbush';
import type { Path, SheetModel } from '@elevationpdf/model';

export interface Segment {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  pathId: string;
}

export type SnapKind = 'endpoint' | 'intersection' | 'midpoint' | 'on-path';

export interface SnapResult {
  x: number;
  y: number;
  kind: SnapKind;
  dist: number;
}

const BEZIER_STEPS = 8;
// PLAN §9 snap priority: endpoint > intersection > midpoint > nearest-on-path
const KIND_RANK: Record<SnapKind, number> = {
  endpoint: 0,
  intersection: 1,
  midpoint: 2,
  'on-path': 3,
};
// beyond this many nearby segments, skip pairwise intersections for the frame
const MAX_INTERSECTION_CANDIDATES = 40;

function cubic(
  p0: number[],
  c1: number[],
  c2: number[],
  p1: number[],
  t: number,
): [number, number] {
  const mt = 1 - t;
  const a = mt * mt * mt;
  const b = 3 * mt * mt * t;
  const c = 3 * mt * t * t;
  const d = t * t * t;
  return [
    a * p0[0] + b * c1[0] + c * c2[0] + d * p1[0],
    a * p0[1] + b * c1[1] + c * c2[1] + d * p1[1],
  ];
}

function flattenPath(path: Path): [number, number][] {
  if (path.kind === 'bezier') {
    const out: [number, number][] = [[path.pts[0][0], path.pts[0][1]]];
    for (let i = 0; i + 3 < path.pts.length; i += 3) {
      for (let s = 1; s <= BEZIER_STEPS; s++) {
        out.push(
          cubic(path.pts[i], path.pts[i + 1], path.pts[i + 2], path.pts[i + 3], s / BEZIER_STEPS),
        );
      }
    }
    return out;
  }
  const pts: [number, number][] = path.pts.map((p) => [p[0], p[1]]);
  const isClosed = path.closed || path.kind === 'rect' || path.kind === 'quad';
  if (isClosed && pts.length > 2) {
    const [fx, fy] = pts[0];
    const [lx, ly] = pts[pts.length - 1];
    if (fx !== lx || fy !== ly) pts.push([fx, fy]);
  }
  return pts;
}

export function flattenModel(model: SheetModel): Segment[] {
  const segments: Segment[] = [];
  for (const path of model.paths) {
    const pts = flattenPath(path);
    for (let i = 0; i + 1 < pts.length; i++) {
      const [x1, y1] = pts[i];
      const [x2, y2] = pts[i + 1];
      if (x1 === x2 && y1 === y2) continue;
      segments.push({ x1, y1, x2, y2, pathId: path.id });
    }
  }
  return segments;
}

function nearestOnSegment(x: number, y: number, s: Segment): { x: number; y: number; t: number } {
  const px = s.x2 - s.x1;
  const py = s.y2 - s.y1;
  const denom = px * px + py * py;
  if (denom === 0) return { x: s.x1, y: s.y1, t: 0 };
  let t = ((x - s.x1) * px + (y - s.y1) * py) / denom;
  t = Math.max(0, Math.min(1, t));
  return { x: s.x1 + t * px, y: s.y1 + t * py, t };
}

function intersect(a: Segment, b: Segment): [number, number] | null {
  const d1x = a.x2 - a.x1;
  const d1y = a.y2 - a.y1;
  const d2x = b.x2 - b.x1;
  const d2y = b.y2 - b.y1;
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-9) return null;
  const t = ((b.x1 - a.x1) * d2y - (b.y1 - a.y1) * d2x) / denom;
  const u = ((b.x1 - a.x1) * d1y - (b.y1 - a.y1) * d1x) / denom;
  if (t < 0 || t > 1 || u < 0 || u > 1) return null;
  return [a.x1 + t * d1x, a.y1 + t * d1y];
}

export class SnapIndex {
  private readonly segments: Segment[];
  private readonly tree: Flatbush;

  constructor(segments: Segment[]) {
    this.segments = segments;
    this.tree = new Flatbush(Math.max(segments.length, 1));
    if (segments.length === 0) {
      // flatbush requires at least one entry
      this.tree.add(0, 0, 0, 0);
    }
    for (const s of segments) {
      this.tree.add(
        Math.min(s.x1, s.x2),
        Math.min(s.y1, s.y2),
        Math.max(s.x1, s.x2),
        Math.max(s.y1, s.y2),
      );
    }
    this.tree.finish();
  }

  get size(): number {
    return this.segments.length;
  }

  /** Segments whose bbox falls within radius of (x, y). */
  near(x: number, y: number, radius: number): Segment[] {
    if (this.segments.length === 0) return [];
    return this.tree
      .search(x - radius, y - radius, x + radius, y + radius)
      .map((i) => this.segments[i]);
  }

  /**
   * Best snap target within `radius` sheet units of (x, y), or null.
   * Intersections are computed lazily among nearby segments only (PLAN §9 —
   * never precompute all pairs).
   */
  snap(x: number, y: number, radius: number): SnapResult | null {
    const candidates = this.near(x, y, radius);
    let best: SnapResult | null = null;

    const consider = (px: number, py: number, kind: SnapKind) => {
      const dist = Math.hypot(px - x, py - y);
      if (dist > radius) return;
      if (
        !best ||
        KIND_RANK[kind] < KIND_RANK[best.kind] ||
        (KIND_RANK[kind] === KIND_RANK[best.kind] && dist < best.dist)
      ) {
        best = { x: px, y: py, kind, dist };
      }
    };

    for (const s of candidates) {
      consider(s.x1, s.y1, 'endpoint');
      consider(s.x2, s.y2, 'endpoint');
      consider((s.x1 + s.x2) / 2, (s.y1 + s.y2) / 2, 'midpoint');
      const n = nearestOnSegment(x, y, s);
      consider(n.x, n.y, 'on-path');
    }

    if (candidates.length <= MAX_INTERSECTION_CANDIDATES) {
      for (let i = 0; i < candidates.length; i++) {
        for (let j = i + 1; j < candidates.length; j++) {
          const p = intersect(candidates[i], candidates[j]);
          if (p) consider(p[0], p[1], 'intersection');
        }
      }
    }
    return best;
  }
}

/** 100.5 -> `100'-6"`, rounding inches to the nearest 1/16. */
export function formatFeet(valueFt: number): string {
  const sign = valueFt < 0 ? '-' : '';
  const abs = Math.abs(valueFt);
  let feet = Math.floor(abs);
  let sixteenths = Math.round((abs - feet) * 12 * 16);
  if (sixteenths >= 12 * 16) {
    feet += 1;
    sixteenths = 0;
  }
  const whole = Math.floor(sixteenths / 16);
  let frac = sixteenths % 16;
  let den = 16;
  while (frac > 0 && frac % 2 === 0) {
    frac /= 2;
    den /= 2;
  }
  const inches = frac > 0 ? `${whole} ${frac}/${den}` : `${whole}`;
  return `${sign}${feet}'-${inches}"`;
}
