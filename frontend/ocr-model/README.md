# Model for reading the machine-readable zone

`mrz.traineddata.gz` is the Tesseract LSTM model `tessdata_fast/mrz.traineddata` from
[DoubangoTelecom/tesseractMRZ](https://github.com/DoubangoTelecom/tesseractMRZ) (BSD
3-Clause, see `LICENSE`), compressed with gzip and otherwise unchanged. It is trained on
the zone's font (OCR-B) and its 37 characters, so it reads the filler `<` and the letters
of names that the general English model confused.

- SHA-256 of the uncompressed file: `ece2a54f125a73792f9cec74e6f8e62f6e5e00c7f19153ba865af689be5b5f01`
- Size: 1.3 MB compressed (the English model it replaced was 2.9 MB).

`scripts/ocr-model.mjs` copies it into `public/assets/ocr/` before `npm run dev` and
`npm run build`; `src/ocr.ts` loads it when a passport photo is read. The choice and the
measurements behind it are in `docs/decisions.md` → «Распознавание паспорта».
