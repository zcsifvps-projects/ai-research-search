"use client";

/**
 * Read-only view of a shared conversation. The API returns only the title, questions, answers and papers:
 * nothing about who shared it.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { BookOpen, ExternalLink, MessageSquare } from "lucide-react";
import { APP_NAME } from "../../brand";
import { doiUrl } from "../../bibtex";
import { Markdown } from "../../markdown";

const API = process.env.NEXT_PUBLIC_API_URL as string;

type SharedTurn = { question: string; answer: string; citations?: { paper_id: number; title: string }[] };
type SharedPaper = {
  title: string;
  authors?: string[];
  publication_year?: number | null;
  description?: string;
  doi?: string | null;
};
type Shared = { title: string; turns: SharedTurn[]; papers: SharedPaper[]; updated_at: string | null };

export default function SharedConversationPage() {
  const params = useParams<{ token: string }>();
  const token = typeof params?.token === "string" ? params.token : "";
  const [data, setData] = useState<Shared | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!/^[A-Za-z0-9_-]{20,64}$/.test(token)) {
      // An impossible token: show the not-found message without asking the server.
      Promise.resolve().then(() => setFailed(true));
      return;
    }
    fetch(`${API}/share/${token}`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error("not found"))))
      .then((json: Shared) => setData(json), () => setFailed(true));
  }, [token]);

  // Papers cited anywhere in the conversation, in order.
  const cited = new Set<number>();
  data?.turns.forEach((t) => (t.citations || []).forEach((c) => cited.add(c.paper_id)));
  const references = [...cited].sort((a, b) => a - b).filter((id) => data?.papers[id - 1]);

  return (
    <main className="min-h-screen bg-[var(--bg-primary)] text-[var(--text-primary)]">
      <header className="border-b border-[var(--border-color)] bg-[var(--bg-surface)]">
        <div className="max-w-3xl mx-auto px-4 py-3 flex items-center justify-between gap-3">
          <Link href="/" className="font-semibold text-sm text-[var(--accent)]">
            {APP_NAME}
          </Link>
          <Link
            href="/"
            className="px-3 py-1.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-xs font-semibold"
          >
            Start your own research
          </Link>
        </div>
      </header>

      <div className="max-w-3xl mx-auto px-4 py-6 sm:py-10">
        {!data && !failed && <p className="text-sm text-[var(--text-secondary)]">Loading the shared conversation...</p>}

        {failed && (
          <div className="p-6 rounded-2xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
            <h1 className="text-lg font-bold">This link is not available</h1>
            <p className="mt-2 text-sm text-[var(--text-secondary)]">
              The person who shared it may have switched the link off or deleted the conversation.
            </p>
          </div>
        )}

        {data && (
          <>
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-[var(--text-muted)]">
              <MessageSquare className="w-3.5 h-3.5" />
              <span>Shared research conversation</span>
            </div>
            <h1 className="mt-2 font-serif-heading text-2xl font-bold">{data.title}</h1>

            <div className="mt-6 space-y-6">
              {data.turns.map((turn, i) => (
                <section key={i} className="space-y-3">
                  <div className="ml-auto max-w-xl rounded-2xl px-4 py-3 bg-[var(--accent)] text-white text-sm whitespace-pre-wrap">
                    {turn.question}
                  </div>
                  <div className="rounded-2xl p-4 sm:p-5 bg-[var(--bg-surface)] border border-[var(--border-color)] text-sm leading-relaxed">
                    <Markdown text={turn.answer} />
                  </div>
                </section>
              ))}
            </div>

            {references.length > 0 && (
              <section className="mt-8">
                <h2 className="text-sm font-semibold flex items-center gap-1.5">
                  <BookOpen className="w-4 h-4 text-[var(--accent)]" /> References
                </h2>
                <ol className="mt-3 space-y-2 text-xs">
                  {references.map((id) => {
                    const paper = data.papers[id - 1];
                    return (
                      <li key={id} className="flex gap-2">
                        <span className="font-semibold text-[var(--accent)] shrink-0">[{id}]</span>
                        <span>
                          <span className="font-semibold">{paper.title}</span>
                          <span className="text-[var(--text-secondary)]">
                            {paper.authors && paper.authors.length > 0 ? ` · ${paper.authors.slice(0, 3).join(", ")}${paper.authors.length > 3 ? " et al." : ""}` : ""}
                            {paper.publication_year ? ` (${paper.publication_year})` : ""}
                            {paper.description ? ` · ${paper.description}` : ""}
                          </span>
                          {paper.doi && (
                            <a
                              href={doiUrl(paper.doi)}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="ml-1 inline-flex items-center gap-0.5 text-[var(--accent)] hover:underline"
                            >
                              DOI <ExternalLink className="w-3 h-3" />
                            </a>
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ol>
              </section>
            )}

            <p className="mt-8 text-[11px] text-[var(--text-muted)]">
              AI-generated from paper abstracts. Check important claims against the full papers before relying on them.
            </p>
          </>
        )}
      </div>
    </main>
  );
}
