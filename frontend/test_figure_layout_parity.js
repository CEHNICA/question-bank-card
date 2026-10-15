// The Word export reproduces the print preview's figure layout in Python
// (library_export.py: _figure_paper_size / _figure_row_lines). The preview
// does NOT get that geometry from CSS — exam-layout.js writes every picture's
// width inline once the image has decoded — so read the script that actually
// decides it, read the exporter, and fail when the two drift apart.
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const root = path.join(__dirname, "..");
const styles = fs.readFileSync(path.join(root, "frontend/styles.css"), "utf8");
const library = fs.readFileSync(path.join(root, "frontend/library.css"), "utf8");
const examLayout = fs.readFileSync(path.join(root, "frontend/exam-layout.js"), "utf8");
const exporter = fs.readFileSync(path.join(root, "backend/core/library_export.py"), "utf8");

// --- how the row wraps --------------------------------------------------------
const figuresRow = styles.match(/\.qb-figures\s*\{([^}]*)\}/);
assert.ok(figuresRow, ".qb-figures is the container that wraps stem figures");
const row = figuresRow[1];
assert.match(row, /display:\s*flex/, "figures wrap by flex, not by an explicit grid");
assert.match(row, /flex-wrap:\s*wrap/, "figures wrap onto new rows");
assert.match(row, /justify-content:\s*center/, ".qb-figures centres the row, so Word must too");

const gap = row.match(/gap:\s*(\d+(?:\.\d+)?)px/);
assert.ok(gap, ".qb-figures declares its own gap");
const gapPx = Number(gap[1]);

// --- how large a picture is ----------------------------------------------------
// exam-layout.js, inside paginate():
//     const imageScale = Math.min(chosenWidth > 0 ? ... : 96 / 150,
//                                  BODY_WIDTH / image.naturalWidth,
//                                  210 * MM / image.naturalHeight);
//     image.style.width = `${image.naturalWidth * imageScale}px`;
const mmConstant = examLayout.match(/const MM\s*=\s*(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)/);
assert.ok(mmConstant, "exam-layout.js defines how many CSS pixels make a millimetre");
const mmPerPixel = Number(mmConstant[2]) / Number(mmConstant[1]);

const bodyWidth = examLayout.match(/BODY_WIDTH\s*=\s*(\d+(?:\.\d+)?)\s*\*\s*MM/);
assert.ok(bodyWidth, "exam-layout.js names the printed text width");
const textWidthMm = Number(bodyWidth[1]);

const imageScale = examLayout.match(/const imageScale = [^;]*;/);
assert.ok(imageScale, "exam-layout.js is what sizes a picture, not the stylesheet");
const scaleLine = imageScale[0];

const naturalDpi = (scaleLine.match(/:\s*(\d+(?:\.\d+)?)\s*\/\s*(\d+(?:\.\d+)?)\s*,/) || [])[2];
assert.ok(Number(naturalDpi) > 0, "the preview states the natural figure size as one ratio");

const pageHeightMm = (scaleLine.match(/,\s*(\d+(?:\.\d+)?)\s*\*\s*MM\s*\/\s*image\.naturalHeight/) || [])[1];
assert.ok(Number(pageHeightMm) > 0, "the preview caps a figure's height at one A4 page");

// All three terms of that Math.min have to be the ones the exporter mirrors; a
// new cap added here must be mirrored there rather than quietly ignored.
assert.match(scaleLine, /BODY_WIDTH\s*\/\s*image\.naturalWidth/,
  "a figure is capped at the text area");
assert.match(scaleLine, /Math\.min\(\s*chosenWidth/,
  "answer-diagram widths come from the record, which Word reads separately");


// The stylesheet caps on screen, but library.css overrides them inside the print
// flow. Reading those caps is what made the download bigger than the preview, so
// pin the override down: if it is ever removed, this test must be revisited, not
// silently kept working.
const screenFigure = styles.match(/\.qb-figure\s+img\s*\{([^}]*)\}/);
assert.ok(screenFigure, ".qb-figure img is capped on screen");
assert.match(screenFigure[1], /max-width:\s*min\(100%,\s*320px\)/,
  "the on-screen width cap is still there");
