/**
 * PDF exports: a research-synthesis report for an AI answer, and a one-paper brief.
 *
 * The AI writes markdown, so it is parsed into a syntax tree (the same GitHub-flavoured markdown rules the
 * on-screen renderer uses) and drawn element by element: headings, paragraphs with bold/italic, bullet and
 * numbered lists (nested), real tables, quotes, code, and dividers. No markdown symbols reach the page.
 * Loaded on demand (dynamic import) so the parser and table plugin are not in the main page bundle.
 */

import { jsPDF } from "jspdf";
import { autoTable } from "jspdf-autotable";
import type { Blockquote, Code, Heading, List, ListItem, Nodes, PhrasingContent, RootContent, Table } from "mdast";
import { fromMarkdown } from "mdast-util-from-markdown";
import { gfmFromMarkdown } from "mdast-util-gfm";
import { gfm } from "micromark-extension-gfm";
import { buildBibtex, type PdfPaper } from "./bibtex";
import { normalizeMarkdown } from "./markdown-utils";



export type { PdfPaper };
export type PdfCitation = { paper_id: number; title: string };

type Rgb = [number, number, number];
const ACCENT: Rgb = [124, 42, 42];
const INK: Rgb = [34, 29, 24];
const MUTED: Rgb = [110, 100, 89];
const RULE: Rgb = [228, 220, 203];
const TINT: Rgb = [247, 241, 233];
const ZEBRA: Rgb = [251, 248, 243];

const PAGE_W = 210;
const PAGE_H = 297;
const MARGIN_X = 20;
const TOP = 24; // content starts below the running header
const BOTTOM = PAGE_H - 20; // content stops above the footer
const CONTENT_W = PAGE_W - MARGIN_X * 2;
const PT = 0.3528; // millimetres per point

type Style = "normal" | "bold" | "italic" | "bolditalic" | "code";
type Run = { text: string; style: Style; color?: Rgb };

// ------------------------------------------------------------------
// Text clean-up: the built-in PDF fonts only cover Western European characters.
// ------------------------------------------------------------------

const CHAR_MAP: Record<string, string> = {
  "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
  "“": '"', "”": '"', "„": '"', "″": '"',
  "–": "-", "—": " - ", "―": " - ", "−": "-", "‐": "-", "‑": "-",
  "…": "...", "•": "-", "●": "-", "▪": "-", "◦": "-",
  " ": " ", " ": " ", " ": " ", " ": " ", "​": "",
  "≤": "<=", "≥": ">=", "≠": "!=", "≈": "~", "→": "->", "←": "<-", "↔": "<->",
  "↑": "up", "↓": "down", "∞": "infinity", "√": "sqrt",
  "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "Δ": "Delta",
  "ε": "epsilon", "θ": "theta", "λ": "lambda", "μ": "mu", "π": "pi",
  "ρ": "rho", "σ": "sigma", "Σ": "Sigma", "τ": "tau", "φ": "phi",
  "χ": "chi", "ω": "omega", "Ω": "Omega",
  "✓": "yes", "✔": "yes", "✗": "no", "✘": "no",
};

export function pdfSafe(text: string): string {
  let out = "";
  for (const ch of (text || "").normalize("NFC")) {
    if (CHAR_MAP[ch] !== undefined) out += CHAR_MAP[ch];
    else if (ch === "\t") out += "  ";
    else if (ch.codePointAt(0)! <= 0xff) out += ch;
    else if (/\p{Extended_Pictographic}/u.test(ch)) out += "";
    else out += "?";
  }
  return out;
}

// ------------------------------------------------------------------
// Layout engine
// ------------------------------------------------------------------

class Writer {
  doc: jsPDF;
  y = TOP;

  constructor() {
    this.doc = new jsPDF({ unit: "mm", format: "a4", orientation: "portrait" });
    this.doc.setLineHeightFactor(1.15);
  }

  ensure(height: number) {
    if (this.y + height > BOTTOM) {
      this.doc.addPage();
      this.y = TOP;
    }
  }

  font(style: Style, size: number) {
    if (style === "code") this.doc.setFont("courier", "normal");
    else this.doc.setFont("helvetica", style);
    this.doc.setFontSize(size);
  }

