/**
 * Reference formatting for papers: APA 7, MLA 9, Chicago (author-date, 17th ed.), Harvard (Cite Them Right) and
 * Vancouver, plus RIS export (imports into Zotero, Mendeley and EndNote).
 *
 * Search results carry title, authors, year, journal (description) and DOI. Volume, issue and pages are not known,
 * so they are left out rather than guessed. Titles are kept as the source wrote them.
 */

import { bareDoi, buildBibtex, doiUrl, type PdfPaper } from "./bibtex";

export type CitationStyle = "apa" | "mla" | "chicago" | "harvard" | "vancouver";

export const CITATION_STYLES: { id: CitationStyle; label: string }[] = [
  { id: "apa", label: "APA 7" },
  { id: "mla", label: "MLA 9" },
  { id: "chicago", label: "Chicago" },
  { id: "harvard", label: "Harvard" },
  { id: "vancouver", label: "Vancouver" },
];

/** A piece of a formatted reference; italic pieces are journal names. */
export type Segment = { text: string; italic?: boolean };

export type CitablePaper = Pick<PdfPaper, "title" | "authors" | "description" | "publication_year" | "doi" | "abstract"> & {
  pdf_url?: string | null;
};

type Name = { family: string; given: string; organisation: boolean };

const PARTICLES = new Set(["van", "von", "der", "den", "de", "da", "das", "do", "dos", "del", "della", "di", "du", "la", "le", "bin", "al", "ter"]);
const ORGANISATION = /\b(group|consortium|collaboration|organi[sz]ation|ministry|council|committee|institute|association|society|network|team|university|department|agency|initiative)\b/i;

function clean(value: string | null | undefined): string {
  return (value || "").replace(/\s+/g, " ").trim();
}

/** "Ann Marie Banda", "Banda, Ann Marie", "Banda, A. M." and "Ludwig van Beethoven" all become family + given. */
export function parseName(raw: string): Name {
  const name = clean(raw).replace(/\.$/, "");
  if (!name) return { family: "", given: "", organisation: false };
  if (ORGANISATION.test(name)) return { family: name, given: "", organisation: true };
  if (name.includes(",")) {
    const [family, ...rest] = name.split(",");
    return { family: clean(family), given: clean(rest.join(" ")), organisation: false };
  }
  const parts = name.split(" ");
  if (parts.length === 1) return { family: parts[0], given: "", organisation: false };
  let cut = parts.length - 1;
  while (cut > 1 && PARTICLES.has(parts[cut - 1].toLowerCase())) cut -= 1;
  return { family: parts.slice(cut).join(" "), given: parts.slice(0, cut).join(" "), organisation: false };
}

/** "Ann Marie" -> ["A", "M"]; "Jean-Paul" -> ["J-P"]; "A. M." -> ["A", "M"]. */
function initialList(given: string): string[] {
  return given
    .split(/[\s.]+/)
    .filter(Boolean)
    .map((word) =>
      word
        .split("-")
        .filter(Boolean)
        .map((piece) => piece.charAt(0).toUpperCase())
        .join("-"),
    );
}

const dotted = (given: string, joiner = " ") => initialList(given).map((i) => i.split("-").join(".-") + ".").join(joiner);
const packed = (given: string) => initialList(given).join("").replace(/-/g, "");

function names(paper: CitablePaper): Name[] {
  return (paper.authors || []).map(parseName).filter((n) => n.family);
}

function sentenceEnd(text: string): string {
  return /[.?!]$/.test(text) ? text : `${text}.`;
}

function joinList(items: string[], lastJoiner: string, comma = ", "): string {
  if (items.length <= 1) return items.join("");
  if (items.length === 2) return `${items[0]}${lastJoiner}${items[1]}`;
  return `${items.slice(0, -1).join(comma)},${lastJoiner}${items[items.length - 1]}`;
}

function doiLink(paper: CitablePaper): string {
  return paper.doi ? doiUrl(paper.doi) : "";
}

// ------------------------------------------------------------------ styles

