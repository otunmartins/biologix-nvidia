/**
 * Loading 3Dmol.js once, for every viewer on the page.
 *
 * Two panels now render coordinates — the protein–polymer complex from a job,
 * and the candidate's own oligomer — and a second copy of the loader would
 * mean a second 800 KB download and two competing script promises. The
 * promise is module-scoped, so whichever viewer mounts first starts the
 * download and the rest await the same one.
 *
 * It stays a CDN load rather than a bundled dependency: WebGL this size has no
 * business in the main bundle for a library only two panels use.
 */

const THREEDMOL_SRC = "https://cdnjs.cloudflare.com/ajax/libs/3Dmol/2.4.2/3Dmol-min.js";

export type Viewer = {
  addModel: (data: string, format: string) => unknown;
  setStyle: (selection: object, style: object) => void;
  addSurface: (type: unknown, style: object, selection?: object) => void;
  removeAllSurfaces: () => void;
  removeAllModels: () => void;
  setBackgroundColor: (color: string, alpha?: number) => void;
  zoomTo: (selection?: object) => void;
  zoom: (factor: number, duration?: number) => void;
  render: () => void;
  spin: (axis: string | boolean, speed?: number) => void;
  setViewStyle: (style: object) => void;
  getModel: (index?: number) => { setFrame?: (n: number) => void } | undefined;
  setFrame: (frame: number) => Promise<void> | void;
  getNumFrames?: () => number;
  resize: () => void;
  clear: () => void;
  pngURI?: () => string;
  rotate?: (angle: number, axis?: string, duration?: number) => void;
};

declare global {
  interface Window {
    $3Dmol?: {
      createViewer: (element: HTMLElement, config: object) => Viewer;
      SurfaceType: { VDW: unknown; SAS: unknown; MS?: unknown };
    };
  }
}

let scriptPromise: Promise<void> | null = null;

export function load3Dmol(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.$3Dmol) return Promise.resolve();
  if (scriptPromise) return scriptPromise;
  scriptPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = THREEDMOL_SRC;
    script.async = true;
    script.onload = () => resolve();
    script.onerror = () => {
      // A failed load must not be cached as a pending promise, or every later
      // viewer waits forever on a download that already failed.
      scriptPromise = null;
      reject(new Error("the 3D viewer library could not be loaded from the CDN"));
    };
    document.head.appendChild(script);
  });
  return scriptPromise;
}
