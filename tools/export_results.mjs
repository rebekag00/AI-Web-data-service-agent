import fs from "node:fs/promises";
import process from "node:process";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("usage: export_results.mjs INPUT_JSON OUTPUT_XLSX");

function excelText(value) {
  return value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, "");
}

const payload = JSON.parse(
  await fs.readFile(inputPath, "utf8"),
  (_key, value) => typeof value === "string" ? excelText(value) : value,
);
const { manifest, audit, pdf_content: pdfContent = {} } = payload;
const workbook = Workbook.create();
const overview = workbook.worksheets.add("Overview");
const financials = workbook.worksheets.add("Financial statements");
const pdfPages = workbook.worksheets.add("PDF content");
const attempts = workbook.worksheets.add("Search attempts");
const events = workbook.worksheets.add("Audit");

const font = "Arial";
const navy = "#16355C";
const blue = "#245F9E";
const pale = "#E9F1F8";
const paleGreen = "#E8F4EC";
const border = "#D9E0E7";
const ink = "#17202A";
const muted = "#607080";
const sheets = [overview, financials, pdfPages, attempts, events];

for (const sheet of sheets) sheet.showGridLines = false;
overview.tabColor = navy;
financials.tabColor = blue;
pdfPages.tabColor = "#397FAF";
attempts.tabColor = "#7198B8";
events.tabColor = "#9FB2C2";

function title(sheet, range, text) {
  sheet.getRange(range).merge();
  const cell = range.split(":")[0];
  sheet.getRange(cell).values = [[text]];
  sheet.getRange(cell).format.font = { name: font, size: 16, bold: true, color: ink };
}

function header(range) {
  range.format = {
    fill: navy,
    font: { name: font, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: border },
  };
}

function body(range) {
  range.format = {
    font: { name: font, size: 9, color: ink },
    verticalAlignment: "top",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: border },
  };
}

const intent = manifest.intent || {};
const source = manifest.selected_source || {};
const year = Number(intent.year) || new Date().getFullYear();
const priorYear = year - 1;
const pages = pdfContent.pages || [];
const financialRows = pdfContent.financial_rows || [];
const metrics = pdfContent.metrics || [];

title(overview, "A2:E2", "Annual report — organized results");
overview.getRange("A3:E3").format.borders = { bottom: { style: "thin", color: blue } };
overview.getRange("A5:B11").values = [
  ["Company", intent.company || ""],
  ["Report year", year],
  ["Document", source.title || ""],
  ["Original PDF", source.url || ""],
  ["PDF pages extracted", pdfContent.page_count || pages.length || ""],
  ["Validation", source.company_match && source.year_match && source.report_match && source.original_document ? "Verified" : "Review needed"],
  ["Search language", `${(intent.language || "unknown").toUpperCase()} prompt → ${(intent.search_language || intent.language || "unknown").toUpperCase()} web${intent.jurisdiction && intent.jurisdiction !== "unknown" ? ` (${intent.jurisdiction})` : ""}`],
];
overview.getRange("A5:A11").format = { fill: pale, font: { name: font, size: 10, bold: true, color: navy }, borders: { preset: "all", style: "thin", color: border } };
overview.getRange("B5:B11").format = { font: { name: font, size: 10, color: ink }, wrapText: true, verticalAlignment: "top", borders: { preset: "all", style: "thin", color: border } };
overview.getRange("D5:E5").merge();
overview.getRange("D5").values = [["Verification status"]];
overview.getRange("D5:E5").format = { fill: paleGreen, font: { name: font, size: 10, bold: true, color: "#245C39" }, horizontalAlignment: "center" };
overview.getRange("D6:E10").values = [
  ["Company", source.company_match ? "Verified" : "Not verified"],
  ["Year", source.year_match ? "Verified" : "Not verified"],
  ["Annual-report context", source.report_match ? "Verified" : "Not verified"],
  ["Original PDF", source.original_document ? "Verified" : "Not verified"],
  ["Restricted access", source.restricted ? "Yes" : "No"],
];
overview.getRange("D6:E10").format = { font: { name: font, size: 10, color: ink }, borders: { preset: "all", style: "thin", color: border } };
overview.getRange("D6:D10").format.font = { name: font, size: 10, bold: true, color: navy };