function apa(paper: CitablePaper): Segment[] {
  const list = names(paper).map((n) => (n.organisation || !n.given ? n.family : `${n.family}, ${dotted(n.given)}`));
  let authors: string;
  if (list.length === 0) authors = "";
  else if (list.length === 1) authors = list[0];
  else if (list.length <= 20) authors = `${list.slice(0, -1).join(", ")}, & ${list[list.length - 1]}`;
  else authors = `${list.slice(0, 19).join(", ")}, . . . ${list[list.length - 1]}`;
  const year = `(${paper.publication_year || "n.d."}).`;
  const title = sentenceEnd(clean(paper.title) || "Untitled");
  const head = authors ? `${sentenceEnd(authors)} ${year} ${title}` : `${title} ${year}`;
  const out: Segment[] = [{ text: `${head} ` }];
  if (clean(paper.description)) out.push({ text: clean(paper.description), italic: true }, { text: "." });
  if (doiLink(paper)) out.push({ text: ` ${doiLink(paper)}` });
  return out;
}

function mla(paper: CitablePaper): Segment[] {
  const list = names(paper);
  const inverted = (n: Name) => (n.given && !n.organisation ? `${n.family}, ${n.given}` : n.family);
  const natural = (n: Name) => (n.given && !n.organisation ? `${n.given} ${n.family}` : n.family);
  let authors = "";
  if (list.length === 1) authors = inverted(list[0]);
  else if (list.length === 2) authors = `${inverted(list[0])}, and ${natural(list[1])}`;
  else if (list.length > 2) authors = `${inverted(list[0])}, et al`;
  const out: Segment[] = [];
  if (authors) out.push({ text: `${sentenceEnd(authors)} ` });
  out.push({ text: `“${sentenceEnd(clean(paper.title) || "Untitled")}” ` });
  // Container, year and DOI, separated by commas, ending with a full stop.
  const pieces: Segment[] = [];
  if (clean(paper.description)) pieces.push({ text: clean(paper.description), italic: true });
  if (paper.publication_year) pieces.push({ text: String(paper.publication_year) });
  if (doiLink(paper)) pieces.push({ text: doiLink(paper) });
  pieces.forEach((piece, i) => {
    if (i > 0) out.push({ text: ", " });
    out.push(piece);
  });
  if (pieces.length) out.push({ text: "." });
  return out;
}

function chicago(paper: CitablePaper): Segment[] {
  const list = names(paper);
  const shown = list.length > 10 ? list.slice(0, 7) : list;
  const formatted = shown.map((n, i) =>
    n.organisation || !n.given ? n.family : i === 0 ? `${n.family}, ${n.given}` : `${n.given} ${n.family}`,
  );
  let authors = joinList(formatted, " and ");
  if (list.length > 10) authors = `${formatted.join(", ")}, et al`;
  const out: Segment[] = [];
  if (authors) out.push({ text: `${sentenceEnd(authors)} ` });
  out.push({ text: `${paper.publication_year ? `${paper.publication_year}.` : "n.d."} “${sentenceEnd(clean(paper.title) || "Untitled")}” ` });
  if (clean(paper.description)) out.push({ text: clean(paper.description), italic: true }, { text: "." });
  if (doiLink(paper)) out.push({ text: ` ${doiLink(paper)}.` });
  return out;
}

function harvard(paper: CitablePaper): Segment[] {
  const list = names(paper).map((n) => (n.organisation || !n.given ? n.family : `${n.family}, ${dotted(n.given, "")}`));
  let authors = "";
  if (list.length === 1) authors = list[0];
  else if (list.length === 2 || list.length === 3) authors = `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}`;
  else if (list.length > 3) authors = `${list[0]} et al.`;
  const out: Segment[] = [];
  out.push({ text: `${authors ? `${authors} ` : ""}(${paper.publication_year || "no date"}) ‘${clean(paper.title) || "Untitled"}’` });
  if (clean(paper.description)) out.push({ text: ", " }, { text: clean(paper.description), italic: true });
  out.push({ text: "." });
  if (doiLink(paper)) out.push({ text: ` Available at: ${doiLink(paper)}.` });
  return out;
}

