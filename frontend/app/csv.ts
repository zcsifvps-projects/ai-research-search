/**
 * CSV export for the results table. Values that a spreadsheet would run as a formula
 * (starting with =, +, -, @, tab or carriage return) are prefixed with an apostrophe.
 */

export function csvCell(value: unknown): string {
  let text = value === null || value === undefined ? "" : String(value);
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  if (/[",\r\n]/.test(text)) text = `"${text.replace(/"/g, '""')}"`;
  return text;
}

export function buildCsv(header: string[], rows: unknown[][]): string {
  // The byte-order mark makes Excel open the file as UTF-8 (accents, Greek letters).
  return "\uFEFF" + [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n");
}

export function downloadCsv(filenameBase: string, csv: string) {
  const slug =
    filenameBase
      .normalize("NFD")
      .replace(/[^A-Za-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 60)
      .toLowerCase() || "papers";
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slug}-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
