"use client";

/**
 * Renders AI-written markdown (answers, deep dives) as formatted text: headings, bold, lists, tables, rules,
 * quotes and code. Raw HTML in the text is never rendered, links are limited to http(s)/mailto, and images are
 * shown as their alt text, so AI output cannot inject markup or load remote content.
 */

import ReactMarkdown, { defaultUrlTransform, type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { normalizeMarkdown } from "./markdown-utils";

function safeUrl(url: string): string {
  const cleaned = defaultUrlTransform(url);
  return /^(https?:|mailto:)/i.test(cleaned) ? cleaned : "";
}

const components: Components = {
  a: ({ href, children }) =>
    href ? (
      <a href={href} target="_blank" rel="noopener noreferrer nofollow">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
  img: ({ alt }) => (alt ? <span className="sp-md-alt">[{alt}]</span> : null),
  table: ({ children }) => (
    <div className="sp-md-table">
      <table>{children}</table>
    </div>
  ),
};

export function Markdown({ text, compact = false }: { text: string; compact?: boolean }) {
  return (
    <div className={compact ? "sp-md sp-md-compact" : "sp-md"}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} urlTransform={safeUrl} components={components}>
        {normalizeMarkdown(text)}
      </ReactMarkdown>
    </div>
  );
}
