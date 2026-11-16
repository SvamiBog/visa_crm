// Finding the machine-readable zone on a passport photo before it is read (backlog 2.5).
// The engine reads a small level strip in a fraction of a second and almost without
// mistakes, and a whole photo slowly and badly: a tilted, dark or sideways one not at all.
// The zone is two or three long lines of evenly spaced characters, so in a small grey copy
// of the photo it shows as long thin streaks of dense vertical strokes, one under another.
// Plain arithmetic on the pixels, some tens of milliseconds; nothing leaves the browser.

/** The longer side of the copy the zone is looked for in. */
const WORK_SIDE = 720;
// A line of the zone against the width of the copy, and against its own height.
const MIN_LENGTH = 0.18;
const MIN_ASPECT = 9;
const MIN_THICKNESS = 3;
// How far from level a line may be, in radians (about 28°): beyond it the photo is read
// turned on its side.
const MAX_TILT = 0.5;
// Lines of one zone: parallel, of one length, one right under another.
const SAME_TILT = 0.07;
const SAME_LENGTH = 0.8;
const MAX_SHIFT = 0.1;
const LINE_GAP = [1.2, 4.5] as const;

type Streak = {
  cx: number;
  cy: number;
  angle: number;
  length: number;
  thickness: number;
};

/** A zone in the copy of the photo turned by `turn` degrees; `scale` is the copy's pixels
 * per pixel of the photo. */
export type Zone = {
  turn: 0 | 90;
  scale: number;
  cx: number;
  cy: number;
  angle: number;
  length: number;
  height: number;
  /** The height of a line, close to the height of its characters. */
  thickness: number;
  score: number;
};

/** The zones found on the photo as it is and turned on its side, the likeliest first.
 * A zone upside down looks the same as one upright: the reader tries both. */
export function findZones(bitmap: ImageBitmap): Zone[] {
  const scale = Math.min(1, WORK_SIDE / Math.max(bitmap.width, bitmap.height));
  const zones: Zone[] = [];
  for (const turn of [0, 90] as const) {
    const copy = canvas(...turnedSize(bitmap, turn, scale));
    drawTurned(copy.ctx, bitmap, turn, scale);
    const { width, height } = copy.canvas;
    const zone = zoneOf(grey(copy.ctx, width, height), width, height);
    if (zone) zones.push({ ...zone, turn, scale });
  }
  return zones.sort((a, b) => b.score - a.score);
}

/** The zone cut out of the photo: level, its characters about `charHeight` pixels high,
 * on white, turned over if asked. */
export function cutZone(
  bitmap: ImageBitmap,
  zone: Zone,
  charHeight: number,
  upsideDown: boolean,
): HTMLCanvasElement {
  const k = zone.scale;
  // A small photo is enlarged, but not beyond what its pixels can show.
  const s = Math.min(3, charHeight / (zone.thickness / k));
  const part = canvas(
    Math.round((zone.length / k) * s * 1.08 + charHeight * 2),
    Math.round((zone.height / k) * s + charHeight * 1.6),
  );
  const { ctx } = part;
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, part.canvas.width, part.canvas.height);
  ctx.imageSmoothingQuality = "high";
  ctx.translate(part.canvas.width / 2, part.canvas.height / 2);
  if (upsideDown) ctx.rotate(Math.PI);
  ctx.rotate(-zone.angle);
  ctx.scale(s / k, s / k);
  ctx.translate(-zone.cx, -zone.cy);
  drawTurned(ctx, bitmap, zone.turn, k);
  return part.canvas;
}

function turnedSize(bitmap: ImageBitmap, turn: 0 | 90, scale: number): [number, number] {
  const [w, h] = turn === 90 ? [bitmap.height, bitmap.width] : [bitmap.width, bitmap.height];
  return [Math.round(w * scale), Math.round(h * scale)];
}

/** Draws the photo turned and scaled, its top left corner at the origin. */
function drawTurned(
  ctx: CanvasRenderingContext2D,
  bitmap: ImageBitmap,
  turn: 0 | 90,
  scale: number,
): void {
  const [w, h] = turn === 90 ? [bitmap.height, bitmap.width] : [bitmap.width, bitmap.height];
  ctx.translate((w * scale) / 2, (h * scale) / 2);
  ctx.rotate((turn * Math.PI) / 180);
  ctx.scale(scale, scale);
  ctx.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2);
}

function canvas(width: number, height: number) {
  const element = document.createElement("canvas");
  element.width = width;
  element.height = height;
  const ctx = element.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("no 2d context");
  return { canvas: element, ctx };
}

function grey(ctx: CanvasRenderingContext2D, width: number, height: number): Float32Array {
  const px = ctx.getImageData(0, 0, width, height).data;
  const out = new Float32Array(width * height);
  for (let i = 0, j = 0; j < out.length; i += 4, j++) {
    out[j] = 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2];
  }
  return out;
}

/** The mean over a box of (2·rx + 1) × (2·ry + 1), by running sums. */
function boxBlur(src: Float32Array, w: number, h: number, rx: number, ry: number): Float32Array {
  const rows = new Float32Array(w * h);
  const out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    let sum = 0;
    for (let x = -rx; x < w; x++) {
      if (x + rx < w) sum += src[y * w + x + rx];
      if (x - rx - 1 >= 0) sum -= src[y * w + x - rx - 1];
      if (x >= 0) rows[y * w + x] = sum / (2 * rx + 1);
    }
  }
  for (let x = 0; x < w; x++) {
    let sum = 0;
    for (let y = -ry; y < h; y++) {
      if (y + ry < h) sum += rows[(y + ry) * w + x];
      if (y - ry - 1 >= 0) sum -= rows[(y - ry - 1) * w + x];
      if (y >= 0) out[y * w + x] = sum / (2 * ry + 1);
    }
  }
  return out;
}