  color(rgb: Rgb) {
    this.doc.setTextColor(rgb[0], rgb[1], rgb[2]);
  }

  /** Lays out mixed-style text with word wrap; y is the top of the first line. */
  runs(runs: Run[], opts: { x?: number; width?: number; size?: number; lead?: number; color?: Rgb; first?: (y: number) => void } = {}) {
    const x = opts.x ?? MARGIN_X;
    const width = opts.width ?? CONTENT_W;
    const size = opts.size ?? 10;
    const lead = opts.lead ?? size * PT * 1.45;
    const baseColor = opts.color ?? INK;
    const ascent = size * PT * 0.78;

    type Tok = { text: string; style: Style; color: Rgb; space: boolean; br?: boolean };
    const toks: Tok[] = [];
    for (const run of runs) {
      const color = run.color ?? baseColor;
      const parts = pdfSafe(run.text).split(/(\n|[ \t]+)/);
      for (const part of parts) {
        if (!part) continue;
        if (part === "\n") toks.push({ text: "", style: run.style, color, space: false, br: true });
        else if (/^[ \t]+$/.test(part)) toks.push({ text: " ", style: run.style, color, space: true });
        else toks.push({ text: part, style: run.style, color, space: false });
      }
    }

    let line: Tok[] = [];
    let lineW = 0;
    let firstLine = true;
    const measure = (t: Tok) => {
      this.font(t.style, size);
      return this.doc.getTextWidth(t.text);
    };
    const flush = () => {
      while (line.length && line[line.length - 1].space) line.pop();
      this.ensure(lead);
      if (firstLine && opts.first) opts.first(this.y);
      firstLine = false;
      let cx = x;
      for (const t of line) {
        this.font(t.style, size);
        this.color(t.color);
        if (!t.space) this.doc.text(t.text, cx, this.y + ascent);
        cx += this.doc.getTextWidth(t.text);
      }
      this.y += lead;
      line = [];
      lineW = 0;
    };

    for (const tok of toks) {
      if (tok.br) {
        flush();
        continue;
      }
      if (tok.space && line.length === 0) continue;
      let w = measure(tok);
      if (!tok.space && w > width) {
        // A single word wider than the column: break it into pieces that fit.
        this.font(tok.style, size);
        const pieces: string[] = this.doc.splitTextToSize(tok.text, width);
        pieces.forEach((piece, i) => {
          const pt: Tok = { ...tok, text: piece };
          const pw = measure(pt);
          if (lineW + pw > width && line.length) flush();
          line.push(pt);
          lineW += pw;
          if (i < pieces.length - 1) flush();
        });
        continue;
      }
      if (!tok.space && lineW + w > width && line.length) {
        flush();
        w = measure(tok);
      }
      line.push(tok);
      lineW += w;
    }
    if (line.length || firstLine) flush();
  }

  gap(mm: number) {
    this.y += mm;
  }

  rule(color: Rgb = RULE, width = 0.3) {
    this.ensure(4);
    this.doc.setDrawColor(color[0], color[1], color[2]);
    this.doc.setLineWidth(width);
    this.doc.line(MARGIN_X, this.y + 1.5, PAGE_W - MARGIN_X, this.y + 1.5);
    this.y += 4.5;
  }

  sectionTitle(text: string) {
    this.ensure(16);
    this.gap(2);
    this.runs([{ text, style: "bold", color: ACCENT }], { size: 12.5 });
    this.doc.setDrawColor(ACCENT[0], ACCENT[1], ACCENT[2]);
    this.doc.setLineWidth(0.4);
    this.doc.line(MARGIN_X, this.y, MARGIN_X + 18, this.y);
    this.gap(3.5);
  }

