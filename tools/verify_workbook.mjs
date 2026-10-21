import fs from "node:fs/promises";
import process from "node:process";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [inputPath, previewDir] = process.argv.slice(2);
if (!inputPath || !previewDir) {
  throw new Error("usage: verify_workbook.mjs INPUT_XLSX PREVIEW_DIR");
}

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheets = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 3000 });
console.log(sheets.ndjson);
const results = await workbook.inspect({
  kind: "table",
  range: "Overview!A2:E28",
  include: "values,formulas",
  tableMaxRows: 15,
  tableMaxCols: 10,
  maxChars: 10000,
});
console.log(results.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errors.ndjson);

await fs.mkdir(previewDir, { recursive: true });
const previewRanges = {
  "Overview": "A1:E28",
  "Financial statements": "A1:H25",
  "PDF content": "A1:C12",
  "Search attempts": "A1:L18",
  "Audit": "A1:D18",
};
for (const [sheetName, range] of Object.entries(previewRanges)) {
  const preview = await workbook.render({
    sheetName,
    range,
    autoCrop: "all",
    scale: 1.5,
    format: "png",
  });
  await fs.writeFile(
    `${previewDir}/${sheetName.toLowerCase().replaceAll(" ", "-")}.png`,
    new Uint8Array(await preview.arrayBuffer()),
  );
}