/** Otsu's threshold: the value that best splits the picture into two kinds of pixels. */
function threshold(values: Float32Array): number {
  let max = 0;
  for (const v of values) if (v > max) max = v;
  if (max === 0) return 0;
  const histogram = new Float64Array(256);
  for (const v of values) histogram[Math.min(255, Math.floor((v / max) * 255))]++;
  let total = 0;
  for (let i = 0; i < 256; i++) total += i * histogram[i];
  let below = 0;
  let sumBelow = 0;
  let best = 0;
  let level = 0;
  for (let i = 0; i < 256; i++) {
    below += histogram[i];
    if (below === 0) continue;
    const above = values.length - below;
    if (above === 0) break;
    sumBelow += i * histogram[i];
    const gap = sumBelow / below - (total - sumBelow) / above;
    const between = below * above * gap * gap;
    if (between > best) {
      best = between;
      level = i;
    }
  }
  return (level / 255) * max;
}

/** Long thin patches where brightness changes often from left to right: lines of text.
 * Each is measured along its own direction, so a tilted line is as long as a level one. */
function streaks(light: Float32Array, w: number, h: number): Streak[] {
  const change = new Float32Array(w * h);
  for (let y = 0; y < h; y++) {
    for (let x = 1; x < w - 1; x++) {
      change[y * w + x] = Math.abs(light[y * w + x + 1] - light[y * w + x - 1]);
    }
  }
  // Smeared along the line, the characters of one line join; the lines stay apart.
  const dense = boxBlur(change, w, h, Math.round(w / 48), 1);
  const level = threshold(dense);
  const seen = new Uint8Array(w * h);
  const found: Streak[] = [];
  const stack: number[] = [];
  for (let start = 0; start < w * h; start++) {
    if (dense[start] <= level || seen[start]) continue;
    const points: number[] = [];
    let sx = 0;
    let sy = 0;
    let sxx = 0;
    let syy = 0;
    let sxy = 0;
    seen[start] = 1;
    stack.push(start);
    while (stack.length > 0) {
      const p = stack.pop() as number;
      const x = p % w;
      const y = (p - x) / w;
      points.push(p);
      sx += x;
      sy += y;
      sxx += x * x;
      syy += y * y;
      sxy += x * y;
      const around = [x > 0 ? p - 1 : -1, x < w - 1 ? p + 1 : -1, p - w, p + w];
      for (const q of around) {
        if (q >= 0 && q < w * h && dense[q] > level && !seen[q]) {
          seen[q] = 1;
          stack.push(q);
        }
      }
    }
    const n = points.length;
    if (n < w * MIN_LENGTH * MIN_THICKNESS) continue;
    const mx = sx / n;
    const my = sy / n;
    // The direction of the patch: the main axis of its pixels.
    const angle =
      0.5 * Math.atan2(2 * (sxy / n - mx * my), sxx / n - mx * mx - (syy / n - my * my));
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    let from = Infinity;
    let to = -Infinity;
    for (const p of points) {
      const x = p % w;
      const along = (x - mx) * cos + ((p - x) / w - my) * sin;
      if (along < from) from = along;
      if (along > to) to = along;
    }
    const length = to - from;
    const thickness = n / length;
    if (
      length >= w * MIN_LENGTH &&
      length / thickness >= MIN_ASPECT &&
      thickness >= MIN_THICKNESS &&
      Math.abs(angle) < MAX_TILT
    ) {
      const middle = (from + to) / 2;
      found.push({ cx: mx + middle * cos, cy: my + middle * sin, angle, length, thickness });
    }
  }
  return found;
}

/** Two or three streaks that lie as the lines of a zone do; of several such groups, the
 * one with the longest lines and the most of them. */
function zoneOf(light: Float32Array, w: number, h: number): Omit<Zone, "turn" | "scale"> | null {
  const all = streaks(light, w, h).sort((a, b) => a.cy - b.cy);
  let best: Omit<Zone, "turn" | "scale"> | null = null;
  for (let i = 0; i < all.length; i++) {
    const lines = [all[i]];
    for (let j = i + 1; j < all.length && lines.length < 3; j++) {
      const a = lines[lines.length - 1];
      const b = all[j];
      const cos = Math.cos(a.angle);
      const sin = Math.sin(a.angle);
      const along = (b.cx - a.cx) * cos + (b.cy - a.cy) * sin;
      const across = -(b.cx - a.cx) * sin + (b.cy - a.cy) * cos;
      if (
        Math.abs(a.angle - b.angle) < SAME_TILT &&
        Math.min(a.length, b.length) / Math.max(a.length, b.length) > SAME_LENGTH &&
        Math.abs(along) < a.length * MAX_SHIFT &&
        across > a.thickness * LINE_GAP[0] &&
        across < a.thickness * LINE_GAP[1]
      ) {
        lines.push(b);
      }
    }
    if (lines.length < 2) continue;
    const length = Math.max(...lines.map((line) => line.length));
    const score = length * lines.length;
    if (best !== null && score <= best.score) continue;
    const mean = (pick: (line: Streak) => number) =>
      lines.reduce((sum, line) => sum + pick(line), 0) / lines.length;
    const thickness = mean((line) => line.thickness);
    const first = lines[0];
    const last = lines[lines.length - 1];
    best = {
      cx: mean((line) => line.cx),
      cy: mean((line) => line.cy),
      angle: mean((line) => line.angle),
      length,
      height: Math.hypot(last.cx - first.cx, last.cy - first.cy) + thickness * 1.6,
      thickness,
      score,
    };
  }
  return best;
}