  /** A shaded box of label/value lines (question, dates, metadata). */
  infoBox(rows: [string, string][]) {
    const size = 9;
    const lead = size * PT * 1.45;
    const labelW = 26;
    const valueW = CONTENT_W - labelW - 8;
    this.font("normal", size);
    const lines = rows.map(([, v]) => (this.doc.splitTextToSize(pdfSafe(v), valueW) as string[]).length);
    const height = lines.reduce((a, n) => a + n * lead, 0) + 6;
    this.ensure(Math.min(height, 120));
    const top = this.y;
    this.doc.setFillColor(TINT[0], TINT[1], TINT[2]);
    this.doc.setDrawColor(RULE[0], RULE[1], RULE[2]);
    this.doc.setLineWidth(0.25);
    this.doc.roundedRect(MARGIN_X, top, CONTENT_W, height, 2, 2, "FD");
    this.doc.setFillColor(ACCENT[0], ACCENT[1], ACCENT[2]);
    this.doc.rect(MARGIN_X, top, 1.2, height, "F");
    this.y = top + 3;
    for (const [label, value] of rows) {
      const rowTop = this.y;
      this.runs([{ text: label, style: "bold", color: MUTED }], { x: MARGIN_X + 5, width: labelW, size });
      this.y = rowTop;
      this.runs([{ text: value, style: "normal" }], { x: MARGIN_X + 5 + labelW, width: valueW, size });
    }
    this.y = top + height + 5;
  }

  // ---------------- markdown ----------------

  markdown(text: string) {
    const tree = fromMarkdown(normalizeMarkdown(text), {
      extensions: [gfm()],
      mdastExtensions: [gfmFromMarkdown()],
    });
    for (const node of tree.children) this.block(node, MARGIN_X, CONTENT_W);
  }

  inline(nodes: PhrasingContent[], style: Style = "normal"): Run[] {
    const out: Run[] = [];
    const combine = (a: Style, add: "bold" | "italic"): Style => {
      if (a === "code") return a;
      if (add === "bold") return a === "italic" || a === "bolditalic" ? "bolditalic" : "bold";
      return a === "bold" || a === "bolditalic" ? "bolditalic" : "italic";
    };
    for (const n of nodes) {
      switch (n.type) {
        case "text":
          out.push({ text: n.value, style });
          break;
        case "strong":
          out.push(...this.inline(n.children, combine(style, "bold")));
          break;
        case "emphasis":
          out.push(...this.inline(n.children, combine(style, "italic")));
          break;
        case "delete":
          out.push(...this.inline(n.children, style));
          break;
        case "inlineCode":
          out.push({ text: n.value, style: "code" });
          break;
        case "break":
          out.push({ text: "\n", style });
          break;
        case "link": {
          const label = this.inline(n.children, style);
          out.push(...label.map((r) => ({ ...r, color: ACCENT })));
          const labelText = label.map((r) => r.text).join("");
          if (n.url && /^https?:/i.test(n.url) && labelText.trim() !== n.url) {
            out.push({ text: ` (${n.url})`, style: "normal", color: MUTED });
          }
          break;
        }
        case "image":
          if (n.alt) out.push({ text: `[${n.alt}]`, style: "italic", color: MUTED });
          break;
        case "html":
          out.push({ text: n.value.replace(/<[^>]*>/g, ""), style });
          break;
        case "footnoteReference":
          out.push({ text: `[${n.label || n.identifier}]`, style });
          break;
        default:
          if ("children" in n) out.push(...this.inline((n as { children: PhrasingContent[] }).children, style));
          else if ("value" in n) out.push({ text: String((n as { value: unknown }).value), style });
      }
    }
    return out;
  }

  plain(node: Nodes): string {
    if ("value" in node && typeof node.value === "string") return node.value;
    if ("children" in node) return (node.children as Nodes[]).map((c) => this.plain(c)).join("");
    return "";
  }

  block(node: RootContent, x: number, width: number) {
    switch (node.type) {
      case "heading":
        this.heading(node, x, width);
        break;
      case "paragraph":
        this.runs(this.inline(node.children), { x, width });
        this.gap(2.2);
        break;
      case "list":
        this.list(node, x, width, 0);
        this.gap(1.5);
        break;
      case "table":
        this.table(node, x, width);
        break;
      case "thematicBreak":
        this.rule();
        break;
      case "blockquote":
        this.quote(node, x, width);
        break;
      case "code":
        this.code(node, x, width);
        break;
      case "html":
        this.runs([{ text: node.value.replace(/<[^>]*>/g, ""), style: "normal" }], { x, width });
        this.gap(2.2);
        break;
      default:
        if ("children" in node) {
          for (const child of node.children as RootContent[]) this.block(child, x, width);
        }
    }
  }