overview.getRange("A14:E14").values = [["Key financial metric (CZK thousands)", String(year), String(priorYear), "Change", "Change %"]];
header(overview.getRange("A14:E14"));
if (metrics.length) {
  overview.getRange("A15").write(metrics.map((item) => [item.metric, item.current ?? "", item.prior ?? "", "", ""]));
  for (let row = 15; row < 15 + metrics.length; row += 1) {
    overview.getRange(`D${row}`).formulas = [[`=B${row}-C${row}`]];
    overview.getRange(`E${row}`).formulas = [[`=IF(C${row}=0,"n.a.",D${row}/ABS(C${row}))`]];
  }
}
const lastMetricRow = Math.max(15, 14 + metrics.length);
body(overview.getRange(`A15:E${lastMetricRow}`));
overview.getRange(`B15:D${lastMetricRow}`).format.numberFormat = "#,##0;[Red]-#,##0";
overview.getRange(`E15:E${lastMetricRow}`).format.numberFormat = "0.0%;[Red]-0.0%";
overview.getRange(`A15:A${lastMetricRow}`).format.font = { name: font, size: 9, bold: true, color: ink };

const noteRow = lastMetricRow + 3;
overview.getRange(`A${noteRow}:E${noteRow}`).merge();
overview.getRange(`A${noteRow}`).values = [["Workbook guide"]];
overview.getRange(`A${noteRow}:E${noteRow}`).format = { fill: pale, font: { name: font, size: 10, bold: true, color: navy } };
overview.getRange(`A${noteRow + 1}:E${noteRow + 1}`).merge();
overview.getRange(`A${noteRow + 1}`).values = [["Financial statements contains the structured numeric tables. PDF content contains the extracted text for every page. Search attempts and Audit preserve how the agent found and verified the document."]];
overview.getRange(`A${noteRow + 1}:E${noteRow + 1}`).format = { font: { name: font, size: 9, color: muted }, wrapText: true, verticalAlignment: "top", borders: { preset: "all", style: "thin", color: border } };
overview.getRange(`A${noteRow + 1}:E${noteRow + 1}`).format.rowHeight = 42;
overview.getRange("A:A").format.columnWidth = 34;
overview.getRange("B:B").format.columnWidth = 58;
overview.getRange("C:E").format.columnWidth = 18;
overview.freezePanes.freezeRows(4);

title(financials, "A2:H2", "Structured financial statements");
financials.getRange("A3:H3").merge();
financials.getRange("A3").values = [[`Source: ${source.url || ""} · Values in CZK thousands · PDF pages shown in the last column`]];
financials.getRange("A3:H3").format = { font: { name: font, size: 9, color: muted }, wrapText: true };
financials.getRange("A5:H5").values = [["Statement", "Code", "Line item", "Gross", "Adjustments", String(year), String(priorYear), "PDF page"]];
header(financials.getRange("A5:H5"));
const financialValues = financialRows.map((item) => [
  item.statement || "", item.code || "", item.item || "", item.gross ?? "", item.adjustments ?? "",
  item.current ?? "", item.prior ?? "", item.page ?? "",
]);
if (financialValues.length) financials.getRange("A6").write(financialValues);
const lastFinancialRow = Math.max(6, 5 + financialValues.length);
body(financials.getRange(`A6:H${lastFinancialRow}`));
financials.getRange(`D6:G${lastFinancialRow}`).format.numberFormat = "#,##0;[Red]-#,##0";
financials.getRange(`H6:H${lastFinancialRow}`).format.horizontalAlignment = "center";
financials.getRange("A:A").format.columnWidth = 24;
financials.getRange("B:B").format.columnWidth = 14;
financials.getRange("C:C").format.columnWidth = 64;
financials.getRange("D:G").format.columnWidth = 18;
financials.getRange("H:H").format.columnWidth = 11;
financials.freezePanes.freezeRows(5);

title(pdfPages, "A2:C2", "Full PDF content — page by page");
pdfPages.getRange("A3:C3").merge();
pdfPages.getRange("A3").values = [[`Source: ${source.url || ""} · ${pdfContent.page_count || pages.length || 0} pages extracted`]];
pdfPages.getRange("A3:C3").format = { font: { name: font, size: 9, color: muted }, wrapText: true };
pdfPages.getRange("A5:C5").values = [["PDF page", "Detected section", "Extracted page text"]];
header(pdfPages.getRange("A5:C5"));
const pageValues = pages.map((item) => [item.page ?? "", item.title || "", item.text || ""]);
if (pageValues.length) pdfPages.getRange("A6").write(pageValues);
const lastPageRow = Math.max(6, 5 + pageValues.length);
body(pdfPages.getRange(`A6:C${lastPageRow}`));
pdfPages.getRange(`A6:A${lastPageRow}`).format.horizontalAlignment = "center";
pdfPages.getRange(`A6:C${lastPageRow}`).format.rowHeight = 90;
pdfPages.getRange("A:A").format.columnWidth = 11;
pdfPages.getRange("B:B").format.columnWidth = 38;
pdfPages.getRange("C:C").format.columnWidth = 110;
pdfPages.freezePanes.freezeRows(5);

