import workerUrl from "tesseract.js/dist/worker.min.js?url";
import coreUrl from "tesseract.js-core/tesseract-core-lstm.wasm.js?url";
import coreSimdUrl from "tesseract.js-core/tesseract-core-simd-lstm.wasm.js?url";
import { simd } from "wasm-feature-detect";

// Reading the machine-readable zone of a passport photo (backlog 2.5) right in the
// browser: the photo never leaves the device, only the recognised lines go to the API,
// which checks them (backend/app/core/mrz.py). The recognition engine and its English
// model are served from this site, not from a CDN, and load only when a photo is read.

const MRZ_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<";
// Where the build puts the English model (vite.config.ts, OCR_MODEL_DIR).
const MODEL_DIR = "/assets/ocr/eng-4.0.0_best_int";
// Wide enough for the small characters of the zone, small enough to be quick on a phone.
const MAX_WIDTH = 1800;

type Attempt = { rotate: 0 | 90 | 180 | 270; fromTop: number; grey?: boolean };
// The zone is at the bottom of the data page: try the lower half first, then its lower part,
// then the whole photo, then the photo turned over or on its side. The engine makes its own
// greys from a colour photo; ours lost the first characters of a Belarusian passport's zone
// that the engine's read whole, so ours are only one more way to try. Each way misreads
// different characters, so the caller checks each reading and stops at one that agrees.
const ATTEMPTS: Attempt[] = [
  { rotate: 0, fromTop: 0.5 },
  { rotate: 0, fromTop: 0.7 },
  { rotate: 0, fromTop: 0.5, grey: true },
  { rotate: 0, fromTop: 0 },
  { rotate: 180, fromTop: 0.5 },
  { rotate: 90, fromTop: 0 },
  { rotate: 270, fromTop: 0 },
];

export class PhotoUnreadable extends Error {}

/** The zone's lines: long lines of letters, digits and fillers, from the first one with
 * fillers in a row (the zone's first line always has them; a data line may not). */
export function zoneLines(text: string): string[] {
  const lines = text
    .toUpperCase()
    .split("\n")
    .map((line) => line.replace(/\s+/g, "").replace(/«/g, "<<"))
    .filter((line) => line.length >= 28 && /^[A-Z0-9<]+$/.test(line));
  const first = lines.findIndex((line) => line.includes("<<"));
  return first < 0 ? [] : lines.slice(first, first + 3);
}

/** The text of the zone from each attempt that finds its lines, in the order of ATTEMPTS.
 * The caller stops asking once a reading checks out; the engine shuts down then. */
export async function* readZones(
  file: File,
  onProgress: (share: number) => void,
): AsyncGenerator<string, void, undefined> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    // HEIC outside Safari, a damaged file or not an image.
    throw new PhotoUnreadable();
  }
  const [{ createWorker, PSM }, withSimd] = await Promise.all([import("tesseract.js"), simd()]);
  let attempt = 0;
  const worker = await createWorker("eng", 1, {
    workerPath: workerUrl,
    corePath: withSimd ? coreSimdUrl : coreUrl,
    langPath: new URL(MODEL_DIR, window.location.origin).href,
    workerBlobURL: false,
    // The browser caches the files; nothing goes to IndexedDB.
    cacheMethod: "none",
    logger: (m) => {
      if (m.status === "recognizing text") {
        onProgress((attempt + m.progress) / ATTEMPTS.length);
      }
    },
  });
  try {
    await worker.setParameters({
      tessedit_char_whitelist: MRZ_CHARS,
      tessedit_pageseg_mode: PSM.SINGLE_BLOCK,
    });
    for (; attempt < ATTEMPTS.length; attempt++) {
      const { data } = await worker.recognize(prepare(bitmap, ATTEMPTS[attempt]));
      const lines = zoneLines(data.text);
      if (lines.length >= 2) yield lines.join("\n");
    }
  } finally {
    bitmap.close();
    await worker.terminate();
  }
}

/** The part of the photo to read, turned upright, scaled down and, if asked, in greys. */
function prepare(bitmap: ImageBitmap, { rotate, fromTop, grey }: Attempt): HTMLCanvasElement {
  const sideways = rotate === 90 || rotate === 270;
  const scale = Math.min(1, MAX_WIDTH / (sideways ? bitmap.height : bitmap.width));
  const whole = canvas(
    Math.round((sideways ? bitmap.height : bitmap.width) * scale),
    Math.round((sideways ? bitmap.width : bitmap.height) * scale),
  );
  whole.ctx.translate(whole.canvas.width / 2, whole.canvas.height / 2);
  whole.ctx.rotate((rotate * Math.PI) / 180);
  whole.ctx.scale(scale, scale);
  whole.ctx.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2);

  const top = Math.round(whole.canvas.height * fromTop);
  const part = canvas(whole.canvas.width, whole.canvas.height - top);
  part.ctx.drawImage(whole.canvas, 0, -top);
  if (!grey) return part.canvas;
  const image = part.ctx.getImageData(0, 0, part.canvas.width, part.canvas.height);
  const px = image.data;
  for (let i = 0; i < px.length; i += 4) {
    const grey = 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2];
    px[i] = px[i + 1] = px[i + 2] = grey;
  }
  part.ctx.putImageData(image, 0, 0);
  return part.canvas;
}

function canvas(width: number, height: number) {
  const element = document.createElement("canvas");
  element.width = width;
  element.height = height;
  const ctx = element.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new PhotoUnreadable();
  return { canvas: element, ctx };
}