  heading(node: Heading, x: number, width: number) {
    const size = node.depth === 1 ? 14 : node.depth === 2 ? 12.5 : node.depth === 3 ? 11 : 10;
    const color = node.depth <= 2 ? ACCENT : INK;
    this.ensure(size * PT * 1.45 + 12); // keep a heading with the text under it
    this.gap(node.depth <= 2 ? 3 : 2);
    const runs = this.inline(node.children).map((r) => ({
      ...r,
      style: (r.style === "code" ? "code" : r.style.includes("italic") ? "bolditalic" : "bold") as Style,
      color,
    }));
    this.runs(runs, { x, width, size });
    this.gap(1.5);
  }

  list(node: List, x: number, width: number, depth: number) {
    const indent = 6;
    const start = node.start ?? 1;
    node.children.forEach((item: ListItem, i: number) => {
      const marker = node.ordered ? `${start + i}.` : depth % 2 === 0 ? "•" : "-";
      const textX = x + indent;
      const textW = width - indent;
      let markerDrawn = false;
      const drawMarker = (top: number) => {
        if (markerDrawn) return;
        markerDrawn = true;
        const size = 10;
        this.font("bold", size);
        this.color(ACCENT);
        // Ordered lists get their number; bullets alternate a dot (in helvetica's character set) and "-" by depth.
        this.doc.text(marker, x + (node.ordered ? 0 : 1), top + size * PT * 0.78);
      };
      const check = item.checked === true ? "[x] " : item.checked === false ? "[ ] " : "";
      item.children.forEach((child, ci) => {
        if (child.type === "paragraph") {
          const runs = this.inline(child.children);
          if (ci === 0 && check) runs.unshift({ text: check, style: "code" });
          this.runs(runs, { x: textX, width: textW, first: ci === 0 ? drawMarker : undefined });
          this.gap(0.8);
        } else if (child.type === "list") {
          if (!markerDrawn) {
            this.ensure(5);
            drawMarker(this.y);
          }
          this.list(child, textX, textW, depth + 1);
        } else {
          if (!markerDrawn) {
            this.ensure(5);
            drawMarker(this.y);
          }
          this.block(child, textX, textW);
        }
      });
      if (!markerDrawn) {
        this.ensure(5);
        drawMarker(this.y);
        this.gap(5);
      }
    });
  }

  table(node: Table, x: number, width: number) {
    const rows = node.children.map((row) => row.children.map((cell) => pdfSafe(this.plain(cell).trim())));
    if (rows.length === 0) return;
    const [head, ...body] = rows;
    const cols = head.length;
    const columnStyles: Record<number, { halign: "left" | "center" | "right" }> = {};
    (node.align || []).forEach((a, i) => {
      if (a) columnStyles[i] = { halign: a };
    });
    this.ensure(18);
    autoTable(this.doc, {
      head: [head],
      body: body.map((r) => (r.length < cols ? [...r, ...Array(cols - r.length).fill("")] : r.slice(0, cols))),
      startY: this.y + 1,
      margin: { left: x, right: PAGE_W - x - width, top: TOP, bottom: PAGE_H - BOTTOM },
      tableWidth: width,
      theme: "grid",
      styles: {
        font: "helvetica",
        fontSize: 8.5,
        cellPadding: { top: 1.8, bottom: 1.8, left: 2.2, right: 2.2 },
        textColor: INK,
        lineColor: RULE,
        lineWidth: 0.2,
        overflow: "linebreak",
        valign: "top",
      },
      headStyles: { fillColor: TINT, textColor: ACCENT, fontStyle: "bold", lineColor: RULE, lineWidth: 0.2 },
      alternateRowStyles: { fillColor: ZEBRA },
      columnStyles,
      didParseCell: (data) => {
        const align = node.align?.[data.column.index];
        if (data.section === "head" && align) data.cell.styles.halign = align;
      },
    });
    const finalY = (this.doc as unknown as { lastAutoTable?: { finalY: number } }).lastAutoTable?.finalY;
    this.y = (finalY ?? this.y) + 4;
  }