title(attempts, "A2:L2", "Search and retry evidence");
attempts.getRange("A3:L3").format.borders = { bottom: { style: "thin", color: blue } };
attempts.getRange("A5:L5").values = [[
  "Attempt", "Query", "Outcome", "Rank", "Candidate title", "Candidate URL",
  "HTTP", "Company", "Year", "Report", "Score", "Decision / reason",
]];
header(attempts.getRange("A5:L5"));
const attemptRows = [];
for (const attempt of manifest.search_attempts || []) {
  if (!attempt.candidates?.length) {
    attemptRows.push([attempt.attempt, attempt.query, attempt.outcome, "", "", "", "", "No", "No", "No", 0, attempt.error || "No candidates"]);
  }
  for (const candidate of attempt.candidates || []) {
    attemptRows.push([
      attempt.attempt, attempt.query, attempt.outcome, candidate.rank, candidate.title, candidate.url,
      candidate.http_status ?? "", candidate.company_match ? "Yes" : "No", candidate.year_match ? "Yes" : "No",
      candidate.report_match ? "Yes" : "No", candidate.score ?? 0,
      candidate.accepted ? "Accepted" : (candidate.reasons || []).join("; "),
    ]);
  }
}
if (attemptRows.length) attempts.getRange("A6").write(attemptRows);
const lastAttemptRow = Math.max(6, 5 + attemptRows.length);
body(attempts.getRange(`A6:L${lastAttemptRow}`));
attempts.getRange(`A6:A${lastAttemptRow}`).format.horizontalAlignment = "center";
attempts.getRange(`D6:D${lastAttemptRow}`).format.horizontalAlignment = "center";
attempts.getRange(`G6:K${lastAttemptRow}`).format.horizontalAlignment = "center";
attempts.getRange("A:A").format.columnWidth = 9;
attempts.getRange("B:B").format.columnWidth = 38;
attempts.getRange("C:C").format.columnWidth = 28;
attempts.getRange("D:D").format.columnWidth = 8;
attempts.getRange("E:E").format.columnWidth = 34;
attempts.getRange("F:F").format.columnWidth = 44;
attempts.getRange("G:K").format.columnWidth = 11;
attempts.getRange("L:L").format.columnWidth = 38;
attempts.freezePanes.freezeRows(5);

title(events, "A2:D2", "Run audit history");
events.getRange("A3:D3").format.borders = { bottom: { style: "thin", color: blue } };
events.getRange("A5:D5").values = [["Timestamp", "Event", "State or URL", "Details"]];
header(events.getRange("A5:D5"));
const eventRows = (audit.events || []).map((item) => [
  item.occurred_at, item.event_type, item.detail.state || item.detail.url || "", JSON.stringify(item.detail),
]);
if (eventRows.length) events.getRange("A6").write(eventRows);
const lastEventRow = Math.max(6, 5 + eventRows.length);
body(events.getRange(`A6:D${lastEventRow}`));
events.getRange(`A6:A${lastEventRow}`).format.numberFormat = "yyyy-mm-dd hh:mm:ss";
events.getRange("A:A").format.columnWidth = 25;
events.getRange("B:B").format.columnWidth = 25;
events.getRange("C:C").format.columnWidth = 30;
events.getRange("D:D").format.columnWidth = 75;
events.freezePanes.freezeRows(5);

workbook.recalculate();
for (const [sheet, range, expected] of [
  ["Overview", `A2:E${Math.min(noteRow + 1, 30)}`, "Annual report — organized results"],
  ["Financial statements", `A2:H${Math.min(lastFinancialRow, 20)}`, "Structured financial statements"],
  ["PDF content", `A2:C${Math.min(lastPageRow, 10)}`, "Full PDF content — page by page"],
  ["Search attempts", `A2:L${Math.min(lastAttemptRow, 20)}`, "Search and retry evidence"],
  ["Audit", `A2:D${Math.min(lastEventRow, 20)}`, "Run audit history"],
]) {
  const inspect = await workbook.inspect({ kind: "table", range: `${sheet}!${range}`, include: "values,formulas", tableMaxRows: 20, tableMaxCols: 12, maxChars: 12000 });
  if (!inspect.ndjson.includes(expected)) throw new Error(`workbook verification failed for ${sheet}`);
}
const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
