import workerUrl from "tesseract.js/dist/worker.min.js?url";
import coreUrl from "tesseract.js-core/tesseract-core-lstm.wasm.js?url";
import coreSimdUrl from "tesseract.js-core/tesseract-core-simd-lstm.wasm.js?url";
import { simd } from "wasm-feature-detect";

import { cutZone, findZones } from "./mrzZone";

// Reading the machine-readable zone of a passport photo (backlog 2.5) right in the
// browser: the photo never leaves the device, only the recognised lines go to the API,
// which checks them (backend/app/core/mrz.py). The recognition engine and its model are
// served from this site, not from a CDN, and load only when a photo is read.
//
// The model knows only the zone's font and its 37 characters (frontend/ocr-model): the
// general English one read the filler «<» as K, L or C and took a letter of a name for
// another. The zone is first found on the photo and cut out level (mrzZone.ts): the
// engine reads that strip, not the photo.

const MRZ_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<";
// Where the build puts the model (scripts/ocr-model.mjs).
const MODEL_DIR = "/assets/ocr/mrz-fast-1";
const MODEL = "mrz";
// The height of the zone's characters the model reads best, and a larger one to try when
// the first reading does not check out.
const CHAR_HEIGHTS = [26, 34];
// A whole photo, when no zone was found on it: wide enough for the small characters of the
// zone, small enough to be quick on a phone.
const MAX_WIDTH = 1800;

type Whole = { rotate: 0 | 90 | 180 | 270; fromTop: number };
// The zone is at the bottom of the data page: the lower half first, then its lower part,
// then the whole photo, then the photo turned over or on its side.
const WHOLE: Whole[] = [
  { rotate: 0, fromTop: 0.5 },
  { rotate: 0, fromTop: 0.7 },
  { rotate: 0, fromTop: 0 },
  { rotate: 180, fromTop: 0.5 },
  { rotate: 90, fromTop: 0 },
  { rotate: 270, fromTop: 0 },
];

export class PhotoUnreadable extends Error {}

/** What may be lines of a zone: long lines of letters, digits and fillers. Which of them
 * are the zone the API decides: the first line of an ID card may have no fillers in a row. */
export function candidateLines(text: string): string[] {
  return text
    .toUpperCase()
    .split("\n")
    .map((line) => line.replace(/\s+/g, "").replace(/«/g, "<<"))
    .filter((line) => line.length >= 28 && /^[A-Z0-9<]+$/.test(line));
}

/** The text of the zone from each way of reading the photo that finds lines: the zone cut
 * out of the photo first, upright and turned over, at two sizes; then the photo itself.
 * Each way misreads different characters, so the caller checks each reading and stops
 * asking at one that agrees; the engine shuts down then. */
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
  let ways = 1;
  const worker = await createWorker(MODEL, 1, {
    workerPath: workerUrl,
    corePath: withSimd ? coreSimdUrl : coreUrl,
    langPath: new URL(MODEL_DIR, window.location.origin).href,
    workerBlobURL: false,
    // The browser caches the files; nothing goes to IndexedDB.
    cacheMethod: "none",
    logger: (m) => {
      if (m.status === "recognizing text") onProgress((attempt + m.progress) / ways);
    },
  });
  try {
    await worker.setParameters({
      tessedit_char_whitelist: MRZ_CHARS,
      tessedit_pageseg_mode: PSM.SINGLE_BLOCK,
    });
    const zones = findZones(bitmap);
    const pictures: (() => HTMLCanvasElement)[] = [
      ...zones.flatMap((zone) =>
        CHAR_HEIGHTS.flatMap((height) => [
          () => cutZone(bitmap, zone, height, false),
          () => cutZone(bitmap, zone, height, true),
        ]),
      ),
      ...WHOLE.map((way) => () => whole(bitmap, way)),
    ];
    ways = pictures.length;
    for (; attempt < ways; attempt++) {
      const { data } = await worker.recognize(pictures[attempt]());
      const lines = candidateLines(data.text);
      if (lines.length >= 2) yield lines.join("\n");
    }
  } finally {
    bitmap.close();
    await worker.terminate();
  }
}

/** The part of the photo to read, turned upright and scaled down. */
function whole(bitmap: ImageBitmap, { rotate, fromTop }: Whole): HTMLCanvasElement {
  const sideways = rotate === 90 || rotate === 270;
  const scale = Math.min(1, MAX_WIDTH / (sideways ? bitmap.height : bitmap.width));
  const all = canvas(
    Math.round((sideways ? bitmap.height : bitmap.width) * scale),
    Math.round((sideways ? bitmap.width : bitmap.height) * scale),
  );
  all.ctx.translate(all.canvas.width / 2, all.canvas.height / 2);
  all.ctx.rotate((rotate * Math.PI) / 180);
  all.ctx.scale(scale, scale);
  all.ctx.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2);

  const top = Math.round(all.canvas.height * fromTop);
  const part = canvas(all.canvas.width, all.canvas.height - top);
  part.ctx.drawImage(all.canvas, 0, -top);
  return part.canvas;
}

function canvas(width: number, height: number) {
  const element = document.createElement("canvas");
  element.width = width;
  element.height = height;
  const ctx = element.getContext("2d");
  if (!ctx) throw new PhotoUnreadable();
  return { canvas: element, ctx };
}