const printedFigure = library.match(/\.print-flow\s+\.qb-figure\s+img[^{]*\{([^}]*)\}/);
assert.ok(printedFigure, "print narrows .qb-figure img to the text area");
assert.match(printedFigure[1], /max-width:\s*100%/);
assert.match(printedFigure[1], /max-height:\s*none/,
  "the 320px/220px screen caps must stay overridden in the print flow");

// .print-flow is the printed text area; it has to agree with BODY_WIDTH, or the
// row would wrap in a different place on paper than on screen.
const printFlow = library.match(/\.print-flow\s*\{([^}]*)\}/);
assert.ok(printFlow, ".print-flow is the printed text area");
const cssWidthMm = Number((printFlow[1].match(/width:\s*(\d+(?:\.\d+)?)mm/) || [])[1]);
assert.ok(cssWidthMm > 0, ".print-flow has an explicit width in mm");
assert.equal(cssWidthMm, textWidthMm,
  `.print-flow is ${cssWidthMm}mm wide but exam-layout.js paginates at ${textWidthMm}mm`);

// --- the exporter must use exactly those numbers ------------------------------
const gapConstant = exporter.match(/PREVIEW_FIGURE_GAP_PX\s*=\s*(\d+(?:\.\d+)?)/);
assert.ok(gapConstant, "library_export.py names the preview gap it assumes");
assert.equal(Number(gapConstant[1]), gapPx,
  `Word wraps with a ${gapConstant[1]}px gap but .qb-figures uses ${gapPx}px`);

const dpiConstant = exporter.match(/PREVIEW_FIGURE_DPI\s*=\s*(\d+(?:\.\d+)?)/);
assert.ok(dpiConstant, "library_export.py names the figure dpi it assumes");
assert.equal(Number(dpiConstant[1]), naturalDpi,
  `Word places figures at ${dpiConstant[1]} dpi but the preview uses ${naturalDpi} dpi`);

const widthConstant = exporter.match(/PREVIEW_TEXT_WIDTH_MM\s*=\s*(\d+(?:\.\d+)?)/);
assert.ok(widthConstant, "library_export.py names the text area it wraps to");
assert.equal(Number(widthConstant[1]), textWidthMm,
  `Word wraps at ${widthConstant[1]}mm but the preview paginates at ${textWidthMm}mm`);

const pageConstant = exporter.match(/PREVIEW_PAGE_HEIGHT_MM\s*=\s*(\d+(?:\.\d+)?)/);
assert.ok(pageConstant, "library_export.py names the height a figure is capped at");
assert.equal(Number(pageConstant[1]), pageHeightMm,
  `Word caps figures at ${pageConstant[1]}mm but the preview caps at ${pageHeightMm}mm`);

const dxf = exporter.match(/text_width_mm=(\d+(?:\.\d+)?)/g) || [];
assert.ok(dxf.includes(`text_width_mm=${textWidthMm}`),
  `the exporter must measure against the ${textWidthMm}mm text area`);

const pxPerInch = exporter.match(/PREVIEW_PX_PER_INCH\s*=\s*(\d+)/);
assert.ok(pxPerInch, "one CSS pixel is 1/96 inch");
assert.equal(Number(pxPerInch[1]), 96, "CSS px to inch is fixed by the browser");
assert.equal(mmPerPixel * 96, 25.4, "96 CSS px must be 25.4 mm");

// The exporter must not have grown a second opinion about figure size.
assert.doesNotMatch(exporter, /PREVIEW_FIGURE_MAX_W_PX|PREVIEW_FIGURE_MAX_H_PX|PREVIEW_BODY_FONT_PX/,
  "the 320px/220px/17px screen numbers are overridden in the print flow and must not come back");

