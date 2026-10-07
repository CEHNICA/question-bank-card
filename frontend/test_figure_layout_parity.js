// The Word export reproduces the preview's figure wrapping in Python
// (library_export.py: _figure_columns). That only stays true while the CSS it
// was measured against is unchanged. Read the real stylesheets and the real
// exporter, and fail when the two drift apart.
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const root = path.join(__dirname, "..");
const styles = fs.readFileSync(path.join(root, "frontend/styles.css"), "utf8");
const library = fs.readFileSync(path.join(root, "frontend/library.css"), "utf8");
const exporter = fs.readFileSync(path.join(root, "backend/core/library_export.py"), "utf8");

// --- the preview's geometry, read back out of the stylesheets -----------------
const figuresRow = styles.match(/\.qb-figures\s*\{([^}]*)\}/);
assert.ok(figuresRow, ".qb-figures is the container that wraps stem figures");
const row = figuresRow[1];
assert.match(row, /display:\s*flex/, "figures wrap by flex, not by an explicit grid");
assert.match(row, /flex-wrap:\s*wrap/, "figures wrap onto new rows");

const gap = row.match(/gap:\s*(\d+(?:\.\d+)?)px/);
assert.ok(gap, ".qb-figures declares its own gap");
const gapPx = Number(gap[1]);

const textWidth = library.match(/\.print-flow\s*\{([^}]*)\}/);
assert.ok(textWidth, ".print-flow is the printed text area");
const widthMm = Number((textWidth[1].match(/width:\s*(\d+(?:\.\d+)?)mm/) || [])[1]);
assert.ok(widthMm > 0, ".print-flow has an explicit width in mm");

// In print an image is capped at the text area and has no height cap, so its
// rendered width is its pixel width unless that is wider than the text area.
const printedFigure = library.match(/\.print-flow\s+\.qb-figure\s+img[^{]*\{([^}]*)\}/);
assert.ok(printedFigure, "print narrows .qb-figure img to the text area");
assert.match(printedFigure[1], /max-width:\s*100%/);
assert.match(printedFigure[1], /max-height:\s*none/);

// Screen caps at 320px, which is why the preview and the print differ for very
// wide diagrams. The exporter models the print geometry; make that deliberate.
assert.match(styles, /\.qb-figure\s+img\s*\{[^}]*max-width:\s*min\(100%,\s*320px\)/,
  "the on-screen 320px cap is what makes wide diagrams differ on screen");

// --- the exporter must use exactly those numbers ------------------------------
const gapConstant = exporter.match(/PREVIEW_FIGURE_GAP_PX\s*=\s*(\d+(?:\.\d+)?)/);
assert.ok(gapConstant, "library_export.py names the preview gap it assumes");
assert.equal(Number(gapConstant[1]), gapPx,
  `Word wraps with a ${gapConstant[1]}px gap but .qb-figures uses ${gapPx}px`);

const dxf = exporter.match(/text_width_mm=(\d+(?:\.\d+)?)/g) || [];
assert.ok(dxf.includes(`text_width_mm=${widthMm}`),
  `the exporter must measure against the ${widthMm}mm .print-flow text area`);

const dpiConstant = exporter.match(/PREVIEW_PX_PER_INCH\s*=\s*(\d+)/);
assert.ok(dpiConstant, "one CSS pixel is 1/96 inch");
assert.equal(Number(dpiConstant[1]), 96, "CSS px to inch is fixed by the browser");

// --- option columns -----------------------------------------------------------
// The container is .qb-question ("container: qpaper / inline-size"), so on a
// printed page it is the 178mm text area: four option columns never survive
// there. Word has to apply the same reduction or every four-option question
// downloads as 1x4 while the preview shows 2x2.
const query = styles.match(/@container\s+qpaper\s*\(max-width:\s*(\d+)px\)\s*\{\s*\.qb-options\.cols-4/);
assert.ok(query, "styles.css reduces four option columns on a narrow question box");
const fourColumnMaxPx = Number(query[1]);

const optionMax = exporter.match(/PREVIEW_OPTION_FOUR_COLUMN_MAX_PX\s*=\s*(\d+)/);
assert.ok(optionMax, "library_export.py names the option-column threshold it assumes");
assert.equal(Number(optionMax[1]), fourColumnMaxPx,
  `Word drops four option columns at ${optionMax[1]}px but the CSS does it at ${fourColumnMaxPx}px`);
assert.ok(widthMm / 25.4 * 96 <= fourColumnMaxPx,
  `the printed text area is ${(widthMm / 25.4 * 96).toFixed(1)}px, so four options really do become two`);

assert.match(exporter, /def _preview_option_columns\(/,
  "the reduction lives in one place both column decisions go through");

// --- the wrapping itself, checked against what Chrome was measured doing ------
// Cases come from measuring the real renderer at a 178mm container.
function wrap(sizes, mm = widthMm, gapPxValue = gapPx) {
  const container = mm / 25.4 * 96;
  const widths = sizes.filter((value) => value > 0).map((value) => Math.min(value, container));
  if (widths.length < 2) return 1;
  let best = 1, used = widths[0], count = 1;
  for (const value of widths.slice(1)) {
    if (used + gapPxValue + value <= container + 0.5) { used += gapPxValue + value; count += 1; }
    else { best = Math.max(best, count); used = value; count = 1; }
  }
  return Math.max(best, count);
}
const four = [240, 240, 240, 240];
assert.equal(wrap(four), 2, "four 240px diagrams fit two per row at 178mm");
assert.equal(wrap([120, 120, 120, 120]), 4, "four 120px diagrams fit on one row");
assert.equal(wrap([400, 400, 400, 400]), 1, "a 400px diagram takes a whole row");
assert.equal(wrap([300, 300, 300, 300, 300]), 2, "five 300px diagrams wrap two per row");
assert.equal(wrap([240]), 1, "one diagram is never a grid");

console.log(`figure layout parity: ${gapPx}px gap, ${widthMm}mm text area, `
  + `four 240px diagrams -> ${wrap(four)} per row (matches the measured preview); `
  + `four options -> 2 columns on the printed ${(widthMm / 25.4 * 96).toFixed(0)}px page`);