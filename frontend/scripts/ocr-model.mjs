// The model for reading passports in the browser (src/ocr.ts), copied from ocr-model/
// into public/, which Vite serves in dev and copies into the build. The engine asks for
// <folder>/mrz.traineddata.gz, so the file keeps its name and the folder carries the
// model's version: change the folder here and in src/ocr.ts when the model changes, so
// that browsers do not read with a cached old one. Runs before `npm run dev` and
// `npm run build`.
import { copyFileSync, mkdirSync } from "node:fs";

const source = new URL("../ocr-model/mrz.traineddata.gz", import.meta.url);
const folder = new URL("../public/assets/ocr/mrz-fast-1/", import.meta.url);
mkdirSync(folder, { recursive: true });
copyFileSync(source, new URL("mrz.traineddata.gz", folder));
