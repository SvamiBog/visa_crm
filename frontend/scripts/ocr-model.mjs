// The English model for reading passports in the browser (src/ocr.ts), copied from its
// npm package into public/, which Vite serves in dev and copies into the build. The
// engine asks for <folder>/eng.traineddata.gz, so the file keeps its name and the folder
// carries the model's version. Runs before `npm run dev` and `npm run build`.
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";

const source = createRequire(import.meta.url).resolve(
  "@tesseract.js-data/eng/4.0.0_best_int/eng.traineddata.gz",
);
const folder = new URL("../public/assets/ocr/eng-4.0.0_best_int/", import.meta.url);
mkdirSync(folder, { recursive: true });
copyFileSync(source, new URL("eng.traineddata.gz", folder));
