/**
 * Shared types for the ElevationPDF sheet model.
 *
 * Mirrors packages/model/schema/sheet-model.schema.json, which is the
 * normative definition (the Python extractor validates against it in CI).
 *
 * COORDINATE CONVENTION (normative): all coordinates are in "sheet space" —
 * the page as displayed (CropBox with /Rotate applied), origin top-left,
 * x right, y DOWN, units = PDF points (1/72 in). `rotation` and `user_unit`
 * are provenance only; never re-apply them.
 */

export const SHEET_MODEL_SCHEMA_VERSION = '0.1';

/** [x, y] in sheet space. */
export type Vec2 = [number, number];

/** [x0, y0, x1, y1] in sheet space, x0<=x1, y0<=y1. */
export type BBox = [number, number, number, number];

/** #rrggbb lowercase hex. */
export type Color = string;

export type LayerSource = 'ocg' | 'inferred' | 'user';

export interface LayerSignature {
  stroke?: Color | null;
  width?: number | null;
  dash?: number[];
}

export interface Layer {
  id: string;
  name: string;
  source: LayerSource;
  default_visible: boolean;
  signature?: LayerSignature | null;
}

/**
 * line: 2 pts. polyline: n>=2 pts. rect/quad: 4 corner pts, closed.
 * bezier: cubic chain control points, 3k+1 pts (p0 c1 c2 p1 c1' c2' p2 ...).
 */
export type PathKind = 'line' | 'polyline' | 'rect' | 'quad' | 'bezier';

export interface Path {
  id: string;
  layer_id: string | null;
  kind: PathKind;
  pts: Vec2[];
  stroke: Color | null;
  /** Fills (hatches, solids) are kept distinct from strokes. */
  fill: Color | null;
  width: number | null;
  /** Dash array in points (empty = solid). */
  dash: number[];
  closed: boolean;
}

export type TextSource = 'text' | 'shx_comment' | 'ocr';

export interface Text {
  id: string;
  str: string;
  bbox: BBox;
  /** Unit reading-direction vector in sheet space (y down). (1,0) = horizontal. */
  dir: Vec2;
  /** Reading direction, degrees CCW from horizontal, 0-359. Vertical text = 90. */
  rotation_deg: number;
  font?: string | null;
  size?: number | null;
  layer_id?: string | null;
  source: TextSource;
}

export interface Viewport {
  id: string;
  clip_bbox: BBox;
  scale_ref: string | null;
}

export interface Dimension {
  text_id: string;
  value_ft: number;
  path_id?: string | null;
  measured_pt?: number | null;
  implied_ppf?: number | null;
}

export type ScaleMethod =
  | 'measure_dict'
  | 'title_block'
  | 'dimension_fit'
  | 'manual'
  | 'none';

export interface Scale {
  id: string;
  method: ScaleMethod;
  points_per_foot: number | null;
  /** 0..1 */
  confidence: number;
  evidence: string[];
}

export interface TitleBlock {
  sheet_no?: string | null;
  title?: string | null;
  scale_text?: string | null;
  bbox?: BBox | null;
}

export interface SheetStats {
  path_count: number;
  segment_count: number;
  text_char_count: number;
  /** Fraction of page area covered by images, 0..1. */
  image_coverage: number;
  is_raster: boolean;
  distinct_stroke_colors: number;
  /** True when >=95% of stroked paths are black/gray. */
  monochrome: boolean;
}

export interface SheetModel {
  sheet_id: string;
  page_index: number;
  /** Displayed page size [width, height] in points (rotation applied). */
  size_pt: Vec2;
  rotation: 0 | 90 | 180 | 270;
  /** PDF /UserUnit multiplier (1.0 = default). */
  user_unit: number;
  extractor_version: string;
  /** "sha256:<hex>" over the page's content streams. */
  content_hash: string;
  producer?: string | null;
  creator?: string | null;
  title_block?: TitleBlock | null;
  layers: Layer[];
  paths: Path[];
  texts: Text[];
  viewports: Viewport[];
  dimensions: Dimension[];
  scales: Scale[];
  stats: SheetStats;
}

/** points_per_foot for an imperial scale: paper inches per real foot × 72. */
export function pointsPerFoot(paperInchesPerRealFoot: number): number {
  return paperInchesPerRealFoot * 72;
}
