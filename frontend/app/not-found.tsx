import Link from "next/link";
import { BookOpen, ArrowRight, Search } from "lucide-react";

export default function NotFound() {
  return (
    <div className="min-h-screen w-full flex flex-col items-center justify-center bg-[var(--bg-primary)] text-[var(--text-primary)] p-6">
      <div className="max-w-md w-full text-center space-y-6 bg-[var(--bg-surface)] p-8 sm:p-10 rounded-2xl border border-[var(--border-color)] shadow-xl">
        <div className="w-16 h-16 rounded-full bg-[var(--accent-light)] text-[var(--accent)] flex items-center justify-center mx-auto">
          <BookOpen className="w-8 h-8" />
        </div>

        <div className="space-y-2">
          <div className="text-xs font-semibold uppercase tracking-wider text-[var(--accent)]">
            Error 404
          </div>
          <h1 className="font-serif-heading text-2xl sm:text-3xl font-bold text-[var(--text-primary)]">
            Academic Resource Not Found
          </h1>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] leading-relaxed">
            The page, paper synthesis, or research view you requested does not exist or has been moved.
          </p>
        </div>

        <div className="pt-2">
          <Link
            href="/"
            className="inline-flex items-center justify-center gap-2 w-full py-3 px-4 rounded-xl bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white font-medium text-xs sm:text-sm transition shadow-sm"
          >
            <Search className="w-4 h-4" />
            <span>Return to Literature Search</span>
            <ArrowRight className="w-4 h-4" />
          </Link>
        </div>

        <div className="pt-4 border-t border-[var(--border-subtle)] text-[11px] text-[var(--text-muted)]">
          ScholarPulse — Federated Discovery Across Five Academic Databases
        </div>
      </div>
    </div>
  );
}
