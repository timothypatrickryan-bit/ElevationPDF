import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import type { Scale, SheetModel } from '@elevationpdf/model';
import { SnapIndex, flattenModel, formatFeet, type SnapResult } from './geometry';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type OptionalContentConfig = Awaited<
  ReturnType<PDFDocumentProxy['getOptionalContentConfig']>
>;

interface LayerRow {
  id: string;
  name: string;
  visible: boolean;
}

interface Measurement {
  id: number;
  a: [number, number];
  b: [number, number];
  /** sheet-space length in points — the source of truth; feet are derived
   * from the active scale at render time (PLAN §11 recalibration policy). */
  lengthPt: number;
}

const SNAP_RADIUS_PX = 10;

const SNAP_COLOR: Record<SnapResult['kind'], string> = {
  endpoint: '#e8483f',
  intersection: '#b34ae0',
  midpoint: '#e8a23f',
  'on-path': '#3f8fe8',
};

/** Flatten pdf.js optional-content order (nested groups) into group ids. */
function flattenOrder(order: unknown[] | null): string[] {
  const ids: string[] = [];
  const walk = (items: unknown[]) => {
    for (const item of items) {
      if (typeof item === 'string') {
        ids.push(item);
      } else if (item && typeof item === 'object' && 'order' in item) {
        walk((item as { order: unknown[] }).order);
      }
    }
  };
  if (order) walk(order);
  return ids;
}

function formatLength(lengthPt: number, ppf: number | null): string {
  return ppf ? formatFeet(lengthPt / ppf) : `${lengthPt.toFixed(1)} pt`;
}