function vancouver(paper: CitablePaper): Segment[] {
  const list = names(paper).map((n) => (n.organisation || !n.given ? n.family : `${n.family} ${packed(n.given)}`));
  let authors = list.slice(0, 6).join(", ");
  if (list.length > 6) authors += ", et al";
  const parts = [authors ? sentenceEnd(authors) : "", sentenceEnd(clean(paper.title) || "Untitled"), clean(paper.description) ? sentenceEnd(clean(paper.description)) : "", paper.publication_year ? `${paper.publication_year}.` : ""].filter(Boolean);
  const out: Segment[] = [{ text: parts.join(" ") }];
  if (paper.doi) out.push({ text: ` doi:${bareDoi(paper.doi)}` });
  return out;
}

const FORMATTERS: Record<CitationStyle, (p: CitablePaper) => Segment[]> = { apa, mla, chicago, harvard, vancouver };

export function formatCitation(paper: CitablePaper, style: CitationStyle): Segment[] {
  return FORMATTERS[style](paper);
}

export function citationText(paper: CitablePaper, style: CitationStyle): string {
  return formatCitation(paper, style).map((s) => s.text).join("").replace(/\s+/g, " ").trim();
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** Same reference as HTML (journal in italics), for pasting into Word or Google Docs. */
export function citationHtml(paper: CitablePaper, style: CitationStyle): string {
  return formatCitation(paper, style)
    .map((s) => (s.italic ? `<i>${escapeHtml(s.text)}</i>` : escapeHtml(s.text)))
    .join("");
}

// ------------------------------------------------------------------ RIS and BibTeX files

function risValue(value: string): string {
  return clean(value).replace(/[\r\n]+/g, " ");
}

/** One RIS record (journal article). RIS is plain text, "TAG  - value", CRLF line endings. */
export function buildRis(paper: CitablePaper): string {
  const lines: string[] = ["TY  - JOUR", `TI  - ${risValue(paper.title) || "Untitled"}`];
  for (const n of names(paper)) lines.push(`AU  - ${risValue(n.given ? `${n.family}, ${n.given}` : n.family)}`);
  if (paper.publication_year) lines.push(`PY  - ${paper.publication_year}`);
  if (clean(paper.description)) lines.push(`JO  - ${risValue(paper.description)}`);
  if (paper.doi) {
    lines.push(`DO  - ${bareDoi(paper.doi)}`);
    lines.push(`UR  - ${doiUrl(paper.doi)}`);
  } else if (paper.pdf_url) {
    lines.push(`UR  - ${paper.pdf_url}`);
  }
  if (clean(paper.abstract)) lines.push(`AB  - ${risValue(paper.abstract || "").slice(0, 5000)}`);
  lines.push("ER  - ");
  return lines.join("\r\n");
}

export function buildRisFile(papers: CitablePaper[]): string {
  return papers.map(buildRis).join("\r\n\r\n") + "\r\n";
}

export function buildBibtexFile(papers: CitablePaper[]): string {
  const seen = new Map<string, number>();
  return (
    papers
      .map((p) => {
        // Keys must be unique inside one file: fisher2015drought, fisher2015droughta, ...
        const entry = buildBibtex({ ...p, abstract: p.abstract ?? null });
        const key = entry.slice(entry.indexOf("{") + 1, entry.indexOf(","));
        const count = seen.get(key) || 0;
        seen.set(key, count + 1);
        return count === 0 ? entry : entry.replace(`{${key},`, `{${key}${String.fromCharCode(96 + count)},`);
      })
      .join("\n\n") + "\n"
  );
}

export function downloadTextFile(name: string, content: string, mime: string) {
  const blob = new Blob([content], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function fileSlug(base: string, fallback: string): string {
  const slug = (base || "")
    .normalize("NFD")
    .replace(/[^A-Za-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 50)
    .toLowerCase();
  return `${slug || fallback}-${new Date().toISOString().slice(0, 10)}`;
}