  quote(node: Blockquote, x: number, width: number) {
    const startPage = this.doc.getNumberOfPages();
    const top = this.y;
    for (const child of node.children) {
      if (child.type === "paragraph") {
        this.runs(this.inline(child.children, "italic"), { x: x + 5, width: width - 5, color: MUTED });
        this.gap(1.5);
      } else {
        this.block(child, x + 5, width - 5);
      }
    }
    if (this.doc.getNumberOfPages() === startPage) {
      this.doc.setDrawColor(ACCENT[0], ACCENT[1], ACCENT[2]);
      this.doc.setLineWidth(0.8);
      this.doc.line(x + 1, top, x + 1, this.y - 1.5);
    }
    this.gap(1.5);
  }

  code(node: Code, x: number, width: number) {
    const size = 8.5;
    const lead = size * PT * 1.4;
    this.font("code", size);
    const lines: string[] = [];
    for (const raw of pdfSafe(node.value).split("\n")) {
      lines.push(...((this.doc.splitTextToSize(raw || " ", width - 6) as string[]) || [" "]));
    }
    this.gap(1);
    for (const line of lines) {
      this.ensure(lead);
      this.doc.setFillColor(TINT[0], TINT[1], TINT[2]);
      this.doc.rect(x, this.y, width, lead, "F");
      this.font("code", size);
      this.color(INK);
      this.doc.text(line, x + 3, this.y + size * PT * 0.95);
      this.y += lead;
    }
    this.gap(3);
  }

  /** Running header and "Page X of Y" footer on every page, drawn last so tables' extra pages get them too. */
  finish(appName: string, label: string) {
    const total = this.doc.getNumberOfPages();
    const generated = new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
    for (let p = 1; p <= total; p++) {
      this.doc.setPage(p);
      this.font("bold", 8.5);
      this.color(ACCENT);
      this.doc.text(pdfSafe(appName), MARGIN_X, 13);
      this.font("normal", 8.5);
      this.color(MUTED);
      this.doc.text(pdfSafe(label), PAGE_W - MARGIN_X, 13, { align: "right" });
      this.doc.setDrawColor(RULE[0], RULE[1], RULE[2]);
      this.doc.setLineWidth(0.3);
      this.doc.line(MARGIN_X, 16, PAGE_W - MARGIN_X, 16);
      this.doc.line(MARGIN_X, PAGE_H - 14, PAGE_W - MARGIN_X, PAGE_H - 14);
      this.font("normal", 8);
      this.doc.text(`Generated ${generated}`, MARGIN_X, PAGE_H - 9);
      this.doc.text(`Page ${p} of ${total}`, PAGE_W - MARGIN_X, PAGE_H - 9, { align: "right" });
    }
  }

  title(text: string, size = 18) {
    this.font("bold", size);
    this.doc.setFont("times", "bold");
    this.color(INK);
    const lines: string[] = this.doc.splitTextToSize(pdfSafe(text), CONTENT_W);
    const lead = size * PT * 1.2;
    for (const line of lines) {
      this.ensure(lead);
      this.doc.text(line, MARGIN_X, this.y + size * PT * 0.8);
      this.y += lead;
    }
    this.gap(2);
  }
}