export function App() {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [fileName, setFileName] = useState<string>('');
  const [pageNum, setPageNum] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [layers, setLayers] = useState<LayerRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  // bumped on every visibility toggle so the render effect re-runs
  const [ocVersion, setOcVersion] = useState(0);

  const [model, setModel] = useState<SheetModel | null>(null);
  const [modelName, setModelName] = useState<string>('');
  const [notice, setNotice] = useState<string | null>(null);
  const [activeScaleId, setActiveScaleId] = useState<string | null>(null);
  const [measurements, setMeasurements] = useState<Measurement[]>([]);
  const [anchor, setAnchor] = useState<[number, number] | null>(null);
  const [hover, setHover] = useState<SnapResult | null>(null);
  const [cursor, setCursor] = useState<[number, number] | null>(null);

  const ocConfigRef = useRef<OptionalContentConfig | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const viewerRef = useRef<HTMLDivElement>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);
  const nextMeasurementId = useRef(1);

  const snapIndex = useMemo(
    () => (model ? new SnapIndex(flattenModel(model)) : null),
    [model],
  );

  const scalesWithPpf = useMemo(
    () => (model?.scales ?? []).filter((s): s is Scale & { points_per_foot: number } =>
      s.points_per_foot != null,
    ),
    [model],
  );

  const activeScale = scalesWithPpf.find((s) => s.id === activeScaleId) ?? null;
  const activePpf = activeScale?.points_per_foot ?? null;

  const openFile = useCallback(async (file: File) => {
    setError(null);
    try {
      const data = new Uint8Array(await file.arrayBuffer());
      const loaded = await pdfjs.getDocument({ data }).promise;

      const config = await loaded.getOptionalContentConfig();
      ocConfigRef.current = config;
      const rows: LayerRow[] = flattenOrder(config.getOrder()).map((id) => {
        const group = config.getGroup(id) as
          | { name: string | null; visible?: boolean }
          | null;
        return {
          id,
          name: group?.name ?? id,
          visible: group?.visible ?? true,
        };
      });

      // fit-width initial zoom
      const first = await loaded.getPage(1);
      const width = first.getViewport({ scale: 1 }).width;
      const avail = (viewerRef.current?.clientWidth ?? 1200) - 24;

      setDoc((prev) => {
        void prev?.destroy();
        return loaded;
      });
      setFileName(file.name);
      setLayers(rows);
      setPageNum(1);
      setZoom(Math.min(2, Math.max(0.1, avail / width)));
      setOcVersion((v) => v + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  const openModel = useCallback(
    async (file: File) => {
      setNotice(null);
      try {
        const parsed = JSON.parse(await file.text()) as SheetModel;
        if (!Array.isArray(parsed.paths) || !Array.isArray(parsed.size_pt)) {
          throw new Error('not a sheet-model JSON (expected paths[] and size_pt)');
        }
        setModel(parsed);
        setModelName(file.name);
        setMeasurements([]);
        setAnchor(null);
        const best = [...(parsed.scales ?? [])]
          .filter((s) => s.points_per_foot != null)
          .sort((a, b) => b.confidence - a.confidence)[0];
        setActiveScaleId(best?.id ?? null);
        if (doc && parsed.page_index < doc.numPages) {
          setPageNum(parsed.page_index + 1);
        }
        if (doc) {
          const page = await doc.getPage(parsed.page_index + 1);
          const vp = page.getViewport({ scale: 1 });
          if (
            Math.abs(vp.width - parsed.size_pt[0]) > 1 ||
            Math.abs(vp.height - parsed.size_pt[1]) > 1
          ) {
            setNotice(
              `Sheet model is ${parsed.size_pt[0]}×${parsed.size_pt[1]} pt but the page is ` +
                `${Math.round(vp.width)}×${Math.round(vp.height)} pt — is this the right sheet?`,
            );
          }
        }
      } catch (e) {
        setNotice(`Could not load sheet model: ${e instanceof Error ? e.message : e}`);
      }
    },
    [doc],
  );

  const toggleLayer = useCallback((id: string, visible: boolean) => {
    ocConfigRef.current?.setVisibility(id, visible);
    setLayers((rows) =>
      rows.map((r) => (r.id === id ? { ...r, visible } : r)),
    );
    setOcVersion((v) => v + 1);
  }, []);

  // ---- overlay drawing ---------------------------------------------------

  const drawOverlay = useCallback(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    const ctx = overlay.getContext('2d');
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, overlay.width / dpr, overlay.height / dpr);

    const toCss = (p: [number, number]): [number, number] => [p[0] * zoom, p[1] * zoom];

    const drawLabel = (text: string, cx: number, cy: number) => {
      ctx.font = '12px system-ui, sans-serif';
      const w = ctx.measureText(text).width;
      ctx.fillStyle = 'rgba(24, 68, 160, 0.92)';
      ctx.fillRect(cx - w / 2 - 4, cy - 18, w + 8, 16);
      ctx.fillStyle = '#fff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(text, cx, cy - 10);
    };

    const drawMeasureLine = (
      a: [number, number],
      b: [number, number],
      label: string,
      dashed: boolean,
    ) => {
      const [ax, ay] = toCss(a);
      const [bx, by] = toCss(b);
      ctx.strokeStyle = '#1844a0';
      ctx.lineWidth = 1.5;
      ctx.setLineDash(dashed ? [5, 4] : []);
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
      ctx.setLineDash([]);
      for (const [px, py] of [[ax, ay], [bx, by]] as const) {
        ctx.fillStyle = '#1844a0';
        ctx.beginPath();
        ctx.arc(px, py, 3, 0, Math.PI * 2);
        ctx.fill();
      }
      drawLabel(label, (ax + bx) / 2, (ay + by) / 2);
    };

    for (const m of measurements) {
      drawMeasureLine(m.a, m.b, formatLength(m.lengthPt, activePpf), false);
    }

    if (anchor && cursor) {
      const end: [number, number] = hover ? [hover.x, hover.y] : cursor;
      const len = Math.hypot(end[0] - anchor[0], end[1] - anchor[1]);
      drawMeasureLine(anchor, end, formatLength(len, activePpf), true);
    }

    if (hover) {
      const [hx, hy] = toCss([hover.x, hover.y]);
      ctx.strokeStyle = SNAP_COLOR[hover.kind];
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(hx, hy, 6, 0, Math.PI * 2);
      ctx.stroke();
    }
  }, [measurements, anchor, cursor, hover, zoom, activePpf]);

  const drawOverlayRef = useRef(drawOverlay);
  useEffect(() => {
    drawOverlayRef.current = drawOverlay;
    drawOverlay();
  }, [drawOverlay]);

  // ---- base render -------------------------------------------------------

  useEffect(() => {
    if (!doc) return;
    let cancelled = false;

    (async () => {
      const page = await doc.getPage(pageNum);
      const canvas = canvasRef.current;
      const overlay = overlayRef.current;
      if (!canvas || !overlay || cancelled) return;

      const viewport = page.getViewport({ scale: zoom });
      const dpr = window.devicePixelRatio || 1;
      for (const c of [canvas, overlay]) {
        c.width = Math.floor(viewport.width * dpr);
        c.height = Math.floor(viewport.height * dpr);
        c.style.width = `${Math.floor(viewport.width)}px`;
        c.style.height = `${Math.floor(viewport.height)}px`;
      }

      renderTaskRef.current?.cancel();
      const task = page.render({
        canvas,
        viewport,
        transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined,
        optionalContentConfigPromise: ocConfigRef.current
          ? Promise.resolve(ocConfigRef.current)
          : undefined,
      });
      renderTaskRef.current = task;
      try {
        await task.promise;
      } catch (e) {
        // a cancelled render throws — that's the expected path when the
        // user pages/zooms/toggles faster than we draw
        if (!(e instanceof Error && e.name === 'RenderingCancelledException')) {
          console.error(e);
        }
      }
      if (!cancelled) drawOverlayRef.current();
    })();

    return () => {
      cancelled = true;
    };
  }, [doc, pageNum, zoom, ocVersion]);

  // ---- measurement interactions -----------------------------------------

  const sheetPoint = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>): [number, number] => {
      const rect = e.currentTarget.getBoundingClientRect();
      return [(e.clientX - rect.left) / zoom, (e.clientY - rect.top) / zoom];
    },
    [zoom],
  );

  const onMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const p = sheetPoint(e);
      setCursor(p);
      setHover(snapIndex?.snap(p[0], p[1], SNAP_RADIUS_PX / zoom) ?? null);
    },
    [sheetPoint, snapIndex, zoom],
  );

  const onClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const raw = sheetPoint(e);
      const snapped = snapIndex?.snap(raw[0], raw[1], SNAP_RADIUS_PX / zoom);
      const p: [number, number] = snapped ? [snapped.x, snapped.y] : raw;
      if (!anchor) {
        setAnchor(p);
        return;
      }
      const lengthPt = Math.hypot(p[0] - anchor[0], p[1] - anchor[1]);
      if (lengthPt > 0) {
        setMeasurements((ms) => [
          ...ms,
          { id: nextMeasurementId.current++, a: anchor, b: p, lengthPt },
        ]);
      }
      setAnchor(null);
    },
    [anchor, sheetPoint, snapIndex, zoom],
  );

  useEffect(() => {
    if (!anchor) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setAnchor(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [anchor]);

  const pageCount = doc?.numPages ?? 0;
  const measureActive = Boolean(model && doc);
  const totalPt = measurements.reduce((sum, m) => sum + m.lengthPt, 0);

  return (
    <div className="app">
      <header className="toolbar">
        <strong className="brand">ElevationPDF</strong>
        <label className="file-btn">
          Open PDF
          <input
            type="file"
            accept="application/pdf"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void openFile(f);
              e.target.value = '';
            }}
          />
        </label>
        <label className="file-btn secondary">
          Open sheet model
          <input
            type="file"
            accept=".json,application/json"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void openModel(f);
              e.target.value = '';
            }}
          />
        </label>
        {doc && (
          <>
            <span className="file-name">{fileName}</span>
            <span className="spacer" />
            <button onClick={() => setPageNum((p) => Math.max(1, p - 1))} disabled={pageNum <= 1}>
              ◀
            </button>
            <span className="page-indicator">
              {pageNum} / {pageCount}
            </span>
            <button
              onClick={() => setPageNum((p) => Math.min(pageCount, p + 1))}
              disabled={pageNum >= pageCount}
            >
              ▶
            </button>
            <button onClick={() => setZoom((z) => z / 1.25)}>−</button>
            <span className="zoom-indicator">{Math.round(zoom * 100)}%</span>
            <button onClick={() => setZoom((z) => z * 1.25)}>+</button>
          </>
        )}
      </header>

      <div className="body">
        <aside className="sidebar">
          <h2>Layers</h2>
          {layers.length === 0 ? (
            <p className="muted">
              {doc
                ? 'No layer information in this PDF (no OCGs — plotted without "Include layer information").'
                : 'Open a plan set to list its layers.'}
            </p>
          ) : (
            <ul className="layer-list">
              {layers.map((l) => (
                <li key={l.id}>
                  <label>
                    <input
                      type="checkbox"
                      checked={l.visible}
                      onChange={(e) => toggleLayer(l.id, e.target.checked)}
                    />
                    {l.name}
                  </label>
                </li>
              ))}
            </ul>
          )}

          <h2>Scale</h2>
          {!model ? (
            <p className="muted">
              Open a sheet model (feasibility.py --json-dir) to enable snapping
              and measurement.
            </p>
          ) : scalesWithPpf.length === 0 ? (
            <p className="muted">
              No detected scale — lengths shown in points. Manual calibration
              lands here next.
            </p>
          ) : (
            <ul className="scale-list">
              {scalesWithPpf.map((s) => (
                <li key={s.id} className={s.id === activeScaleId ? 'active' : ''}>
                  <label>
                    <input
                      type="radio"
                      name="scale"
                      checked={s.id === activeScaleId}
                      onChange={() => setActiveScaleId(s.id)}
                    />
                    <span className="scale-method">{s.method.replace('_', ' ')}</span>
                    <span className="scale-ppf">{s.points_per_foot.toFixed(3)} pt/ft</span>
                    <span className="scale-conf">{Math.round(s.confidence * 100)}%</span>
                  </label>
                  <ul className="evidence">
                    {s.evidence.map((ev, i) => (
                      <li key={i}>{ev}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          )}

          {model && (
            <>
              <h2>Measurements</h2>
              <p className="muted model-name">{modelName}</p>
              {measurements.length === 0 ? (
                <p className="muted">
                  Click two points on the sheet — snapping to endpoints,
                  intersections, and midpoints. Esc cancels.
                </p>
              ) : (
                <ul className="measure-list">
                  {measurements.map((m) => (
                    <li key={m.id}>
                      <span className="measurement-value">
                        {formatLength(m.lengthPt, activePpf)}
                      </span>
                      <button
                        className="delete"
                        title="delete"
                        onClick={() =>
                          setMeasurements((ms) => ms.filter((x) => x.id !== m.id))
                        }
                      >
                        ×
                      </button>
                    </li>
                  ))}
                  {measurements.length > 1 && (
                    <li className="total">
                      <span>Σ {formatLength(totalPt, activePpf)}</span>
                      <button className="delete" onClick={() => setMeasurements([])}>
                        clear
                      </button>
                    </li>
                  )}
                </ul>
              )}
            </>
          )}
        </aside>

        <main className="viewer" ref={viewerRef}>
          {error && <div className="error">{error}</div>}
          {notice && <div className="notice">{notice}</div>}
          {!doc && !error && (
            <div className="placeholder">
              Open an AutoCAD-plotted PDF to see it rendered with layer
              toggling. Add its extracted sheet model to measure with snapping.
            </div>
          )}
          <div className="canvas-stack" data-zoom={zoom}>
            <canvas ref={canvasRef} />
            <canvas
              ref={overlayRef}
              className={`overlay${measureActive ? ' active' : ''}`}
              onMouseMove={measureActive ? onMove : undefined}
              onClick={measureActive ? onClick : undefined}
              onMouseLeave={() => {
                setHover(null);
                setCursor(null);
              }}
            />
          </div>
        </main>
      </div>

      <footer className="statusbar">
        {cursor
          ? `x ${cursor[0].toFixed(1)} pt · y ${cursor[1].toFixed(1)} pt`
          : model
            ? 'move over the sheet to snap'
            : ''}
        {hover && <span className="snap-kind"> · snap: {hover.kind}</span>}
        {activeScale && (
          <span className="active-scale">
            {' '}
            · scale: {activeScale.method.replace('_', ' ')} (
            {activeScale.points_per_foot.toFixed(3)} pt/ft)
          </span>
        )}
        {snapIndex && (
          <span className="muted"> · {snapIndex.size} segments indexed</span>
        )}
      </footer>
    </div>
  );
}