// --- option columns -----------------------------------------------------------
// styles.css still carries "@container qpaper (max-width: 680px) { .qb-options.cols-4
// -> two columns }", and the exporter used to model it verbatim. That rule can never
// take effect in a browser: exam-layout.js fitOptions writes an inline
// grid-template-columns on every option list, and an inline style outranks a
// container query. Measured 2026-11-04 on a real 23-question paper: the teacher picks
// four-across, the PDF prints four-across in 4 pages, Word printed 2x2 in 5 pages —
// and it blamed "选项较宽" on options as short as "10i".
//
// So parity here is NOT "Word copies the same px threshold". It is "Word does not
// apply a reduction the renderer cannot apply". The width decision stays in
// _option_columns, which measures the real content.
assert.doesNotMatch(exporter, /PREVIEW_OPTION_FOUR_COLUMN_MAX_PX|def _preview_option_columns\(/,
  "Word must not model a CSS rule that fitOptions shadows with an inline style");
assert.doesNotMatch(exporter, /_preview_option_columns\(/,
  "no column decision may route through the removed reduction");

// --- the wrapping itself, checked against the preview's own formula ------------
// Same arithmetic as _figure_paper_size + _figure_row_lines, written here
// independently so a change on one side shows up as a failure. It returns the
// line lengths, not a single count, because that is what the export now uses:
// the preview centres every line on its own, so Word writes one table per line.
function paperMm(pxWidth) {
  return Math.min(pxWidth * 25.4 / naturalDpi, textWidthMm);
}
function wrap(pxWidths) {
  const gapMm = gapPx * mmPerPixel;
  const widths = pxWidths.filter((value) => value > 0).map(paperMm);
  if (!widths.length) return [1];
  const lines = [];
  let used = widths[0], count = 1;
  for (const width of widths.slice(1)) {
    if (used + gapMm + width <= textWidthMm + 0.01) { used += gapMm + width; count += 1; }
    else { lines.push(count); used = width; count = 1; }
  }
  lines.push(count);
  return lines;
}

const four = [240, 240, 240, 240];
assert.deepEqual(wrap(four), [4], "four 240px diagrams fill one row at 150 dpi");
assert.deepEqual(wrap([120, 120, 120, 120]), [4], "four 120px diagrams fill one row");
assert.deepEqual(wrap([400, 400, 400, 400]), [2, 2], "four 400px diagrams go two per row");
assert.deepEqual(wrap([300, 300, 300, 300, 300]), [3, 2], "five 300px diagrams go three then two");
assert.deepEqual(wrap([240]), [1], "one diagram is a single line");
assert.deepEqual(wrap([600, 600]), [1, 1], "two 600px strips each take a whole row");
// The reported case, 初三「特殊的平行四边形」第 23 题.
assert.deepEqual(wrap([279, 231, 210]), [3], "the three scanned diagrams stay on one row");
// The wrapping case that was still off centre: a set that breaks 2+1. The lone
// second line has to be its own centred table, not the first column of a wider
// one, or that picture sits hard against the left of the page. These are the
// real picture widths of 必修第一册第 4 题, which wraps exactly that way.
assert.deepEqual(wrap([415, 419, 419]), [2, 1],
  "必修第一册第 4 题 wraps 2+1, so the second line holds one diagram on its own");
assert.deepEqual(wrap([340, 347, 336, 326]), [2, 2],
  "必修第一册第 1 题 wraps 2+2, and both lines are the same length");
// The row must come out exactly as wide as the browser draws it, not as wide as
// the text area: 178.56 + 147.84 + 134.4 px plus two 14px gaps = 488.8 px.
const rowMm = [279, 231, 210].reduce((sum, value) => sum + paperMm(value), 0)
  + 2 * gapPx * mmPerPixel;
assert.ok(Math.abs(rowMm - 488.8 * mmPerPixel) < 0.2,
  `the measured preview row is 129.3mm, this computes ${rowMm.toFixed(1)}mm`);

console.log(`figure layout parity: ${naturalDpi} dpi figures, ${gapPx}px gap, `
  + `${textWidthMm}mm text area; four 240px diagrams -> ${wrap(four).join('+')} per row, `
  + `the three scanned diagrams -> ${wrap([279, 231, 210]).join('+')} per row `
  + `(${rowMm.toFixed(1)}mm, the same row the preview draws), `
  + `必修第一册第 4 题 -> ${wrap([415, 419, 419]).join('+')}, each line centred on its own`);