function filename(base: string, fallback: string): string {
  const slug = (base || "")
    .normalize("NFD")
    .replace(/[^A-Za-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60)
    .toLowerCase();
  return `${slug || fallback}-${new Date().toISOString().slice(0, 10)}.pdf`;
}

function referenceLine(paper: PdfPaper): string {
  const authors =
    paper.authors.length > 3 ? `${paper.authors.slice(0, 3).join(", ")} et al.` : paper.authors.join(", ");
  const bits = [authors || "Unknown authors", paper.publication_year ? `(${paper.publication_year})` : "", paper.description || ""];
  return bits.filter(Boolean).join(". ").replace(/\.\s*\(/, " (");
}

// ------------------------------------------------------------------
// Public exports
// ------------------------------------------------------------------

export function exportSynthesisPdf(input: {
  appName: string;
  question: string;
  answer: string;
  citations: PdfCitation[];
  papers: PdfPaper[];
  scopedCount?: number;
}) {
  const w = new Writer();
  w.title("Research Synthesis Report");
  const rows: [string, string][] = [["Question", input.question || "-"]];
  rows.push(["Generated", new Date().toLocaleString()]);
  rows.push([
    "Based on",
    input.scopedCount
      ? `${input.scopedCount} selected paper${input.scopedCount === 1 ? "" : "s"}`
      : `${input.papers.length} retrieved paper${input.papers.length === 1 ? "" : "s"} (abstracts only)`,
  ]);
  w.infoBox(rows);

  w.sectionTitle("Synthesis");
  w.markdown(input.answer);

  const cited = [...input.citations].sort((a, b) => a.paper_id - b.paper_id);
  if (cited.length > 0) {
    w.gap(2);
    w.sectionTitle("References");
    for (const c of cited) {
      const paper = input.papers[c.paper_id - 1];
      const tag = `[${c.paper_id}]`;
      // Keep each reference (title, authors line, DOI) together on one page.
      w.font("bold", 9.5);
      const titleLines = (w.doc.splitTextToSize(pdfSafe(paper?.title || c.title), CONTENT_W - 11) as string[]).length;
      w.ensure((titleLines + (paper ? 1 : 0) + (paper?.doi ? 1 : 0)) * 9.5 * PT * 1.45 + 2);
      w.font("bold", 9.5);
      const tagW = 11;
      const startY = w.y;
      w.runs([{ text: tag, style: "bold", color: ACCENT }], { width: tagW, size: 9.5 });
      w.y = startY;
      const runs: Run[] = [{ text: paper?.title || c.title, style: "bold" }];
      if (paper) {
        runs.push({ text: `\n${referenceLine(paper)}`, style: "normal", color: MUTED });
        if (paper.doi) runs.push({ text: `\nhttps://doi.org/${paper.doi}`, style: "normal", color: ACCENT });
      }
      w.runs(runs, { x: MARGIN_X + tagW, width: CONTENT_W - tagW, size: 9.5 });
      w.gap(2.5);
    }
  }

  w.gap(3);
  w.runs(
    [{ text: "AI-generated from paper abstracts. Verify important claims against the full papers before relying on them.", style: "italic" }],
    { size: 8, color: MUTED },
  );
  w.finish(input.appName, "Research synthesis");
  w.doc.save(filename(input.question, "research-synthesis"));
}

export function exportPaperPdf(input: { appName: string; paper: PdfPaper; deepDive?: string }) {
  const { paper } = input;
  const w = new Writer();
  w.title(paper.title || "Untitled paper", 16);
  w.runs([{ text: paper.authors.length ? paper.authors.join(", ") : "Unknown authors", style: "normal" }], {
    size: 10,
    color: MUTED,
  });
  w.gap(3);

  const rows: [string, string][] = [];
  rows.push(["Year", paper.publication_year ? String(paper.publication_year) : "Not stated"]);
  rows.push(["Source", paper.description || "Unknown"]);
  if (paper.doi) rows.push(["DOI", `https://doi.org/${paper.doi}`]);
  if (typeof paper.similarity_score === "number") {
    rows.push(["Relevance", `${(paper.similarity_score * 100).toFixed(1)}% match to your search`]);
  }
  w.infoBox(rows);

  w.sectionTitle("Abstract");
  if (paper.abstract) {
    w.markdown(paper.abstract);
  } else {
    w.runs([{ text: "No abstract is available for this paper.", style: "italic" }], { color: MUTED });
    w.gap(2);
  }

  if (input.deepDive && input.deepDive.trim()) {
    w.gap(2);
    w.sectionTitle("AI Deep Dive");
    w.markdown(input.deepDive);
  }

  w.gap(2);
  w.sectionTitle("BibTeX Citation");
  const bib = buildBibtex(paper);
  w.code({ type: "code", value: bib } as Code, MARGIN_X, CONTENT_W);

  w.finish(input.appName, "Paper brief");
  w.doc.save(filename(paper.title, "paper"));
}
