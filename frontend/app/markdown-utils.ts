/** Shared by the on-screen renderer (markdown.tsx) and the PDF export (pdf.ts). */

/**
 * Small clean-ups so common AI formatting habits still parse as markdown:
 * "•" bullets become "-" bullets, Windows line endings are normalised, and a table that starts directly under
 * a paragraph gets the blank line markdown needs before it.
 */
export function normalizeMarkdown(text: string): string {
  const lines = (text || "").replace(/\r\n?/g, "\n").split("\n");
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].replace(/^(\s*)[•●▪◦]\s+/, "$1- ");
    const isTableRow = /^\s*\|.*\|\s*$/.test(line);
    const prev = out.length ? out[out.length - 1] : "";
    if (isTableRow && prev.trim() !== "" && !/^\s*\|.*\|\s*$/.test(prev)) out.push("");
    out.push(line);
  }
  return out.join("\n").trim();
}
