/** BibTeX for a search result. Used by "Copy BibTeX" and the paper PDF. */

export type PdfPaper = {
  title: string;
  authors: string[];
  description: string;
  publication_year: number | null;
  doi: string | null;
  abstract: string | null;
  similarity_score?: number | null;
};

/** "10.1/x" from any DOI form: bare, "doi:10.1/x" or a doi.org link (OpenAlex returns links). */
export function bareDoi(doi: string): string {
  return doi
    .trim()
    .replace(/^https?:\/\/(dx\.)?doi\.org\//i, "")
    .replace(/^doi:\s*/i, "");
}

export function doiUrl(doi: string): string {
  return `https://doi.org/${bareDoi(doi)}`;
}

function bibValue(value: string): string {
  return (value || "").replace(/[{}\\]/g, "").replace(/\s+/g, " ").trim();
}

export function buildBibtex(paper: PdfPaper): string {
  // "Fisher, M." -> Fisher ; "Monica Fisher" -> Fisher
  const first = (paper.authors[0] || "unknown").trim();
  const lastName = (first.includes(",") ? first.split(",")[0] : first.split(/\s+/).pop()) || "unknown";
  const firstWord = (paper.title || "paper").match(/[A-Za-z0-9]{3,}/)?.[0] || "paper";
  const year = paper.publication_year ? String(paper.publication_year) : "nd";
  const key = `${lastName}${year}${firstWord}`.normalize("NFD").replace(/[^A-Za-z0-9]/g, "").toLowerCase();
  const fields: [string, string][] = [
    ["title", bibValue(paper.title)],
    ["author", bibValue(paper.authors.join(" and ")) || "Unknown"],
    ["year", paper.publication_year ? String(paper.publication_year) : ""],
    ["journal", bibValue(paper.description)],
    ["doi", bibValue(paper.doi ? bareDoi(paper.doi) : "")],
  ];
  const body = fields
    .filter(([, v]) => v)
    .map(([k, v]) => `  ${k} = {${v}}`)
    .join(",\n");
  return `@article{${key},\n${body}\n}`;
}
