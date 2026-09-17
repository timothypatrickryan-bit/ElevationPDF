import { useCallback, useEffect, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import type { PDFDocumentProxy, RenderTask } from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

type OptionalContentConfig = Awaited<
  ReturnType<PDFDocumentProxy['getOptionalContentConfig']>
>;

interface LayerRow {
  id: string;
  name: string;
  visible: boolean;
}

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

export function App() {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [fileName, setFileName] = useState<string>('');
  const [pageNum, setPageNum] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [layers, setLayers] = useState<LayerRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  // bumped on every visibility toggle so the render effect re-runs
  const [ocVersion, setOcVersion] = useState(0);

  const ocConfigRef = useRef<OptionalContentConfig | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const overlayRef = useRef<HTMLCanvasElement>(null);
  const viewerRef = useRef<HTMLDivElement>(null);
  const renderTaskRef = useRef<RenderTask | null>(null);

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

  const toggleLayer = useCallback((id: string, visible: boolean) => {
    ocConfigRef.current?.setVisibility(id, visible);
    setLayers((rows) =>
      rows.map((r) => (r.id === id ? { ...r, visible } : r)),
    );
    setOcVersion((v) => v + 1);
  }, []);

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
    })();

    return () => {
      cancelled = true;
    };
  }, [doc, pageNum, zoom, ocVersion]);

  const pageCount = doc?.numPages ?? 0;

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
        </aside>

        <main className="viewer" ref={viewerRef}>
          {error && <div className="error">{error}</div>}
          {!doc && !error && (
            <div className="placeholder">
              Open an AutoCAD-plotted PDF to see it rendered with layer
              toggling. Measurement tools land on the overlay canvas next.
            </div>
          )}
          <div className="canvas-stack">
            <canvas ref={canvasRef} />
            {/* overlay: measurements + snapping render here (PLAN §14 step 5) */}
            <canvas ref={overlayRef} className="overlay" />
          </div>
        </main>
      </div>
    </div>
  );
}
