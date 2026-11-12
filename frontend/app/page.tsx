"use client";

import { APP_NAME } from "./brand";
import { buildBibtex, doiUrl } from "./bibtex";
import {
  CITATION_STYLES,
  buildBibtexFile,
  buildRisFile,
  citationHtml,
  citationText,
  downloadTextFile,
  fileSlug,
  formatCitation,
  type CitablePaper,
  type CitationStyle,
} from "./citations";
import { Markdown } from "./markdown";
import { AnimatePresence, animate, motion, useMotionValue, useReducedMotion } from "motion/react";
import { ModalPanel, ModalShell, SPRING, SlideUp, projectMomentum, useIsDesktop } from "./motion";
import { buildCsv, downloadCsv } from "./csv";
import type { ComparisonRow, Glance } from "./pdf";
import {
  Upload,
  Network,
  Quote,
  Folder,
  FolderPlus,
  Link2,
  MessageSquare,
  Pencil,
  Save,
  Share2,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  ArrowUpDown,
  BookOpen,
  Download,
  Lightbulb,
  Scale,
  Table2,
  Bookmark,
  BookmarkCheck,
  Check,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  Clock,
  Compass,
  Copy,
  Cookie,
  ExternalLink,
  FileDown,
  FileText,
  Filter,
  History,
  Info,
  Key,
  Layers,
  LogOut,
  Mail,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  RotateCcw,
  Search,
  Send,
  Settings,
  Shield,
  Sparkles,
  Square,
  Sun,
  Trash2,
  X
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

type Result = {
  title: string;
  authors: string[];
  description: string;
  publication_year: number | null;
  doi: string | null;
  abstract: string | null;
  similarity_score?: number | null;
  is_open_access?: boolean | null;
  database?: string | null;
  cited_by_count?: number | null;
  study_types?: string[];
  is_preprint?: boolean | null;
  is_retracted?: boolean;
  field?: string | null;
  pdf_url?: string | null;
};

const STUDY_TYPE_LABELS: Record<string, string> = {
  meta_analysis: "Meta-analysis",
  systematic_review: "Systematic review",
  review: "Review",
  rct: "RCT",
  clinical_trial: "Clinical trial",
  case_report: "Case report",
};

const FIELD_OPTIONS = [
  "Medicine",
  "Biology",
  "Neuroscience",
  "Psychology",
  "Computer Science",
  "Engineering",
  "Physics",
  "Chemistry",
  "Mathematics",
  "Materials Science",
  "Environmental Science",
  "Earth Sciences",
  "Agriculture",
  "Pharmacology",
  "Economics",
  "Business",
  "Social Sciences",
  "Arts and Humanities",
];

type SortOption = "relevance" | "newest" | "most_cited";
type SearchBy = "topic" | "title" | "author";

// The most specific study type the backend reported (the list is ordered most specific first).
function primaryStudyType(paper: Result): string | null {
  const first = paper.study_types?.[0];
  return first ? STUDY_TYPE_LABELS[first] || null : null;
}

function PaperBadges({ paper }: { paper: Result }) {
  const studyType = primaryStudyType(paper);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {paper.is_retracted && (
        <span className="px-1.5 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wide bg-red-600 text-white">
          Retracted
        </span>
      )}
      {paper.is_preprint && (
        <span className="px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-amber-500/15 text-amber-600 border border-amber-500/30">
          Preprint
        </span>
      )}
      {studyType && (
        <span className="px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-[var(--accent-light)] text-[var(--accent)] border border-[var(--accent)]/20">
          {studyType}
        </span>
      )}
      {typeof paper.cited_by_count === "number" && (
        <span className="text-[10px] text-[var(--text-muted)]">
          {paper.cited_by_count.toLocaleString()} {paper.cited_by_count === 1 ? "citation" : "citations"}
        </span>
      )}
    </div>
  );
}

// Takeaways are stored by paper identity (DOI, else title), so they survive new searches and history reloads.
function paperKey(paper: Result): string {
  return (paper.doi || paper.title || "").trim().toLowerCase();
}

function hasEvidence(paper: Result): boolean {
  const abstract = (paper.abstract || "").trim();
  return Boolean(abstract) && abstract.toLowerCase() !== "no abstract available." && !paper.is_retracted;
}

type TakeawayItem = { paper_id: number; takeaway: string };
type TableSortKey = "rank" | "title" | "year" | "citations";
type Comparison = { summary: string; rows: ComparisonRow[] };

const COMPARE_LABELS: [keyof ComparisonRow, string][] = [
  ["study_type", "Study type"],
  ["population", "Population"],
  ["sample_size", "Sample size"],
  ["methods", "Methods"],
  ["main_finding", "Main finding"],
  ["limitations", "Limitations"],
];

type Citation = {
  paper_id: number;
  title: string;
};

type AnswerMode = "quick" | "deep" | "medical";
type ResearchMode = "auto" | AnswerMode;
type MeterLabel = "yes" | "no" | "possibly";
type Meter = {
  yes: number;
  no: number;
  possibly: number;
  total: number;
  labels: { paper_id: number; label: MeterLabel }[];
};

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  timestamp: string;
  scopedPaperIds?: number[];
  // Phase 3: how the answer was made, for the label, the meter and the Go deeper / quick fallback buttons.
  mode?: AnswerMode;
  modeLabel?: string;
  subQueries?: string[];
  meter?: Meter | null;
  question?: string;
  offerQuick?: boolean;
};

const MODE_OPTIONS: { value: ResearchMode; label: string; hint: string }[] = [
  { value: "auto", label: "Auto", hint: "Picks Quick, Deep or Medical from your question" },
  { value: "quick", label: "Quick", hint: "One search, a short answer from the top papers" },
  { value: "deep", label: "Deep", hint: "3 to 5 searches and a structured review (takes longer)" },
  { value: "medical", label: "Medical", hint: "Ranks meta-analyses, reviews and trials first; not medical advice" },
];

const METER_STYLES: Record<MeterLabel, { label: string; bar: string; text: string }> = {
  yes: { label: "Yes", bar: "bg-emerald-500", text: "text-emerald-600" },
  possibly: { label: "Possibly", bar: "bg-amber-400", text: "text-amber-600" },
  no: { label: "No", bar: "bg-red-500", text: "text-red-600" },
};

function now(): string {
  return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

let messageCounter = 0;
function newMessageId(tag = "msg"): string {
  messageCounter += 1;
  return `${tag}-${Date.now()}-${messageCounter}`;
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

// Paper ids in an answer refer to the list sent with it. Scoped follow-ups send a subset, so map back to positions
// in the full results list (what the cards and the detail view use).
function toResultIds(meter: Meter | null | undefined, sent: Result[], all: Result[]): Meter | null {
  if (!meter) return null;
  const labels = meter.labels
    .map((item) => ({ ...item, paper_id: all.indexOf(sent[item.paper_id - 1]) + 1 }))
    .filter((item) => item.paper_id > 0);
  return { ...meter, labels };
}

function EvidenceMeter({ meter, onOpenPaper }: { meter: Meter; onOpenPaper: (paperId: number) => void }) {
  const [open, setOpen] = useState<MeterLabel | null>(null);
  const order: MeterLabel[] = ["yes", "possibly", "no"];
  const pct = (n: number) => Math.round((n / meter.total) * 100);
  return (
    <div className="mt-4 pt-3 border-t border-[var(--border-color)]">
      <div className="text-xs font-semibold text-[var(--text-secondary)] mb-2">
        What the papers say (based on {meter.total} papers)
      </div>
      <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-[var(--bg-surface-elevated)]" aria-hidden="true">
        {order.map((key) =>
          meter[key] > 0 ? (
            <div key={key} className={METER_STYLES[key].bar} style={{ width: `${pct(meter[key])}%` }} />
          ) : null
        )}
      </div>
      <div className="mt-2 flex flex-wrap gap-2 text-xs">
        {order.map((key) => (
          <button
            key={key}
            type="button"
            disabled={meter[key] === 0}
            onClick={() => setOpen(open === key ? null : key)}
            aria-expanded={open === key}
            className={`px-2 py-0.5 rounded-md border border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)] disabled:opacity-50 transition ${
              open === key ? "bg-[var(--bg-surface-elevated)]" : ""
            }`}
          >
            <span className={`font-semibold ${METER_STYLES[key].text}`}>{pct(meter[key])}% {METER_STYLES[key].label}</span>
            <span className="text-[var(--text-muted)]"> ({meter[key]})</span>
          </button>
        ))}
      </div>
      {open && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {meter.labels
            .filter((item) => item.label === open)
            .map((item) => (
              <button
                key={item.paper_id}
                type="button"
                onClick={() => onOpenPaper(item.paper_id)}
                className="text-xs px-2 py-0.5 rounded-md border border-[var(--border-color)] bg-[var(--bg-surface-elevated)] hover:text-[var(--accent)] transition font-semibold"
              >
                [{item.paper_id}]
              </button>
            ))}
        </div>
      )}
      <div className="mt-2 text-[11px] text-[var(--text-muted)]">
        AI labels from abstracts only, not a vote on the truth. Check the papers before relying on it.
      </div>
    </div>
  );
}

type User = {
  id: number;
  email: string;
  full_name?: string | null;
  is_verified?: boolean;
};

type HistoryItem = {
  id: number;
  session_id: string;
  user_id?: number | null;
  question: string;
  answer: string;
  citations: Citation[];
  papers?: Result[];
  created_at: string;
};

type BookmarkItem = {
  id: number;
  user_id: number;
  title: string;
  authors: string[];
  abstract: string | null;
  publication_year: number | null;
  source: string | null;
  doi: string | null;
  created_at: string;
  collection_ids?: number[];
};

type CollectionInfo = { id: number; name: string; count: number };

type ThreadSummary = {
  id: number;
  title: string;
  turn_count: number;
  paper_count: number;
  shared: boolean;
  share_token: string | null;
  updated_at: string | null;
};

type ThreadTurn = { question: string; answer: string; citations: Citation[]; mode?: AnswerMode | null };

type DocumentInfo = { id: number; title: string; filename: string | null; pages: number; words: number; created_at: string | null };
type DocCitation = { n: number; document_id: number; title: string; page: number };
type DocMessage = { id: string; role: "user" | "assistant"; content: string; citations?: DocCitation[] };
type GraphNode = { id: string; title: string; year: number | null; cited_by_count: number | null; doi: string | null; kind: "seed" | "reference" | "citing" };
type GraphData = { seed: GraphNode; nodes: GraphNode[]; reference_total: number; citing_total: number };

const MAX_UPLOAD_MB = 20;
const MAX_DOCS_PER_CHAT = 5;

const GUEST_SEARCH_LIMIT = 5;

export default function Home() {
  const [query, setQuery] = useState("");
  const [followUpQuery, setFollowUpQuery] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [results, setResults] = useState<Result[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingStep, setLoadingStep] = useState("");
  const [error, setError] = useState("");

  // Filters & Citations
  const [yearFilter, setYearFilter] = useState<"all" | "3" | "5" | "10">("all");
  const [openAccessOnly, setOpenAccessOnly] = useState<boolean>(false);
  const [showMoreFilters, setShowMoreFilters] = useState<boolean>(false);
  const [researchMode, setResearchMode] = useState<ResearchMode>("auto");
  // Mode of the current conversation, so follow-up questions on a medical answer stay medical.
  const [activeMode, setActiveMode] = useState<AnswerMode>("quick");
  const [studyTypeFilter, setStudyTypeFilter] = useState<string>("");
  const [fieldFilter, setFieldFilter] = useState<string>("");
  const [minCitations, setMinCitations] = useState<string>("");
  const [excludePreprints, setExcludePreprints] = useState<boolean>(false);
  const [sortBy, setSortBy] = useState<SortOption>("relevance");
  const [searchBy, setSearchBy] = useState<SearchBy>("topic");
  const activeExtraFilters =
    (studyTypeFilter ? 1 : 0) +
    (fieldFilter ? 1 : 0) +
    (minCitations ? 1 : 0) +
    (excludePreprints ? 1 : 0) +
    (sortBy !== "relevance" ? 1 : 0) +
    (searchBy !== "topic" ? 1 : 0);
  // Phase 7: the Cite dialog (formatted references, BibTeX, RIS)
  const [citeTarget, setCiteTarget] = useState<Result | null>(null);
  const [citeStyle, setCiteStyle] = useState<CitationStyle | "bibtex">("apa");
  const [citeCopied, setCiteCopied] = useState(false);
  const [copiedAnswerId, setCopiedAnswerId] = useState<string | null>(null);

  // Deep dive states
  const [deepDiveLoading, setDeepDiveLoading] = useState<Record<number, boolean>>({});
  const [deepDives, setDeepDives] = useState<Record<string, { text: string; glance: Glance | null }>>({});
  const [deepDiveErrors, setDeepDiveErrors] = useState<Record<string, string>>({});

  // Infinite Scroll / Load More Papers
  const [visiblePapers, setVisiblePapers] = useState(10);
  const papersScrollRef = useRef<HTMLDivElement>(null);
  const loadMoreRef = useRef<HTMLDivElement>(null);

  // Feature 1: Paper Detail View
  const [selectedPaperForDetail, setSelectedPaperForDetail] = useState<{
    paper: Result;
    index: number;
  } | null>(null);
  const [detailTab, setDetailTab] = useState<"overview" | "metadata">("overview");

  // Feature 2: Select Papers into Question Context (Context Scoping)
  const [selectedPaperIds, setSelectedPaperIds] = useState<number[]>([]);

  // Phase 2: takeaways, table view, compare
  const [takeaways, setTakeaways] = useState<Record<string, string>>({});
  const [takeawaysLoading, setTakeawaysLoading] = useState(false);
  const [takeawaysError, setTakeawaysError] = useState("");
  const [tableOpen, setTableOpen] = useState(false);
  const [tableSort, setTableSort] = useState<{ key: TableSortKey; dir: "asc" | "desc" }>({ key: "rank", dir: "asc" });
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState("");
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [compareCopied, setCompareCopied] = useState(false);

  // Feature 3: Bookmarks / My Library
  const [bookmarks, setBookmarks] = useState<BookmarkItem[]>([]);
  const [libraryLoading, setLibraryLoading] = useState(false);

  // Phase 6: folders, saved conversations, share links
  const [collections, setCollections] = useState<CollectionInfo[]>([]);
  const [activeCollection, setActiveCollection] = useState<number | null>(null);
  const [folderMenuFor, setFolderMenuFor] = useState<number | null>(null);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadsSectionOpen, setThreadsSectionOpen] = useState(true);
  const [currentThreadId, setCurrentThreadId] = useState<number | null>(null);
  const [savedTurnCount, setSavedTurnCount] = useState(0);
  const [threadSaving, setThreadSaving] = useState(false);
  const [threadNotice, setThreadNotice] = useState("");
  const [shareTarget, setShareTarget] = useState<ThreadSummary | null>(null);
  const [shareBusy, setShareBusy] = useState(false);
  const [shareCopied, setShareCopied] = useState(false);

  // Phase 8: uploaded PDFs (text only), document chat, citation map
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [docsSectionOpen, setDocsSectionOpen] = useState(true);
  const [docUploading, setDocUploading] = useState(false);
  const [docNotice, setDocNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [docSelection, setDocSelection] = useState<number[]>([]);
  const [docChatIds, setDocChatIds] = useState<number[] | null>(null);
  const [docMessages, setDocMessages] = useState<DocMessage[]>([]);
  const [docQuestion, setDocQuestion] = useState("");
  const [docAsking, setDocAsking] = useState(false);
  const [docChatError, setDocChatError] = useState("");
  const [graphPaper, setGraphPaper] = useState<Result | null>(null);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [graphError, setGraphError] = useState("");

  // Theme mode: 'light' or 'dark'
  const [theme, setTheme] = useState<"light" | "dark">("light");

  // Sidebar state: always starts collapsed on page load (open it with the toggle button)
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const isDesktop = useIsDesktop();
  const reduceMotion = useReducedMotion();
  // One motion value drives the sidebar's horizontal position, so the open/close spring and the
  // finger-drag share it (and a flick's velocity carries straight into the spring).
  const sidebarX = useMotionValue(-288);
  const sidebarFirstRun = useRef(true);
  const sidebarReleaseVelocity = useRef(0);
  useEffect(() => {
    const target = isDesktop || sidebarOpen ? 0 : -288;
    if (sidebarFirstRun.current) {
      sidebarFirstRun.current = false;
      sidebarX.set(target); // no animation on first paint: the sidebar must simply start closed
      return;
    }
    const velocity = sidebarReleaseVelocity.current;
    sidebarReleaseVelocity.current = 0;
    const controls = animate(sidebarX, target, reduceMotion ? { duration: 0.15 } : { ...SPRING, velocity });
    return () => controls.stop();
  }, [sidebarOpen, isDesktop, reduceMotion, sidebarX]);
  const [historySectionOpen, setHistorySectionOpen] = useState(true);
  const [librarySectionOpen, setLibrarySectionOpen] = useState(true);

  // Account Menu & Settings Modal
  const [accountDropdownOpen, setAccountDropdownOpen] = useState(false);
  const [settingsModalOpen, setSettingsModalOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<"account" | "password" | "danger">("account");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordLoading, setPasswordLoading] = useState(false);
  const [passwordError, setPasswordError] = useState("");
  const [passwordSuccess, setPasswordSuccess] = useState("");
  const [deleteAccountModalOpen, setDeleteAccountModalOpen] = useState(false);
  const [deletePasswordConfirm, setDeletePasswordConfirm] = useState("");
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  // Compliance Policy Modals
  const [activePolicyModal, setActivePolicyModal] = useState<
    "privacy" | "terms" | "cookie_policy" | "cookie_prefs" | null
  >(null);
  // Strictly necessary storage is always on. "preferences" covers the optional, non-essential storage
  // (currently only the remembered light/dark theme). There are no analytics or advertising cookies.
  const [cookiePrefs, setCookiePrefs] = useState({
    essential: true,
    preferences: false,
  });
  // True once the visitor has made a choice; the banner shows until then.
  // Starts true so the banner never flashes on the server render or for returning visitors.
  const [cookieDecided, setCookieDecided] = useState(true);

  // Authentication
  const [user, setUser] = useState<User | null>(null);
  const [guestSearchCount, setGuestSearchCount] = useState<number>(0);
  const [authModalOpen, setAuthModalOpen] = useState(false);
  const [authMode, setAuthMode] = useState<"login" | "register">("register");
  const [authPromptMessage, setAuthPromptMessage] = useState("");
  const [authFullName, setAuthFullName] = useState("");
  const [authEmail, setAuthEmail] = useState("");
  const [authPassword, setAuthPassword] = useState("");
  const [authError, setAuthError] = useState("");
  const [authSuccess, setAuthSuccess] = useState("");
  const [authLoading, setAuthLoading] = useState(false);
  const [softGateModalOpen, setSoftGateModalOpen] = useState(false);

  // Forgot / Reset Password Modals
  const [forgotPasswordModalOpen, setForgotPasswordModalOpen] = useState(false);
  const [forgotEmail, setForgotEmail] = useState("");
  const [forgotLoading, setForgotLoading] = useState(false);
  const [forgotSuccess, setForgotSuccess] = useState("");
  const [forgotError, setForgotError] = useState("");

  const [resetPasswordModalOpen, setResetPasswordModalOpen] = useState(false);
  const [resetToken, setResetToken] = useState("");
  const [resetNewPassword, setResetNewPassword] = useState("");
  const [resetConfirmPassword, setResetConfirmPassword] = useState("");
  const [resetLoading, setResetLoading] = useState(false);
  const [resetSuccess, setResetSuccess] = useState("");
  const [resetError, setResetError] = useState("");

  // Verification banner / status
  const [verificationBanner, setVerificationBanner] = useState<{
    type: "success" | "info" | "error";
    message: string;
  } | null>(null);

  // History
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [confirmClearHistory, setConfirmClearHistory] = useState(false);
  const [clearingHistory, setClearingHistory] = useState(false);

  const chatEndRef = useRef<HTMLDivElement>(null);
  const accountMenuRef = useRef<HTMLDivElement>(null);

  // Set at build time (see frontend/.env.example); next.config.ts refuses to start without it.
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL as string;

  // ============================================================
  // INITIALIZATION & URL PARAMETER HANDLING
  // ============================================================

  useEffect(() => {
    if (messages.length > 0) {
      chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, loading]);

  useEffect(() => {
    if (!loadMoreRef.current || !papersScrollRef.current) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setVisiblePapers((prev) => Math.min(prev + 10, results.length));
        }
      },
      { root: papersScrollRef.current, rootMargin: "200px" }
    );
    observer.observe(loadMoreRef.current);
    return () => observer.disconnect();
  }, [results, visiblePapers]);

  function toggleSidebar() {
    const nextState = !sidebarOpen;
    setSidebarOpen(nextState);
  }

  function saveCookieConsent(preferences: boolean) {
    setCookiePrefs({ essential: true, preferences });
    setCookieDecided(true);
    setActivePolicyModal(null);
    try {
      localStorage.setItem(
        "scholarpulse-cookie-consent",
        JSON.stringify({ essential: true, preferences, decidedAt: new Date().toISOString() }),
      );
      if (preferences) {
        localStorage.setItem("researchai-theme", theme);
      } else {
        localStorage.removeItem("researchai-theme");
      }
    } catch {
      // storage unavailable (private mode): the choice simply applies to this visit
    }
  }

  function toggleTheme() {
    const nextTheme = theme === "light" ? "dark" : "light";
    setTheme(nextTheme);
    if (cookiePrefs.preferences) {
      localStorage.setItem("researchai-theme", nextTheme); // optional storage: only with consent
    }
    document.documentElement.setAttribute("data-theme", nextTheme);
    if (nextTheme === "dark") {
      document.documentElement.classList.add("dark");
    } else {
      document.documentElement.classList.remove("dark");
    }
  }

  function getSessionId() {
    if (typeof window === "undefined") return "anonymous";
    const key = "researchai-session-id";
    const existing = window.localStorage.getItem(key);
    if (existing && /^[A-Za-z0-9_-]{16,128}$/.test(existing)) return existing;
    let created: string;
    if (typeof window.crypto?.randomUUID === "function") {
      created = window.crypto.randomUUID();
    } else {
      // cryptographically random even where randomUUID is unavailable (plain-http contexts)
      const bytes = new Uint8Array(16);
      window.crypto.getRandomValues(bytes);
      created = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
    }
    window.localStorage.setItem(key, created);
    return created;
  }

  // ============================================================
  // USER DISPLAY & INITIALS HELPERS
  // ============================================================

  function getDisplayName(u: User | null): string {
    if (!u) return "Guest";
    if (u.full_name && u.full_name.trim()) return u.full_name.trim();
    if (u.email) return u.email.split("@")[0];
    return "Member";
  }

  function getInitials(u: User | null): string {
    if (!u) return "U";
    if (u.full_name && u.full_name.trim()) {
      const parts = u.full_name.trim().split(" ").filter(Boolean);
      if (parts.length >= 2) {
        return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
      }
      return parts[0][0].toUpperCase();
    }
    if (u.email) {
      return u.email[0].toUpperCase();
    }
    return "U";
  }

  // ============================================================
  // AUTHENTICATION & SETTINGS (COOKIE-BASED)
  // ============================================================

  async function fetchCurrentUser() {
    try {
      const res = await fetch(`${apiBaseUrl}/auth/me`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setUser(data.user);
        loadBookmarks();
        loadHistory();
      } else {
        setUser(null);
        loadHistory();
      }
    } catch {
      loadHistory();
    }
  }

  async function handleVerifyEmailToken(tokenStr: string) {
    try {
      const res = await fetch(`${apiBaseUrl}/auth/verify-email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ token: tokenStr }),
      });
      const data = await res.json();
      if (res.ok) {
        setVerificationBanner({
          type: "success",
          message: `Your email has been verified! Welcome to ${APP_NAME}.`,
        });
        if (data.user) setUser(data.user);
        // Now that the account is verified: claim this browser's guest history and load saved data
        fetch(`${apiBaseUrl}/history/sync`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({ session_id: getSessionId() }),
        }).catch(() => {});
        loadBookmarks();
        loadHistory();
        setAuthModalOpen(false);
        // Clean URL
        window.history.replaceState({}, document.title, window.location.pathname);
      } else {
        setVerificationBanner({
          type: "error",
          message: data.detail || "Verification token expired or invalid.",
        });
      }
    } catch {
      setVerificationBanner({
        type: "error",
        message: "Failed to verify email. Please try again.",
      });
    }
  }

  async function handleResendVerification() {
    if (!user?.email) return;
    try {
      const res = await fetch(`${apiBaseUrl}/auth/resend-verification`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email: user.email }),
      });
      if (res.ok) {
        setVerificationBanner({
          type: "info",
          message: "A new verification email has been dispatched to your inbox.",
        });
      }
    } catch {
      // ignore
    }
  }

  async function handleAuthSubmit(e: React.FormEvent) {
    e.preventDefault();
    setAuthError("");
    setAuthSuccess("");

    if (authPassword.length < 8) {
      setAuthError("Password must be at least 8 characters.");
      return;
    }

    setAuthLoading(true);
    const endpoint = authMode === "login" ? "/auth/login" : "/auth/register";

    try {
      const res = await fetch(`${apiBaseUrl}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          email: authEmail,
          password: authPassword,
          full_name: authMode === "register" ? authFullName || undefined : undefined,
        }),
      });

      const data = await res.json();

      if (!res.ok) {
        setAuthError(data.detail || "Authentication failed. Please try again.");
        setAuthLoading(false);
        return;
      }

      if (authMode === "register") {
        // Registration does not sign you in: the emailed link verifies the address and signs you in.
        // The API answers identically whether or not the address was already registered.
        setAuthSuccess(
          data.message ||
            "Check your email. We have sent a link to verify your address and finish creating your account.",
        );
        setAuthPassword("");
        return;
      }

      setUser(data.user);

      // Sync guest search history to user account
      const sid = getSessionId();
      fetch(`${apiBaseUrl}/history/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ session_id: sid }),
      }).catch(() => {});

      loadBookmarks();
      loadHistory();

      setAuthModalOpen(false);
      setSoftGateModalOpen(false);
      setAuthEmail("");
      setAuthPassword("");
      setAuthFullName("");
    } catch {
      setAuthError("Network error. Please check your connection.");
    } finally {
      setAuthLoading(false);
    }
  }

  async function logout() {
    try {
      await fetch(`${apiBaseUrl}/auth/logout`, {
        method: "POST",
        credentials: "include",
      });
    } catch {
      // ignore
    }
    setUser(null);
    setAccountDropdownOpen(false);
    setSettingsModalOpen(false);
    setBookmarks([]);
    setCollections([]);
    setActiveCollection(null);
    setThreads([]);
    setDocuments([]);
    setDocSelection([]);
    setDocChatIds(null);
    setCurrentThreadId(null);
    setSavedTurnCount(0);
    loadHistory();
  }

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    setPasswordError("");
    setPasswordSuccess("");

    if (newPassword.length < 8) {
      setPasswordError("New password must be at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("New passwords do not match.");
      return;
    }

    setPasswordLoading(true);
    try {
      const res = await fetch(`${apiBaseUrl}/auth/change-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setPasswordError(data.detail || "Could not change password.");
      } else {
        setPasswordSuccess("Password updated successfully!");
        setCurrentPassword("");
        setNewPassword("");
        setConfirmPassword("");
      }
    } catch {
      setPasswordError("Network error. Please try again.");
    } finally {
      setPasswordLoading(false);
    }
  }

  async function handleForgotPasswordSubmit(e: React.FormEvent) {
    e.preventDefault();
    setForgotError("");
    setForgotSuccess("");
    setForgotLoading(true);

    try {
      const res = await fetch(`${apiBaseUrl}/auth/forgot-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email: forgotEmail }),
      });
      const data = await res.json();
      if (res.ok) {
        setForgotSuccess("If registered, password reset instructions have been sent to your email.");
      } else {
        setForgotError(data.detail || "Could not process password reset.");
      }
    } catch {
      setForgotError("Network error. Please try again.");
    } finally {
      setForgotLoading(false);
    }
  }

  async function handleResetPasswordSubmit(e: React.FormEvent) {
    e.preventDefault();
    setResetError("");
    setResetSuccess("");

    if (resetNewPassword.length < 8) {
      setResetError("Password must be at least 8 characters.");
      return;
    }
    if (resetNewPassword !== resetConfirmPassword) {
      setResetError("Passwords do not match.");
      return;
    }

    setResetLoading(true);
    try {
      const res = await fetch(`${apiBaseUrl}/auth/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          token: resetToken,
          new_password: resetNewPassword,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setResetSuccess("Password reset successfully! You may now sign in.");
        setTimeout(() => {
          setResetPasswordModalOpen(false);
          setResetNewPassword("");
          setResetConfirmPassword("");
          setResetSuccess("");
          openAuthModal("login");
        }, 2000);
      } else {
        setResetError(data.detail || "Invalid or expired reset token.");
      }
    } catch {
      setResetError("Network error. Could not reset password.");
    } finally {
      setResetLoading(false);
    }
  }

  async function handleDeleteAccount(e: React.FormEvent) {
    e.preventDefault();
    setDeleteError("");
    setDeleteLoading(true);

    try {
      const res = await fetch(`${apiBaseUrl}/auth/delete-account`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ password: deletePasswordConfirm }),
      });
      const data = await res.json();
      if (!res.ok) {
        setDeleteError(data.detail || "Could not delete account.");
      } else {
        setDeleteAccountModalOpen(false);
        setSettingsModalOpen(false);
        logout();
      }
    } catch {
      setDeleteError("Network error. Could not delete account.");
    } finally {
      setDeleteLoading(false);
    }
  }

  function openAuthModal(mode: "login" | "register", prompt = "") {
    setAuthMode(mode);
    setAuthPromptMessage(prompt);
    setAuthError("");
    setAuthSuccess("");
    setAuthModalOpen(true);
  }

  // ============================================================
  // BOOKMARKS / MY LIBRARY
  // ============================================================

  async function loadBookmarks() {
    setLibraryLoading(true);
    try {
      const res = await fetch(`${apiBaseUrl}/bookmarks`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setBookmarks(data.bookmarks || []);
      }
    } catch {
      // ignore
    } finally {
      setLibraryLoading(false);
    }
    loadCollections();
    loadThreads();
    loadDocuments();
  }

  // ============================================================
  // FOLDERS (collections)
  // ============================================================

  async function loadCollections() {
    try {
      const res = await fetch(`${apiBaseUrl}/collections`, { credentials: "include" });
      if (res.ok) setCollections((await res.json()).collections || []);
    } catch {
      // ignore
    }
  }

  async function createCollection() {
    const name = window.prompt("Name for the new folder:")?.trim();
    if (!name) return;
    const res = await fetch(`${apiBaseUrl}/collections`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ name: name.slice(0, 100) }),
    }).catch(() => null);
    if (res && !res.ok) {
      const errData = await res.json().catch(() => ({}));
      setError(typeof errData.detail === "string" ? errData.detail : "Could not create the folder.");
    }
    loadCollections();
  }

  async function renameCollection(folder: CollectionInfo) {
    const name = window.prompt("Rename folder:", folder.name)?.trim();
    if (!name || name === folder.name) return;
    await fetch(`${apiBaseUrl}/collections/${folder.id}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ name: name.slice(0, 100) }),
    }).catch(() => null);
    loadCollections();
  }

  async function deleteCollection(folder: CollectionInfo) {
    if (!window.confirm(`Delete the folder "${folder.name}"? The papers stay in your library.`)) return;
    await fetch(`${apiBaseUrl}/collections/${folder.id}`, { method: "DELETE", credentials: "include" }).catch(() => null);
    if (activeCollection === folder.id) setActiveCollection(null);
    loadBookmarks();
  }

  async function toggleInCollection(bookmark: BookmarkItem, folderId: number) {
    const inside = (bookmark.collection_ids || []).includes(folderId);
    const res = await fetch(
      inside ? `${apiBaseUrl}/collections/${folderId}/items/${bookmark.id}` : `${apiBaseUrl}/collections/${folderId}/items`,
      {
        method: inside ? "DELETE" : "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: inside ? undefined : JSON.stringify({ bookmark_id: bookmark.id }),
      },
    ).catch(() => null);
    if (res && res.ok) {
      setBookmarks((prev) =>
        prev.map((b) =>
          b.id === bookmark.id
            ? {
                ...b,
                collection_ids: inside
                  ? (b.collection_ids || []).filter((id) => id !== folderId)
                  : [...(b.collection_ids || []), folderId],
              }
            : b,
        ),
      );
      setCollections((prev) => prev.map((c) => (c.id === folderId ? { ...c, count: c.count + (inside ? -1 : 1) } : c)));
    }
  }

  // ============================================================
  // SAVED CONVERSATIONS (threads) AND SHARE LINKS
  // ============================================================

  async function loadThreads() {
    try {
      const res = await fetch(`${apiBaseUrl}/threads`, { credentials: "include" });
      if (res.ok) setThreads((await res.json()).threads || []);
    } catch {
      // ignore
    }
  }

  // Question and answer pairs from the chat, in order.
  function conversationTurns(list: Message[]): ThreadTurn[] {
    const turns: ThreadTurn[] = [];
    for (let i = 0; i < list.length - 1; i += 1) {
      const q = list[i];
      const a = list[i + 1];
      if (q.role === "user" && a.role === "assistant") {
        turns.push({
          question: q.content.slice(0, 2000),
          answer: a.content.slice(0, 20000),
          citations: (a.citations || []).slice(0, 50),
          mode: a.mode || null,
        });
        i += 1;
      }
    }
    return turns.slice(-50);
  }

  const chatTurns = conversationTurns(messages);
  const unsavedTurns = chatTurns.length > 0 && (currentThreadId === null || chatTurns.length !== savedTurnCount);

  async function saveThread() {
    if (!user) {
      openAuthModal("register", "Create a free account to save conversations.");
      return;
    }
    if (!user.is_verified) {
      setThreadNotice("Verify your email to save conversations.");
      return;
    }
    if (chatTurns.length === 0 || threadSaving) return;
    setThreadSaving(true);
    setThreadNotice("");
    try {
      const res = await fetch(currentThreadId ? `${apiBaseUrl}/threads/${currentThreadId}` : `${apiBaseUrl}/threads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ turns: chatTurns, papers: results.slice(0, 50) }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not save the conversation.");
      setCurrentThreadId(data.id);
      setSavedTurnCount(chatTurns.length);
      setThreadNotice("Saved.");
      loadThreads();
    } catch (err) {
      setThreadNotice(errorMessage(err, "Could not save the conversation."));
    } finally {
      setThreadSaving(false);
    }
  }

  async function openThread(summary: ThreadSummary) {
    try {
      const res = await fetch(`${apiBaseUrl}/threads/${summary.id}`, { credentials: "include" });
      if (!res.ok) throw new Error("Could not open the conversation.");
      const data = await res.json();
      const turns: ThreadTurn[] = Array.isArray(data.turns) ? data.turns : [];
      showResults(Array.isArray(data.papers) ? data.papers : []);
      setSelectedPaperForDetail(null);
      const restored: Message[] = [];
      turns.forEach((t, i) => {
        restored.push({ id: newMessageId(`thread-q${i}`), role: "user", content: t.question, timestamp: "" });
        restored.push({
          id: newMessageId(`thread-a${i}`),
          role: "assistant",
          content: t.answer,
          citations: t.citations || [],
          timestamp: "",
          mode: t.mode || undefined,
        });
      });
      setMessages(restored);
      const lastMode = turns.length > 0 ? turns[turns.length - 1].mode : null;
      setActiveMode(lastMode === "medical" ? "medical" : "quick");
      setCurrentThreadId(summary.id);
      setSavedTurnCount(turns.length);
      setThreadNotice("");
      if (window.innerWidth < 768) setSidebarOpen(false);
    } catch (err) {
      setError(errorMessage(err, "Could not open the conversation."));
    }
  }

  async function renameThread(summary: ThreadSummary) {
    const title = window.prompt("Rename conversation:", summary.title)?.trim();
    if (!title || title === summary.title) return;
    await fetch(`${apiBaseUrl}/threads/${summary.id}/rename`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ title: title.slice(0, 200) }),
    }).catch(() => null);
    loadThreads();
  }

  async function deleteThread(summary: ThreadSummary) {
    const extra = summary.shared ? " Its share link will stop working." : "";
    if (!window.confirm(`Delete "${summary.title}"?${extra}`)) return;
    await fetch(`${apiBaseUrl}/threads/${summary.id}`, { method: "DELETE", credentials: "include" }).catch(() => null);
    if (currentThreadId === summary.id) {
      setCurrentThreadId(null);
      setSavedTurnCount(0);
    }
    loadThreads();
  }

  function shareUrl(token: string): string {
    return `${window.location.origin}/share/${token}`;
  }

  async function setSharing(summary: ThreadSummary, on: boolean) {
    setShareBusy(true);
    try {
      const res = await fetch(`${apiBaseUrl}/threads/${summary.id}/${on ? "share" : "unshare"}`, {
        method: "POST",
        credentials: "include",
      });
      if (res.ok) {
        const data: ThreadSummary = await res.json();
        setShareTarget(data);
        setThreads((prev) => prev.map((t) => (t.id === data.id ? { ...t, ...data } : t)));
      }
    } finally {
      setShareBusy(false);
    }
  }

  async function copyShareLink(token: string) {
    try {
      await navigator.clipboard.writeText(shareUrl(token));
      setShareCopied(true);
      setTimeout(() => setShareCopied(false), 2000);
    } catch {
      window.prompt("Copy this link:", shareUrl(token));
    }
  }

  // ============================================================
  // UPLOADED DOCUMENTS (Phase 8): only the extracted text is kept on the server
  // ============================================================

  async function loadDocuments() {
    try {
      const res = await fetch(`${apiBaseUrl}/documents`, { credentials: "include" });
      if (res.ok) setDocuments((await res.json()).documents || []);
    } catch {
      // ignore
    }
  }

  async function uploadDocument(file: File) {
    if (!user) {
      openAuthModal("register", "Create a free account to upload PDFs and chat with them.");
      return;
    }
    if (!user.is_verified) {
      setDocNotice({ kind: "error", text: "Verify your email to upload PDFs." });
      return;
    }
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
      setDocNotice({ kind: "error", text: "Choose a PDF file." });
      return;
    }
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setDocNotice({ kind: "error", text: `This PDF is larger than ${MAX_UPLOAD_MB} MB.` });
      return;
    }
    setDocUploading(true);
    setDocNotice(null);
    try {
      const res = await fetch(`${apiBaseUrl}/documents`, {
        method: "POST",
        credentials: "include",
        // Header values must be plain ASCII, so accented file names are simplified.
        headers: { "Content-Type": "application/pdf", "X-Filename": file.name.normalize("NFD").replace(/[^\x20-\x7e]/g, "").slice(0, 200) },
        body: file,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The upload failed.");
      setDocNotice({ kind: "ok", text: `Read ${data.pages} page${data.pages === 1 ? "" : "s"}, about ${Number(data.words).toLocaleString()} words.` });
      loadDocuments();
    } catch (err) {
      setDocNotice({ kind: "error", text: errorMessage(err, "The upload failed.") });
    } finally {
      setDocUploading(false);
    }
  }

  async function deleteDocument(doc: DocumentInfo) {
    if (!window.confirm(`Delete "${doc.title}"? Its text is removed from the server.`)) return;
    await fetch(`${apiBaseUrl}/documents/${doc.id}`, { method: "DELETE", credentials: "include" }).catch(() => null);
    setDocSelection((prev) => prev.filter((id) => id !== doc.id));
    loadDocuments();
  }

  function toggleDocSelection(id: number) {
    setDocSelection((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : prev.length >= MAX_DOCS_PER_CHAT ? prev : [...prev, id]));
  }

  function openDocChat(ids: number[]) {
    setDocChatIds(ids);
    setDocMessages([]);
    setDocQuestion("");
    setDocChatError("");
    if (window.innerWidth < 768) setSidebarOpen(false);
  }

  async function askDocuments(e: React.FormEvent) {
    e.preventDefault();
    const question = docQuestion.trim();
    if (!question || !docChatIds || docAsking) return;
    const history: { question: string; answer: string }[] = [];
    for (let i = 0; i < docMessages.length - 1; i += 1) {
      if (docMessages[i].role === "user" && docMessages[i + 1].role === "assistant") {
        history.push({ question: docMessages[i].content.slice(0, 2000), answer: docMessages[i + 1].content.slice(0, 20000) });
      }
    }
    setDocMessages((prev) => [...prev, { id: newMessageId("doc-q"), role: "user", content: question }]);
    setDocQuestion("");
    setDocAsking(true);
    setDocChatError("");
    try {
      const res = await fetch(`${apiBaseUrl}/documents/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ question: question.slice(0, 2000), document_ids: docChatIds, conversation_history: history.slice(-20) }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Could not answer from the documents.");
      setDocMessages((prev) => [
        ...prev,
        { id: newMessageId("doc-a"), role: "assistant", content: data.answer || "", citations: data.citations || [] },
      ]);
    } catch (err) {
      setDocChatError(errorMessage(err, "Could not answer from the documents."));
    } finally {
      setDocAsking(false);
    }
  }

  // ============================================================
  // CITATION MAP (Phase 8)
  // ============================================================

  async function openCitationMap(paper: Result) {
    if (!paper.doi) return;
    setGraphPaper(paper);
    setGraphData(null);
    setGraphError("");
    try {
      const res = await fetch(`${apiBaseUrl}/graph?${new URLSearchParams({ doi: paper.doi }).toString()}`, { credentials: "include" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof data.detail === "string" ? data.detail : "The citation map could not be loaded.");
      setGraphData(data);
    } catch (err) {
      setGraphError(errorMessage(err, "The citation map could not be loaded."));
    }
  }

  function exploreGraphNode(node: GraphNode) {
    if (!node.doi) return;
    setGraphPaper(null);
    setQuery(node.doi);
    executeSearch(node.doi, "quick");
  }

  const visibleBookmarks =
    activeCollection === null ? bookmarks : bookmarks.filter((b) => (b.collection_ids || []).includes(activeCollection));

  function isPaperBookmarked(paper: Result | null): BookmarkItem | undefined {
    if (!paper) return undefined;
    return bookmarks.find((b) => {
      if (paper.doi && b.doi && paper.doi.toLowerCase() === b.doi.toLowerCase()) {
        return true;
      }
      return b.title.trim().toLowerCase() === paper.title.trim().toLowerCase();
    });
  }

  async function toggleBookmark(paper: Result) {
    if (!user) {
      openAuthModal("register", "Create a free account to save papers to your library.");
      return;
    }

    const existing = isPaperBookmarked(paper);

    if (existing) {
      try {
        const res = await fetch(`${apiBaseUrl}/bookmarks/${existing.id}`, {
          method: "DELETE",
          credentials: "include",
        });
        if (res.ok) {
          setBookmarks((prev) => prev.filter((b) => b.id !== existing.id));
        }
      } catch {
        // ignore
      }
    } else {
      try {
        const res = await fetch(`${apiBaseUrl}/bookmarks`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            title: paper.title,
            authors: paper.authors,
            abstract: paper.abstract,
            publication_year: paper.publication_year,
            source: paper.description,
            doi: paper.doi,
          }),
        });
        if (res.ok) {
          const data = await res.json();
          setBookmarks((prev) => [data, ...prev]);
        }
      } catch {
        // ignore
      }
    }
  }

  async function deleteBookmarkById(bookmarkId: number) {
    try {
      const res = await fetch(`${apiBaseUrl}/bookmarks/${bookmarkId}`, {
        method: "DELETE",
        credentials: "include",
      });
      if (res.ok) {
        setBookmarks((prev) => prev.filter((b) => b.id !== bookmarkId));
      }
    } catch {
      // ignore
    }
  }

  // ============================================================
  // HISTORY
  // ============================================================

  async function loadHistory() {
    const sid = getSessionId();
    setHistoryLoading(true);
    try {
      const res = await fetch(`${apiBaseUrl}/history/${encodeURIComponent(sid)}`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setHistory(data.history || []);
      }
    } catch {
      // ignore
    } finally {
      setHistoryLoading(false);
    }
  }

  function handleSelectHistoryItem(item: HistoryItem) {
    setCurrentThreadId(null);
    setSavedTurnCount(0);
    setThreadNotice("");
    setQuery(item.question);
    if (item.papers && item.papers.length > 0) {
      showResults(item.papers);
    }
    const restoredMessages: Message[] = [
      {
        id: `hist-q-${item.id}`,
        role: "user",
        content: item.question,
        timestamp: new Date(item.created_at).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        }),
      },
      {
        id: `hist-a-${item.id}`,
        role: "assistant",
        content: item.answer,
        citations: item.citations || [],
        timestamp: new Date(item.created_at).toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        }),
      },
    ];
    setMessages(restoredMessages);
    setSelectedPaperForDetail(null);
    if (window.innerWidth < 768) {
      setSidebarOpen(false);
    }
  }

  async function handleDeleteHistoryItem(e: React.MouseEvent, item: HistoryItem) {
    e.stopPropagation();
    try {
      // Delete just this entry (the old call cleared the whole session / account history).
      const res = await fetch(
        `${apiBaseUrl}/history/entry/${item.id}?session_id=${encodeURIComponent(item.session_id)}`,
        { method: "DELETE", credentials: "include" },
      );
      if (res.ok) {
        setHistory((prev) => prev.filter((h) => h.id !== item.id));
      }
    } catch {
      // ignore
    }
  }

  async function handleClearAllHistory() {
    setClearingHistory(true);
    try {
      // Signed in: clears the whole account history. Guest: clears this browser's session.
      const res = await fetch(`${apiBaseUrl}/history/${encodeURIComponent(getSessionId())}`, {
        method: "DELETE",
        credentials: "include",
      });
      if (res.ok) setHistory([]);
    } catch {
      // ignore
    } finally {
      setClearingHistory(false);
      setConfirmClearHistory(false);
    }
  }

  // Page load: cookie choice, theme, sign-in and email links. Declared after the functions it calls.
  useEffect(() => {
    // 0. Cookie consent: show the banner until a choice has been stored
    try {
      const rawConsent = localStorage.getItem("scholarpulse-cookie-consent");
      if (rawConsent) {
        const stored = JSON.parse(rawConsent);
        // Saved choices live in localStorage, which only exists in the browser, so they are read after mount.
        setCookiePrefs({ essential: true, preferences: Boolean(stored?.preferences) });
        setCookieDecided(true);
      } else {
        setCookieDecided(false);
      }
    } catch {
      setCookieDecided(false);
    }

    // 1. Load Theme
    const savedTheme = localStorage.getItem("researchai-theme") as "light" | "dark" | null;
    if (savedTheme) {
      setTheme(savedTheme);
      document.documentElement.setAttribute("data-theme", savedTheme);
      if (savedTheme === "dark") {
        document.documentElement.classList.add("dark");
      } else {
        document.documentElement.classList.remove("dark");
      }
    }

    // 2. (Sidebar always starts closed; nothing to restore.)

    // 3. Load Guest Count
    const savedCount = parseInt(
      localStorage.getItem("researchai-guest-searches") || "0",
      10
    );
    setGuestSearchCount(isNaN(savedCount) ? 0 : savedCount);

    // 4. Authenticate via httpOnly Cookie
    fetchCurrentUser();

    // 5. Handle URL Tokens (Email Verification / Password Reset)
    const urlParams = new URLSearchParams(window.location.search);
    const vToken = urlParams.get("verify_token");
    const rToken = urlParams.get("reset_token");

    if (vToken) {
      handleVerifyEmailToken(vToken);
    }
    if (rToken) {
      setResetToken(rToken);
      setResetPasswordModalOpen(true);
    }

    // Click outside listener for account dropdown
    function handleClickOutside(event: MouseEvent) {
      if (accountMenuRef.current && !accountMenuRef.current.contains(event.target as Node)) {
        setAccountDropdownOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
    // Runs once on page load on purpose (email verification and reset links). Listing the handlers as
    // dependencies would re-run it on every render and repeat the verification request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ============================================================
  // SEARCH & QUESTION WORKFLOW
  // ============================================================

  function handleNewSearch() {
    setCurrentThreadId(null);
    setSavedTurnCount(0);
    setThreadNotice("");
    setQuery("");
    setFollowUpQuery("");
    setMessages([]);
    showResults([]);
    setError("");
    setSelectedPaperForDetail(null);
    if (window.innerWidth < 768) {
      setSidebarOpen(false);
    }
  }

  // Results change in one place, so the visible count and ticked papers always reset together.
  function showResults(list: Result[]) {
    setResults(list);
    setVisiblePapers(10);
    setSelectedPaperIds([]);
  }

  function currentFilters() {
    const currentYear = new Date().getFullYear();
    let yearMin: number | undefined;
    if (yearFilter === "3") yearMin = currentYear - 2;
    if (yearFilter === "5") yearMin = currentYear - 4;
    if (yearFilter === "10") yearMin = currentYear - 9;
    return {
      year_min: yearMin,
      open_access_only: openAccessOnly,
      study_type: studyTypeFilter || undefined,
      field: fieldFilter || undefined,
      min_citations: minCitations ? Number(minCitations) : undefined,
      exclude_preprints: excludePreprints,
    };
  }

  async function pickMode(question: string, forced?: AnswerMode): Promise<AnswerMode> {
    if (forced) return forced;
    if (researchMode !== "auto") return researchMode;
    const q = question.trim();
    // Author, title and DOI lookups are not research questions: answer them quickly.
    if (searchBy !== "topic" || /^(author|title|doi)\s*:/i.test(q) || /^(https?:\/\/(dx\.)?doi\.org\/)?10\.\d{4,9}\//i.test(q)) {
      return "quick";
    }
    try {
      const res = await fetch(`${apiBaseUrl}/mode?${new URLSearchParams({ q: q.slice(0, 2000) }).toString()}`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        if (data.mode === "deep" || data.mode === "medical" || data.mode === "quick") return data.mode;
      }
    } catch {
      // fall back to a quick answer
    }
    return "quick";
  }

  async function runDeepResearch(question: string, medical: boolean | null) {
    setLoading(true);
    setError("");
    const steps = [
      "Planning 3 to 5 searches...",
      "Searching 7 academic databases...",
      "Merging and ranking the papers...",
      "Reading the top abstracts...",
      "Writing the structured review...",
    ];
    let step = 0;
    setLoadingStep(steps[0]);
    const timer = setInterval(() => {
      step = Math.min(step + 1, steps.length - 1);
      setLoadingStep(steps[step]);
    }, 6000);

    try {
      const res = await fetch(`${apiBaseUrl}/deep-research`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          question: question.slice(0, 2000),
          session_id: getSessionId(),
          medical,
          ...currentFilters(),
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        const detail = typeof errData.detail === "string" ? errData.detail : "Deep research failed.";
        if (res.status === 429 && /deep research limit/i.test(detail)) {
          setMessages((prev) => [
            ...prev,
            { id: newMessageId("deep-limit"), role: "assistant", content: detail, timestamp: now(), question, offerQuick: true },
          ]);
          return;
        }
        throw new Error(detail);
      }

      const data = await res.json();
      const list: Result[] = Array.isArray(data.results) ? data.results : [];
      showResults(list);
      setSelectedPaperForDetail(null);
      rememberTakeaways(data.takeaways, list);
      setActiveMode(data.medical ? "medical" : "deep");
      const subQueries: string[] = Array.isArray(data.sub_queries) ? data.sub_queries : [];
      setMessages((prev) => [
        ...prev,
        {
          id: newMessageId("deep"),
          role: "assistant",
          content: data.answer || "Deep research complete.",
          citations: data.citations || [],
          timestamp: now(),
          mode: "deep",
          modeLabel: `Deep research${data.medical ? " · medical" : ""} · ${subQueries.length} search${
            subQueries.length === 1 ? "" : "es"
          } · ${list.length} papers`,
          subQueries,
          meter: data.meter || null,
          question,
        },
      ]);
      loadHistory();
    } catch (err) {
      setError(errorMessage(err, "Deep research failed. Please try again."));
    } finally {
      clearInterval(timer);
      setLoading(false);
      setLoadingStep("");
    }
  }

  function handleGoDeeper(m: Message) {
    if (!m.question || loading) return;
    setMessages((prev) => [...prev, { id: newMessageId("go-deeper"), role: "user", content: `Go deeper: ${m.question}`, timestamp: now() }]);
    runDeepResearch(m.question, m.mode === "medical" ? true : null);
  }

  function handleQuickFallback(m: Message) {
    if (!m.question || loading) return;
    executeSearch(m.question, "quick");
  }

  async function executeSearch(searchQuery: string, forcedMode?: AnswerMode) {
    if (!searchQuery.trim()) return;

    if (!user) {
      const nextCount = guestSearchCount + 1;
      setGuestSearchCount(nextCount);
      localStorage.setItem("researchai-guest-searches", nextCount.toString());
      if (nextCount > GUEST_SEARCH_LIMIT) {
        setSoftGateModalOpen(true);
      }
    }

    setLoading(true);
    setError("");
    showResults([]);
    setSelectedPaperForDetail(null);
    setCurrentThreadId(null);
    setSavedTurnCount(0);
    setThreadNotice("");
    setLoadingStep("Choosing how to research this...");

    const userMessage: Message = {
      id: newMessageId(),
      role: "user",
      content: searchQuery,
      timestamp: now(),
    };
    setMessages([userMessage]);

    try {
      const mode = await pickMode(searchQuery, forcedMode);
      setActiveMode(mode);
      if (mode === "deep") {
        await runDeepResearch(searchQuery, null);
        return;
      }

      setLoadingStep("Searching 11 academic databases, including PubMed, DOAJ and African Journals Online...");
      const filters = currentFilters();
      const searchParams = new URLSearchParams({
        q: searchQuery,
        open_access_only: filters.open_access_only.toString(),
      });
      if (filters.year_min) searchParams.append("year_min", filters.year_min.toString());
      if (filters.study_type) searchParams.append("study_type", filters.study_type);
      if (filters.field) searchParams.append("field", filters.field);
      if (filters.min_citations) searchParams.append("min_citations", filters.min_citations.toString());
      if (filters.exclude_preprints) searchParams.append("exclude_preprints", "true");
      if (sortBy !== "relevance") searchParams.append("sort", sortBy);
      if (searchBy !== "topic") searchParams.append("search_by", searchBy);
      if (mode === "medical") searchParams.append("medical", "true");

      const searchRes = await fetch(`${apiBaseUrl}/search?${searchParams.toString()}`, {
        credentials: "include",
      });
      if (!searchRes.ok) {
        const errData = await searchRes.json().catch(() => ({}));
        throw new Error(typeof errData.detail === "string" ? errData.detail : "Paper search failed.");
      }

      const searchData = await searchRes.json();
      const fetchedResults: Result[] = searchData.results || [];
      showResults(fetchedResults);

      if (fetchedResults.length === 0) {
        setMessages((prev) => [
          ...prev,
          {
            id: newMessageId(),
            role: "assistant",
            content: "No matching academic papers found in any of the 11 databases. Try adjusting your search query or filters.",
            timestamp: now(),
          },
        ]);
        return;
      }

      setLoadingStep("Synthesizing multi-source literature answer with citations...");

      const sid = getSessionId();
      const askRes = await fetch(`${apiBaseUrl}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          question: searchQuery,
          results: fetchedResults,
          session_id: sid,
          conversation_history: [],
          mode: mode === "medical" ? "medical" : "quick",
        }),
      });

      if (!askRes.ok) {
        const errData = await askRes.json().catch(() => ({}));
        throw new Error(typeof errData.detail === "string" ? errData.detail : "AI synthesis failed.");
      }

      const askData = await askRes.json();
      rememberTakeaways(askData.takeaways, fetchedResults);
      const assistantMessage: Message = {
        id: newMessageId(),
        role: "assistant",
        content: askData.answer || "Synthesized analysis completed.",
        citations: askData.citations || [],
        timestamp: now(),
        mode,
        modeLabel:
          mode === "medical"
            ? `Medical · strongest evidence first · ${fetchedResults.length} papers`
            : `Quick answer · ${fetchedResults.length} papers`,
        meter: askData.meter || null,
        question: searchQuery,
      };

      setMessages((prev) => [...prev, assistantMessage]);
      loadHistory();
    } catch (err) {
      setError(errorMessage(err, "An unexpected error occurred."));
    } finally {
      setLoading(false);
      setLoadingStep("");
    }
  }

  async function handleFollowUpSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!followUpQuery.trim() || loading) return;

    const followUpText = followUpQuery.trim();
    setFollowUpQuery("");

    if (results.length === 0) {
      executeSearch(followUpText);
      return;
    }

    setLoading(true);
    setError("");
    setLoadingStep("Synthesizing follow-up research answer...");

    const userMessage: Message = {
      id: newMessageId(),
      role: "user",
      content: followUpText,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      scopedPaperIds: selectedPaperIds.length > 0 ? [...selectedPaperIds] : undefined,
    };
    setMessages((prev) => [...prev, userMessage]);

    try {
      const historyTurns = [];
      for (let i = 0; i < messages.length; i += 2) {
        if (messages[i]?.role === "user" && messages[i + 1]?.role === "assistant") {
          historyTurns.push({
            question: messages[i].content,
            answer: messages[i + 1].content,
          });
        }
      }

      const papersToPass =
        selectedPaperIds.length > 0
          ? results.filter((_, idx) => selectedPaperIds.includes(idx + 1))
          : results;

      const sid = getSessionId();
      const askRes = await fetch(`${apiBaseUrl}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          question: followUpText,
          results: papersToPass,
          session_id: sid,
          conversation_history: historyTurns,
          mode: activeMode === "medical" ? "medical" : "quick",
        }),
      });

      if (!askRes.ok) {
        const errData = await askRes.json().catch(() => ({}));
        throw new Error(typeof errData.detail === "string" ? errData.detail : "Follow-up synthesis failed.");
      }

      const askData = await askRes.json();
      rememberTakeaways(askData.takeaways, papersToPass);
      const assistantMessage: Message = {
        id: newMessageId(),
        role: "assistant",
        content: askData.answer || "Synthesis complete.",
        citations: askData.citations || [],
        timestamp: now(),
        scopedPaperIds: selectedPaperIds.length > 0 ? [...selectedPaperIds] : undefined,
        mode: activeMode === "medical" ? "medical" : "quick",
        meter: toResultIds(askData.meter, papersToPass, results),
        question: followUpText,
      };

      setMessages((prev) => [...prev, assistantMessage]);
      loadHistory();
    } catch (err) {
      setError(errorMessage(err, "Failed to process follow-up question."));
    } finally {
      setLoading(false);
      setLoadingStep("");
    }
  }

  async function handleDeepDive(index: number, paper: Result) {
    const key = paperKey(paper);
    if (deepDives[key]) return;
    setDeepDiveLoading((prev) => ({ ...prev, [index]: true }));
    setDeepDiveErrors((prev) => ({ ...prev, [key]: "" }));

    try {
      const res = await fetch(`${apiBaseUrl}/deep-dive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          title: paper.title,
          abstract: paper.abstract || paper.description || "No abstract available.",
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(typeof errData.detail === "string" ? errData.detail : "Unable to analyze this paper right now.");
      }
      const data = await res.json();
      setDeepDives((prev) => ({
        ...prev,
        [key]: { text: data.deep_dive || "Analysis complete.", glance: data.glance || null },
      }));
    } catch (err) {
      // Not stored as an analysis, so the button can be pressed again.
      setDeepDiveErrors((prev) => ({ ...prev, [key]: errorMessage(err, "Unable to analyze this paper right now.") }));
    } finally {
      setDeepDiveLoading((prev) => ({ ...prev, [index]: false }));
    }
  }

  // ------------------------------------------------------------
  // Citations: copy one reference, or download RIS / BibTeX files
  // ------------------------------------------------------------

  async function copyCitation(paper: Result, style: CitationStyle | "bibtex") {
    const plain = style === "bibtex" ? buildBibtex(paper) : citationText(paper, style);
    try {
      // Rich text keeps the journal name in italics when pasted into Word or Google Docs.
      if (style !== "bibtex" && typeof ClipboardItem !== "undefined" && navigator.clipboard.write) {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/plain": new Blob([plain], { type: "text/plain" }),
            "text/html": new Blob([citationHtml(paper, style)], { type: "text/html" }),
          }),
        ]);
      } else {
        await navigator.clipboard.writeText(plain);
      }
      setCiteCopied(true);
      setTimeout(() => setCiteCopied(false), 2000);
    } catch {
      window.prompt("Copy this reference:", plain);
    }
  }

  function exportReferences(papers: CitablePaper[], format: "ris" | "bibtex", name: string) {
    if (papers.length === 0) return;
    if (format === "ris") {
      downloadTextFile(`${fileSlug(name, "references")}.ris`, buildRisFile(papers), "application/x-research-info-systems");
    } else {
      downloadTextFile(`${fileSlug(name, "references")}.bib`, buildBibtexFile(papers), "application/x-bibtex");
    }
  }

  function bookmarkAsPaper(b: BookmarkItem): CitablePaper {
    return {
      title: b.title,
      authors: b.authors || [],
      description: b.source || "",
      publication_year: b.publication_year,
      doi: b.doi,
      abstract: b.abstract,
    };
  }

  function handleCopyAnswer(message: Message) {
    navigator.clipboard.writeText(message.content);
    setCopiedAnswerId(message.id);
    setTimeout(() => setCopiedAnswerId(null), 2500);
  }

  async function handleExportPDF(message: Message) {
    // The question is the user message right before this answer (the search box may have changed since).
    const at = messages.findIndex((m) => m.id === message.id);
    const asked = messages.slice(0, at === -1 ? messages.length : at).reverse().find((m) => m.role === "user");
    try {
      const { exportSynthesisPdf } = await import("./pdf");
      exportSynthesisPdf({
        appName: APP_NAME,
        question: asked?.content || query,
        answer: message.content,
        citations: message.citations || [],
        papers: results,
        scopedCount: message.scopedPaperIds?.length,
      });
    } catch (err) {
      console.error("PDF export failed", err);
      setError("Could not create the PDF. Please try again.");
    }
  }

  async function handleExportPaperPDF(index: number, paper: Result) {
    try {
      const { exportPaperPdf } = await import("./pdf");
      const analysis = deepDives[paperKey(paper)];
      exportPaperPdf({ appName: APP_NAME, paper, deepDive: analysis?.text, glance: analysis?.glance });
    } catch (err) {
      console.error("PDF export failed", err);
      setError("Could not create the PDF. Please try again.");
    }
  }

  function togglePaperSelection(id: number) {
    setSelectedPaperIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  }

  // ------------------------------------------------------------
  // Takeaways (from /ask for free, or POST /takeaways in batches of 10)
  // ------------------------------------------------------------

  // A key holding "" means "asked, but no takeaway came back", so it is not requested again.
  function rememberTakeaways(items: TakeawayItem[] | undefined, papers: Result[], attempted: Result[] = []) {
    const list = Array.isArray(items) ? items : [];
    if (list.length === 0 && attempted.length === 0) return;
    setTakeaways((prev) => {
      const next = { ...prev };
      for (const paper of attempted) {
        if (!(paperKey(paper) in next)) next[paperKey(paper)] = "";
      }
      for (const item of list) {
        const paper = papers[item.paper_id - 1];
        if (paper && typeof item.takeaway === "string" && item.takeaway.trim()) {
          next[paperKey(paper)] = item.takeaway.trim();
        }
      }
      return next;
    });
  }

  const papersMissingTakeaways = results
    .map((paper, idx) => ({ paper, id: idx + 1 }))
    .filter(({ paper }) => hasEvidence(paper) && !(paperKey(paper) in takeaways));

  async function handleGetTakeaways() {
    if (takeawaysLoading) return;
    const batch = papersMissingTakeaways.slice(0, 10);
    if (batch.length === 0) return;
    setTakeawaysLoading(true);
    setTakeawaysError("");
    try {
      const res = await fetch(`${apiBaseUrl}/takeaways`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          papers: batch.map(({ paper, id }) => ({
            paper_id: id,
            title: (paper.title || "Untitled").slice(0, 500),
            abstract: (paper.abstract || "").slice(0, 10000),
            publication_year: paper.publication_year,
            is_retracted: Boolean(paper.is_retracted),
          })),
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(typeof errData.detail === "string" ? errData.detail : "Could not get takeaways.");
      }
      const data = await res.json();
      rememberTakeaways(data.takeaways, results, batch.map(({ paper }) => paper));
    } catch (err) {
      setTakeawaysError(err instanceof Error ? err.message : "Could not get takeaways.");
    } finally {
      setTakeawaysLoading(false);
    }
  }

  // ------------------------------------------------------------
  // Compare 2 to 5 selected papers (POST /compare)
  // ------------------------------------------------------------

  const selectedForCompare = selectedPaperIds.filter((id) => id >= 1 && id <= results.length).sort((a, b) => a - b);
  const canCompare = selectedForCompare.length >= 2 && selectedForCompare.length <= 5;

  async function handleCompare() {
    if (!canCompare || compareLoading) return;
    setCompareOpen(true);
    setCompareLoading(true);
    setCompareError("");
    setComparison(null);
    setCompareCopied(false);
    try {
      const res = await fetch(`${apiBaseUrl}/compare`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          papers: selectedForCompare.map((id) => {
            const paper = results[id - 1];
            return {
              paper_id: id,
              title: (paper.title || "Untitled").slice(0, 500),
              abstract: (paper.abstract || "").slice(0, 10000),
              publication_year: paper.publication_year,
              is_retracted: Boolean(paper.is_retracted),
            };
          }),
        }),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(typeof errData.detail === "string" ? errData.detail : "Could not compare these papers.");
      }
      const data = await res.json();
      setComparison({ summary: String(data.summary || ""), rows: Array.isArray(data.rows) ? data.rows : [] });
    } catch (err) {
      setCompareError(err instanceof Error ? err.message : "Could not compare these papers.");
    } finally {
      setCompareLoading(false);
    }
  }

  async function handleCopyComparison() {
    if (!comparison) return;
    try {
      const { comparisonMarkdown } = await import("./pdf");
      await navigator.clipboard.writeText(`${comparison.summary}\n\n${comparisonMarkdown(comparison.rows)}`);
      setCompareCopied(true);
      setTimeout(() => setCompareCopied(false), 2000);
    } catch {
      setCompareError("Could not copy to the clipboard.");
    }
  }

  async function handleExportComparisonPdf() {
    if (!comparison) return;
    try {
      const { exportComparisonPdf } = await import("./pdf");
      exportComparisonPdf({ appName: APP_NAME, summary: comparison.summary, rows: comparison.rows });
    } catch {
      setCompareError("Could not create the PDF. Please try again.");
    }
  }

  // ------------------------------------------------------------
  // Table view (sorted in the browser) and CSV export
  // ------------------------------------------------------------

  const sortedTableRows = results
    .map((paper, idx) => ({ paper, id: idx + 1 }))
    .sort((a, b) => {
      const dir = tableSort.dir === "asc" ? 1 : -1;
      switch (tableSort.key) {
        case "title":
          return dir * (a.paper.title || "").localeCompare(b.paper.title || "");
        case "year":
          return dir * ((a.paper.publication_year ?? -1) - (b.paper.publication_year ?? -1));
        case "citations":
          return dir * ((a.paper.cited_by_count ?? -1) - (b.paper.cited_by_count ?? -1));
        default:
          return dir * (a.id - b.id);
      }
    });

  function toggleTableSort(key: TableSortKey) {
    setTableSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === "asc" ? "desc" : "asc" }
        : { key, dir: key === "title" || key === "rank" ? "asc" : "desc" }
    );
  }

  function handleExportCsv() {
    const yesNo = (v: boolean | null | undefined) => (v === true ? "yes" : v === false ? "no" : "unknown");
    const header = [
      "#", "Title", "Authors", "Year", "Published in", "Database", "DOI", "Citations",
      "Study type", "Open access", "Preprint", "Retracted", "Field", "Takeaway",
    ];
    const rows = sortedTableRows.map(({ paper, id }) => [
      id,
      paper.title,
      paper.authors.join("; "),
      paper.publication_year ?? "",
      paper.description,
      paper.database ?? "",
      paper.doi ? doiUrl(paper.doi) : "",
      paper.cited_by_count ?? "",
      (paper.study_types || []).map((t) => STUDY_TYPE_LABELS[t] || t).join("; "),
      yesNo(paper.is_open_access),
      yesNo(paper.is_preprint),
      paper.is_retracted ? "yes" : "no",
      paper.field ?? "",
      takeaways[paperKey(paper)] || "",
    ]);
    downloadCsv(query || "papers", buildCsv(header, rows));
  }

  // Esc closes the compare overlay first, then the table.
  useEffect(() => {
    if (!tableOpen && !compareOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      if (compareOpen) setCompareOpen(false);
      else setTableOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [tableOpen, compareOpen]);

  const displayName = getDisplayName(user);
  const userInitials = getInitials(user);

  // ============================================================
  // RENDER
  // ============================================================

  return (
    <div className="flex h-screen w-full overflow-hidden bg-[var(--bg-primary)] text-[var(--text-primary)]">
      {/* Mobile Sidebar Backdrop Overlay */}
      <AnimatePresence>
        {sidebarOpen && !isDesktop && (
          <motion.div
            key="sidebar-scrim"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={() => setSidebarOpen(false)}
            className="fixed inset-0 z-40 bg-black/50 backdrop-blur-xs"
          />
        )}
      </AnimatePresence>

      {/* ======================================================== */}
      {/* 1. PERSISTENT & MOBILE SLIDE-OVER LEFT SIDEBAR */}
      {/* ======================================================== */}
      <motion.aside
        key={isDesktop ? "sidebar-desktop" : "sidebar-mobile"}
        initial={false}
        inert={!sidebarOpen}
        style={{ x: sidebarX }}
        animate={{ width: isDesktop ? (sidebarOpen ? 288 : 0) : 288 }}
        transition={reduceMotion ? { duration: 0.15 } : SPRING}
        drag={!isDesktop && sidebarOpen ? "x" : false}
        dragDirectionLock
        dragConstraints={{ left: -288, right: 0 }}
        dragElastic={{ left: 0.08, right: 0 }}
        dragMomentum={false}
        onDragEnd={(_, info) => {
          // Decide from where a flick would come to rest, not from where the finger lifted.
          const rest = info.offset.x + projectMomentum(info.velocity.x);
          sidebarReleaseVelocity.current = info.velocity.x;
          if (rest < -144) {
            setSidebarOpen(false);
          } else {
            animate(sidebarX, 0, reduceMotion ? { duration: 0.15 } : { ...SPRING, velocity: info.velocity.x });
          }
        }}
        className={`sp-sidebar flex flex-col bg-[var(--bg-surface)] z-50 overflow-hidden fixed inset-y-0 left-0 md:relative md:inset-auto ${
          sidebarOpen
            ? "border-r border-[var(--border-color)] shadow-2xl md:shadow-none"
            : ""
        }`}
      >
        <div className="flex h-full w-72 flex-col">
        {/* Sidebar Header: Brand & New Search */}
        <div className="p-4 border-b border-[var(--border-color)] flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BookOpen className="w-5 h-5 text-[var(--accent)]" />
              <span className="font-serif-heading text-lg font-bold tracking-tight text-[var(--text-primary)]">
                {APP_NAME}
              </span>
            </div>
            <button
              onClick={toggleSidebar}
              className="p-1.5 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
              title="Collapse sidebar"
            >
              <PanelLeftClose className="w-4 h-4" />
            </button>
          </div>

          <button
            onClick={handleNewSearch}
            className="flex items-center justify-center gap-2 w-full py-2.5 px-4 rounded-xl bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white font-medium text-sm transition shadow-sm"
          >
            <Plus className="w-4 h-4" />
            <span>New search</span>
          </button>
        </div>

        {/* Verification Alert Banner if Unverified */}
        {user && !user.is_verified && (
          <div className="mx-3 mt-3 p-2.5 rounded-xl bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-900/50 text-xs text-amber-800 dark:text-amber-300 space-y-1">
            <div className="flex items-center gap-1.5 font-semibold">
              <Mail className="w-3.5 h-3.5" />
              <span>Email not verified</span>
            </div>
            <p className="text-[11px] leading-tight">Check your inbox to verify your account.</p>
            <button
              onClick={handleResendVerification}
              className="text-[11px] font-semibold underline hover:opacity-80"
            >
              Resend verification email
            </button>
          </div>
        )}

        {/* Sidebar Scrollable Body: History & My Library */}
        <div className="flex-1 overflow-y-auto px-3 py-4 space-y-5">
          {/* History Accordion */}
          <div>
            <button
              onClick={() => setHistorySectionOpen(!historySectionOpen)}
              className="flex items-center justify-between w-full px-2 py-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
            >
              <div className="flex items-center gap-2">
                <History className="w-3.5 h-3.5 text-[var(--accent)]" />
                <span>Search History</span>
              </div>
              <ChevronDown
                className={`w-3.5 h-3.5 transition-transform duration-200 ${
                  historySectionOpen ? "" : "-rotate-90"
                }`}
              />
            </button>

            {historySectionOpen && (
              <div className="mt-2 space-y-1">
                {historyLoading ? (
                  <div className="text-xs text-[var(--text-muted)] px-2 py-2">Loading history...</div>
                ) : history.length === 0 ? (
                  <div className="text-xs text-[var(--text-muted)] px-2 py-2 italic">
                    No recent searches yet.
                  </div>
                ) : (
                  history.slice(0, 15).map((item) => (
                    <div
                      key={item.id}
                      onClick={() => handleSelectHistoryItem(item)}
                      className="group flex items-center justify-between gap-2 px-2.5 py-2 rounded-lg text-xs hover:bg-[var(--bg-surface-elevated)] cursor-pointer transition text-[var(--text-primary)]"
                    >
                      <div className="flex items-center gap-2 truncate">
                        <Clock className="w-3 h-3 text-[var(--text-muted)] shrink-0" />
                        <span className="truncate">{item.question}</span>
                      </div>
                      <button
                        onClick={(e) => handleDeleteHistoryItem(e, item)}
                        className="opacity-0 group-hover:opacity-100 p-1 hover:text-[var(--accent)] transition"
                        title="Delete search"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  ))
                )}

                {!historyLoading && history.length > 0 && (
                  <div className="px-2 pt-2">
                    {confirmClearHistory ? (
                      <div className="rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface-elevated)] p-2.5 space-y-2">
                        <p className="text-xs text-[var(--text-primary)]">
                          Delete all {history.length} saved search{history.length === 1 ? "" : "es"}? This cannot be undone.
                        </p>
                        <div className="flex gap-2">
                          <button
                            onClick={handleClearAllHistory}
                            disabled={clearingHistory}
                            className="flex-1 px-2 py-1.5 rounded-md bg-red-600 hover:bg-red-700 disabled:opacity-60 text-white text-xs font-medium transition"
                          >
                            {clearingHistory ? "Deleting..." : "Delete all"}
                          </button>
                          <button
                            onClick={() => setConfirmClearHistory(false)}
                            disabled={clearingHistory}
                            className="flex-1 px-2 py-1.5 rounded-md border border-[var(--border-color)] text-xs text-[var(--text-primary)] hover:bg-[var(--bg-surface)] transition"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <button
                        onClick={() => setConfirmClearHistory(true)}
                        className="flex items-center gap-1.5 text-[11px] text-[var(--text-muted)] hover:text-red-600 transition"
                      >
                        <Trash2 className="w-3 h-3" />
                        <span>Clear all history</span>
                      </button>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* My Documents Accordion */}
          <div>
            <button
              onClick={() => setDocsSectionOpen(!docsSectionOpen)}
              className="flex items-center justify-between w-full px-2 py-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
            >
              <div className="flex items-center gap-2">
                <FileText className="w-3.5 h-3.5 text-[var(--accent)]" />
                <span>My Documents</span>
                <span className="text-[10px] bg-[var(--bg-surface-elevated)] px-1.5 py-0.5 rounded-full text-[var(--text-secondary)]">
                  {documents.length}
                </span>
              </div>
              <ChevronDown className={`w-3.5 h-3.5 transition-transform duration-200 ${docsSectionOpen ? "" : "-rotate-90"}`} />
            </button>
            {docsSectionOpen && (
              <div className="mt-2 space-y-1">
                <div className="px-2 flex flex-wrap items-center gap-2">
                  <label
                    className={`px-2.5 py-1.5 rounded-lg border border-[var(--border-color)] text-[11px] font-medium flex items-center gap-1.5 transition cursor-pointer ${
                      docUploading ? "opacity-60 pointer-events-none" : "hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)]"
                    }`}
                  >
                    <Upload className="w-3.5 h-3.5" />
                    <span>{docUploading ? "Reading PDF..." : "Upload PDF"}</span>
                    <input
                      type="file"
                      accept="application/pdf,.pdf"
                      className="sr-only"
                      disabled={docUploading}
                      onChange={(e) => {
                        const file = e.target.files?.[0];
                        e.target.value = "";
                        if (file) uploadDocument(file);
                      }}
                    />
                  </label>
                  {docSelection.length > 0 && (
                    <button
                      onClick={() => openDocChat(docSelection)}
                      className="px-2.5 py-1.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-[11px] font-semibold"
                    >
                      Chat with {docSelection.length}
                    </button>
                  )}
                </div>
                {docNotice && (
                  <div className={`px-2 text-[11px] ${docNotice.kind === "ok" ? "text-emerald-600" : "text-red-600"}`}>{docNotice.text}</div>
                )}
                {documents.length === 0 ? (
                  <div className="text-xs text-[var(--text-muted)] px-2 py-1 italic">
                    {user ? `Upload a PDF (up to ${MAX_UPLOAD_MB} MB) to chat with its full text.` : "Sign in to upload PDFs."}
                  </div>
                ) : (
                  documents.map((doc) => {
                    const ticked = docSelection.includes(doc.id);
                    return (
                      <div
                        key={doc.id}
                        onClick={() => openDocChat([doc.id])}
                        className="group flex items-center justify-between gap-1 px-2.5 py-2 rounded-lg text-xs cursor-pointer hover:bg-[var(--bg-surface-elevated)] text-[var(--text-primary)] transition"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleDocSelection(doc.id);
                            }}
                            className="text-[var(--accent)] shrink-0"
                            aria-label={ticked ? `Unselect ${doc.title}` : `Select ${doc.title}`}
                            title={`Tick up to ${MAX_DOCS_PER_CHAT} to chat with several at once`}
                          >
                            {ticked ? <CheckSquare className="w-3.5 h-3.5" /> : <Square className="w-3.5 h-3.5 text-[var(--text-muted)]" />}
                          </button>
                          <div className="min-w-0">
                            <div className="truncate">{doc.title}</div>
                            <div className="text-[10px] text-[var(--text-muted)]">
                              {doc.pages} pages · {doc.words.toLocaleString()} words
                            </div>
                          </div>
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            deleteDocument(doc);
                          }}
                          className="opacity-100 md:opacity-0 md:group-hover:opacity-100 p-1 hover:text-red-600 transition shrink-0"
                          aria-label={`Delete ${doc.title}`}
                          title="Delete"
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    );
                  })
                )}
              </div>
            )}
          </div>

          {/* Saved Conversations Accordion */}
          <div>
            <button
              onClick={() => setThreadsSectionOpen(!threadsSectionOpen)}
              className="flex items-center justify-between w-full px-2 py-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
            >
              <div className="flex items-center gap-2">
                <MessageSquare className="w-3.5 h-3.5 text-[var(--accent)]" />
                <span>Saved Conversations</span>
                <span className="text-[10px] bg-[var(--bg-surface-elevated)] px-1.5 py-0.5 rounded-full text-[var(--text-secondary)]">
                  {threads.length}
                </span>
              </div>
              <ChevronDown
                className={`w-3.5 h-3.5 transition-transform duration-200 ${threadsSectionOpen ? "" : "-rotate-90"}`}
              />
            </button>
            {threadsSectionOpen && (
              <div className="mt-2 space-y-1">
                {threads.length === 0 ? (
                  <div className="text-xs text-[var(--text-muted)] px-2 py-2 italic">
                    {user ? "Use \"Save conversation\" above an answer to keep it here." : "Sign in to save conversations."}
                  </div>
                ) : (
                  threads.map((t) => (
                    <div
                      key={t.id}
                      onClick={() => openThread(t)}
                      className={`group flex items-center justify-between gap-1 px-2.5 py-2 rounded-lg text-xs cursor-pointer transition ${
                        currentThreadId === t.id
                          ? "bg-[var(--accent-light)] text-[var(--accent)]"
                          : "hover:bg-[var(--bg-surface-elevated)] text-[var(--text-primary)]"
                      }`}
                    >
                      <div className="flex items-center gap-2 truncate">
                        {t.shared ? (
                          <Link2 className="w-3 h-3 text-[var(--accent)] shrink-0" aria-label="Shared" />
                        ) : (
                          <MessageSquare className="w-3 h-3 text-[var(--text-muted)] shrink-0" />
                        )}
                        <span className="truncate">{t.title}</span>
                      </div>
                      <div className="flex items-center shrink-0 opacity-100 md:opacity-0 md:group-hover:opacity-100 transition">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            setShareCopied(false);
                            setShareTarget(t);
                          }}
                          className="p-1 hover:text-[var(--accent)]"
                          title="Share"
                          aria-label={`Share ${t.title}`}
                        >
                          <Share2 className="w-3 h-3" />
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            renameThread(t);
                          }}
                          className="p-1 hover:text-[var(--accent)]"
                          title="Rename"
                          aria-label={`Rename ${t.title}`}
                        >
                          <Pencil className="w-3 h-3" />
                        </button>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            deleteThread(t);
                          }}
                          className="p-1 hover:text-red-600"
                          title="Delete"
                          aria-label={`Delete ${t.title}`}
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>

          {/* My Library Accordion */}
          <div>
            <button
              onClick={() => setLibrarySectionOpen(!librarySectionOpen)}
              className="flex items-center justify-between w-full px-2 py-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
            >
              <div className="flex items-center gap-2">
                <Bookmark className="w-3.5 h-3.5 text-[var(--accent)]" />
                <span>My Library</span>
                <span className="text-[10px] bg-[var(--bg-surface-elevated)] px-1.5 py-0.5 rounded-full text-[var(--text-secondary)]">
                  {bookmarks.length}
                </span>
              </div>
              <ChevronDown
                className={`w-3.5 h-3.5 transition-transform duration-200 ${
                  librarySectionOpen ? "" : "-rotate-90"
                }`}
              />
            </button>

            {librarySectionOpen && (
              <div className="mt-2 space-y-1">
                {user && user.is_verified && (
                  <div className="px-2 pb-1 flex flex-wrap items-center gap-1">
                    <button
                      onClick={() => setActiveCollection(null)}
                      className={`px-2 py-0.5 rounded-full text-[11px] border transition ${
                        activeCollection === null
                          ? "bg-[var(--accent)] text-white border-[var(--accent)]"
                          : "border-[var(--border-color)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-elevated)]"
                      }`}
                    >
                      All
                    </button>
                    {collections.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => setActiveCollection(activeCollection === c.id ? null : c.id)}
                        className={`px-2 py-0.5 rounded-full text-[11px] border transition flex items-center gap-1 max-w-[160px] ${
                          activeCollection === c.id
                            ? "bg-[var(--accent)] text-white border-[var(--accent)]"
                            : "border-[var(--border-color)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-elevated)]"
                        }`}
                        title={c.name}
                      >
                        <Folder className="w-3 h-3 shrink-0" />
                        <span className="truncate">{c.name}</span>
                        <span className="opacity-70">{c.count}</span>
                      </button>
                    ))}
                    <button
                      onClick={createCollection}
                      className="p-1 rounded-full text-[var(--text-muted)] hover:text-[var(--accent)] transition"
                      title="New folder"
                      aria-label="New folder"
                    >
                      <FolderPlus className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}
                {activeCollection !== null && (() => {
                  const folder = collections.find((c) => c.id === activeCollection);
                  return folder ? (
                    <div className="px-2 pb-1 flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
                      <button onClick={() => renameCollection(folder)} className="hover:text-[var(--accent)] flex items-center gap-1">
                        <Pencil className="w-3 h-3" /> Rename folder
                      </button>
                      <button onClick={() => deleteCollection(folder)} className="hover:text-red-600 flex items-center gap-1">
                        <Trash2 className="w-3 h-3" /> Delete folder
                      </button>
                    </div>
                  ) : null;
                })()}
                {!user ? (
                  <div className="text-xs text-[var(--text-muted)] px-2 py-2 italic">
                    No saved papers yet.
                  </div>
                ) : libraryLoading ? (
                  <div className="text-xs text-[var(--text-muted)] px-2 py-2">Loading library...</div>
                ) : visibleBookmarks.length === 0 ? (
                  <div className="text-xs text-[var(--text-muted)] px-2 py-2 italic">
                    {activeCollection === null ? "No bookmarked papers yet." : "This folder is empty. Use the folder icon on a saved paper to add it."}
                  </div>
                ) : (
                  visibleBookmarks.map((bm) => (
                    <div
                      key={bm.id}
                      onClick={() => {
                        const existingResult = results.find((r) => r.title === bm.title);
                        if (existingResult) {
                          setSelectedPaperForDetail({
                            paper: existingResult,
                            index: results.indexOf(existingResult),
                          });
                        } else {
                          setSelectedPaperForDetail({
                            paper: {
                              title: bm.title,
                              authors: bm.authors,
                              description: bm.source || "Academic Database",
                              publication_year: bm.publication_year,
                              doi: bm.doi,
                              abstract: bm.abstract,
                            },
                            index: 0,
                          });
                        }
                        if (window.innerWidth < 768) setSidebarOpen(false);
                      }}
                      className="group flex items-center justify-between gap-2 px-2.5 py-2 rounded-lg text-xs hover:bg-[var(--bg-surface-elevated)] cursor-pointer transition"
                    >
                      <div className="flex items-center gap-2 truncate">
                        <BookmarkCheck className="w-3.5 h-3.5 text-[var(--accent)] shrink-0" />
                        <span className="truncate text-[var(--text-primary)]">{bm.title}</span>
                      </div>
                      {collections.length > 0 && (
                        <div className="relative shrink-0">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              setFolderMenuFor(folderMenuFor === bm.id ? null : bm.id);
                            }}
                            className={`p-1 hover:text-[var(--accent)] transition ${
                              (bm.collection_ids || []).length > 0 ? "text-[var(--accent)]" : "opacity-100 md:opacity-0 md:group-hover:opacity-100"
                            }`}
                            title="Folders"
                            aria-label={`Folders for ${bm.title}`}
                            aria-expanded={folderMenuFor === bm.id}
                          >
                            <Folder className="w-3 h-3" />
                          </button>
                          {folderMenuFor === bm.id && (
                            <div
                              onClick={(e) => e.stopPropagation()}
                              className="absolute right-0 top-6 z-20 w-48 rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] shadow-lg py-1"
                            >
                              {collections.map((c) => {
                                const inside = (bm.collection_ids || []).includes(c.id);
                                return (
                                  <button
                                    key={c.id}
                                    onClick={() => toggleInCollection(bm, c.id)}
                                    className="w-full px-3 py-1.5 text-left text-xs flex items-center gap-2 hover:bg-[var(--bg-surface-elevated)] text-[var(--text-primary)]"
                                  >
                                    <Check className={`w-3 h-3 ${inside ? "opacity-100 text-[var(--accent)]" : "opacity-0"}`} />
                                    <span className="truncate">{c.name}</span>
                                  </button>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      )}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteBookmarkById(bm.id);
                        }}
                        className="opacity-100 md:opacity-0 md:group-hover:opacity-100 p-1 hover:text-[var(--accent)] transition"
                        title="Remove bookmark"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  ))
                )}
                {user && visibleBookmarks.length > 0 && (
                  <div className="px-2 pt-2 flex flex-wrap items-center gap-2 text-[11px] text-[var(--text-muted)]">
                    <span>Export {activeCollection === null ? "library" : "folder"}:</span>
                    <button
                      onClick={() =>
                        exportReferences(
                          visibleBookmarks.map(bookmarkAsPaper),
                          "ris",
                          collections.find((c) => c.id === activeCollection)?.name || "library",
                        )
                      }
                      className="hover:text-[var(--accent)] underline-offset-2 hover:underline"
                      title="For Zotero, Mendeley and EndNote"
                    >
                      RIS
                    </button>
                    <button
                      onClick={() =>
                        exportReferences(
                          visibleBookmarks.map(bookmarkAsPaper),
                          "bibtex",
                          collections.find((c) => c.id === activeCollection)?.name || "library",
                        )
                      }
                      className="hover:text-[var(--accent)] underline-offset-2 hover:underline"
                      title="For LaTeX"
                    >
                      BibTeX
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Sidebar Footer: Pinned Account Menu (Displays Full Name / Email Prefix only when logged in) */}
        {user && (
          <div className="p-3 border-t border-[var(--border-color)] bg-[var(--bg-surface)] relative" ref={accountMenuRef}>
            <button
              onClick={() => setAccountDropdownOpen(!accountDropdownOpen)}
              className="flex items-center justify-between w-full p-2 rounded-xl hover:bg-[var(--bg-surface-elevated)] transition text-left"
            >
              <div className="flex items-center gap-2.5 truncate">
                <div className="w-8 h-8 rounded-full bg-[var(--accent)] text-white flex items-center justify-center font-bold text-xs shrink-0 shadow-sm">
                  {userInitials}
                </div>
                <div className="truncate">
                  <div className="text-xs font-semibold text-[var(--text-primary)] truncate">
                    {displayName}
                  </div>
                  <div className="text-[10px] text-[var(--text-secondary)] truncate">
                    {user.is_verified ? "Verified Member" : "Unverified"}
                  </div>
                </div>
              </div>
              <ChevronDown className="w-4 h-4 text-[var(--text-secondary)] shrink-0" />
            </button>

            {/* Account Dropdown Menu */}
            {accountDropdownOpen && (
              <div className="absolute bottom-full left-3 right-3 mb-2 bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-xl shadow-xl p-1.5 z-50 text-xs space-y-1">
                <div className="px-3 py-2 border-b border-[var(--border-color)]">
                  <div className="text-xs font-semibold text-[var(--text-primary)]">{displayName}</div>
                  <div className="text-[11px] text-[var(--text-muted)] truncate">{user.email}</div>
                </div>

                <button
                  onClick={() => {
                    setAccountDropdownOpen(false);
                    setSettingsTab("account");
                    setSettingsModalOpen(true);
                  }}
                  className="flex items-center gap-2.5 w-full px-3 py-2 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-primary)] transition"
                >
                  <Settings className="w-4 h-4 text-[var(--text-secondary)]" />
                  <span>Settings</span>
                </button>

                <div className="border-t border-[var(--border-color)] my-1"></div>

                <button
                  onClick={() => {
                    setAccountDropdownOpen(false);
                    setActivePolicyModal("privacy");
                  }}
                  className="flex items-center gap-2.5 w-full px-3 py-1.5 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
                >
                  <Shield className="w-3.5 h-3.5" />
                  <span>Privacy Policy</span>
                </button>

                <button
                  onClick={() => {
                    setAccountDropdownOpen(false);
                    setActivePolicyModal("terms");
                  }}
                  className="flex items-center gap-2.5 w-full px-3 py-1.5 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
                >
                  <FileText className="w-3.5 h-3.5" />
                  <span>Terms of Service</span>
                </button>

                <button
                  onClick={() => {
                    setAccountDropdownOpen(false);
                    setActivePolicyModal("cookie_prefs");
                  }}
                  className="flex items-center gap-2.5 w-full px-3 py-1.5 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
                >
                  <Cookie className="w-3.5 h-3.5" />
                  <span>Cookie Preferences</span>
                </button>

                <div className="border-t border-[var(--border-color)] my-1"></div>

                <button
                  onClick={logout}
                  className="flex items-center gap-2.5 w-full px-3 py-2 rounded-lg hover:bg-[var(--accent-light)] text-[var(--accent)] transition font-medium"
                >
                  <LogOut className="w-4 h-4" />
                  <span>Log out</span>
                </button>
              </div>
            )}
          </div>
        )}
        </div>
      </motion.aside>

      {/* ======================================================== */}
      {/* 2. MAIN APPLICATION CONTENT AREA */}
      {/* ======================================================== */}
      <main className="flex-1 flex flex-col h-full overflow-hidden">
        {/* Verification Status Notification Banner */}
        {verificationBanner && (
          <div
            className={`px-4 py-2 text-xs flex items-center justify-between ${
              verificationBanner.type === "success"
                ? "bg-emerald-50 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300 border-b border-emerald-200"
                : verificationBanner.type === "info"
                ? "bg-sky-50 dark:bg-sky-950/40 text-sky-800 dark:text-sky-300 border-b border-sky-200"
                : "bg-red-50 dark:bg-red-950/40 text-red-800 dark:text-red-300 border-b border-red-200"
            }`}
          >
            <div className="flex items-center gap-2">
              <Info className="w-4 h-4 shrink-0" />
              <span>{verificationBanner.message}</span>
            </div>
            <button
              onClick={() => setVerificationBanner(null)}
              className="p-1 hover:opacity-70"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Simplified Top Nav Bar */}
        <header className="h-14 border-b border-[var(--border-color)] bg-[var(--bg-surface)] px-4 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-3">
            {!sidebarOpen && (
              <button
                onClick={toggleSidebar}
                className="p-1.5 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
                title="Open sidebar"
              >
                <PanelLeftOpen className="w-5 h-5" />
              </button>
            )}

            {!sidebarOpen && (
              <div className="flex items-center gap-2">
                <BookOpen className="w-5 h-5 text-[var(--accent)]" />
                <span className="font-serif-heading text-lg font-bold tracking-tight text-[var(--text-primary)]">
                  {APP_NAME}
                </span>
              </div>
            )}

            {/* 5 Academic Sources Badge */}
            <div className="hidden sm:flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-[var(--bg-surface-elevated)] border border-[var(--border-color)] text-[11px] text-[var(--text-secondary)] font-medium">
              <Sparkles className="w-3.5 h-3.5 text-[var(--accent)]" />
              <span>5 Academic Sources</span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {/* Theme Toggle */}
            <button
              onClick={toggleTheme}
              className="p-2 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
              title={`Switch to ${theme === "light" ? "dark" : "light"} mode`}
            >
              {theme === "light" ? <Moon className="w-4 h-4" /> : <Sun className="w-4 h-4 text-amber-400" />}
            </button>

            {!user && (
              <button
                onClick={() => openAuthModal("login")}
                className="py-1.5 px-3.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-xs font-medium transition shadow-sm"
              >
                Sign in
              </button>
            )}
          </div>
        </header>

        {/* Dynamic Center Stage: Landing View vs Split Results View */}
        <div className="flex-1 flex overflow-hidden">
          {messages.length === 0 ? (
            /* ======================================================== */
            /* LANDING VIEW (Centered Search + Compliance Footer) */
            /* ======================================================== */
            <div className="flex-1 overflow-y-auto flex flex-col justify-between p-6 sm:p-10">
              <div className="max-w-2xl mx-auto w-full pt-10 sm:pt-16 text-center space-y-6">
                <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-[var(--accent-light)] text-[var(--accent)] text-xs font-semibold">
                  <Sparkles className="w-4 h-4" />
                  <span>Federated Academic Search & AI Literature Synthesis</span>
                </div>

                {/* Exactly One Single H1 Per Page for SEO */}
                <h1 className="font-serif-heading text-4xl sm:text-5xl font-bold tracking-tight text-[var(--text-primary)] leading-tight">
                  Discover and synthesize academic literature with precision.
                </h1>

                <p className="text-base text-[var(--text-secondary)] max-w-lg mx-auto">
                  Search eleven academic databases at once, including PubMed, DOAJ and African Journals Online.
                </p>

                {/* Primary Search Bar */}
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    executeSearch(query);
                  }}
                  className="relative mt-8"
                >
                  <div className="relative flex items-center">
                    <Search className="absolute left-4 w-5 h-5 text-[var(--text-muted)]" />
                    <input
                      type="text"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Ask any research question (e.g. quantum error mitigation in NISQ computers)..."
                      className="w-full pl-12 pr-28 py-4 bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-sm text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)] text-[var(--text-primary)] placeholder-[var(--text-muted)] transition"
                      autoFocus
                    />
                    <button
                      type="submit"
                      disabled={!query.trim() || loading}
                      className="absolute right-2.5 px-4 py-2 bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-50 text-white rounded-xl text-xs font-semibold transition flex items-center gap-1.5"
                    >
                      <span>Search</span>
                      <ArrowRight className="w-3.5 h-3.5" />
                    </button>
                  </div>

                  {/* Research mode */}
                  <div
                    role="radiogroup"
                    aria-label="Research mode"
                    className="flex flex-wrap items-center justify-center gap-1.5 mt-4 text-xs text-[var(--text-secondary)]"
                  >
                    <span className="font-medium mr-1">Mode:</span>
                    {MODE_OPTIONS.map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        role="radio"
                        aria-checked={researchMode === option.value}
                        title={option.hint}
                        onClick={() => setResearchMode(option.value)}
                        className={`px-2.5 py-1 rounded-full border transition ${
                          researchMode === option.value
                            ? "bg-[var(--accent)] text-white border-[var(--accent)]"
                            : "bg-[var(--bg-surface)] border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)]"
                        }`}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                  <p className="mt-1.5 text-center text-[11px] text-[var(--text-muted)]">
                    {MODE_OPTIONS.find((option) => option.value === researchMode)?.hint}
                  </p>

                  {/* Filter Pills */}
                  <div className="flex flex-wrap items-center justify-center gap-2 mt-4 text-xs text-[var(--text-secondary)]">
                    <span className="font-medium">Filter by year:</span>
                    {(["all", "3", "5", "10"] as const).map((y) => (
                      <button
                        key={y}
                        type="button"
                        onClick={() => setYearFilter(y)}
                        className={`px-2.5 py-1 rounded-full border transition ${
                          yearFilter === y
                            ? "bg-[var(--accent)] text-white border-[var(--accent)]"
                            : "bg-[var(--bg-surface)] border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)]"
                        }`}
                      >
                        {y === "all" ? "All Time" : `Past ${y} Years`}
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() => setOpenAccessOnly(!openAccessOnly)}
                      className={`px-2.5 py-1 rounded-full border transition flex items-center gap-1 ${
                        openAccessOnly
                          ? "bg-[var(--accent)] text-white border-[var(--accent)]"
                          : "bg-[var(--bg-surface)] border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)]"
                      }`}
                    >
                      <Check className={`w-3 h-3 ${openAccessOnly ? "opacity-100" : "opacity-0"}`} />
                      <span>Open Access</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => setShowMoreFilters(!showMoreFilters)}
                      aria-expanded={showMoreFilters}
                      className={`px-2.5 py-1 rounded-full border transition flex items-center gap-1 ${
                        showMoreFilters || activeExtraFilters > 0
                          ? "bg-[var(--accent-light)] text-[var(--accent)] border-[var(--accent)]/40"
                          : "bg-[var(--bg-surface)] border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)]"
                      }`}
                    >
                      <Filter className="w-3 h-3" />
                      <span>More filters{activeExtraFilters > 0 ? ` (${activeExtraFilters})` : ""}</span>
                    </button>
                  </div>

                  {showMoreFilters && (
                    <div className="mt-3 p-4 rounded-2xl border border-[var(--border-color)] bg-[var(--bg-surface)] text-xs text-[var(--text-secondary)] text-left grid grid-cols-1 sm:grid-cols-2 gap-3">
                      <label className="flex flex-col gap-1">
                        <span className="font-medium">Search by</span>
                        <select
                          value={searchBy}
                          onChange={(e) => setSearchBy(e.target.value as SearchBy)}
                          className="px-2.5 py-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                        >
                          <option value="topic">Topic or question</option>
                          <option value="title">Paper title</option>
                          <option value="author">Author name</option>
                        </select>
                      </label>
                      <label className="flex flex-col gap-1">
                        <span className="font-medium">Sort by</span>
                        <select
                          value={sortBy}
                          onChange={(e) => setSortBy(e.target.value as SortOption)}
                          className="px-2.5 py-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                        >
                          <option value="relevance">Relevance</option>
                          <option value="newest">Newest first</option>
                          <option value="most_cited">Most cited</option>
                        </select>
                      </label>
                      <label className="flex flex-col gap-1">
                        <span className="font-medium">Study type</span>
                        <select
                          value={studyTypeFilter}
                          onChange={(e) => setStudyTypeFilter(e.target.value)}
                          className="px-2.5 py-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                        >
                          <option value="">Any study type</option>
                          {Object.entries(STUDY_TYPE_LABELS).map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="flex flex-col gap-1">
                        <span className="font-medium">Field of study</span>
                        <select
                          value={fieldFilter}
                          onChange={(e) => setFieldFilter(e.target.value)}
                          className="px-2.5 py-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                        >
                          <option value="">Any field</option>
                          {FIELD_OPTIONS.map((f) => (
                            <option key={f} value={f}>
                              {f}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="flex flex-col gap-1">
                        <span className="font-medium">Minimum citations</span>
                        <select
                          value={minCitations}
                          onChange={(e) => setMinCitations(e.target.value)}
                          className="px-2.5 py-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                        >
                          <option value="">No minimum</option>
                          <option value="10">10+</option>
                          <option value="50">50+</option>
                          <option value="100">100+</option>
                          <option value="500">500+</option>
                        </select>
                      </label>
                      <div className="flex flex-col gap-1">
                        <span className="font-medium">Preprints</span>
                        <button
                          type="button"
                          onClick={() => setExcludePreprints(!excludePreprints)}
                          aria-pressed={excludePreprints}
                          className={`px-2.5 py-2 rounded-lg border transition flex items-center gap-1.5 ${
                            excludePreprints
                              ? "bg-[var(--accent)] text-white border-[var(--accent)]"
                              : "bg-[var(--bg-primary)] border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)]"
                          }`}
                        >
                          <Check className={`w-3 h-3 ${excludePreprints ? "opacity-100" : "opacity-0"}`} />
                          <span>Exclude preprints</span>
                        </button>
                      </div>
                      <p className="sm:col-span-2 text-[11px] text-[var(--text-muted)] leading-relaxed">
                        Papers whose study type, field or citation count is unknown are left out by those filters. Tip: you can
                        also type <span className="font-mono">author:</span> or <span className="font-mono">title:</span> before
                        your search, or paste a DOI.
                      </p>
                      {activeExtraFilters > 0 && (
                        <button
                          type="button"
                          onClick={() => {
                            setStudyTypeFilter("");
                            setFieldFilter("");
                            setMinCitations("");
                            setExcludePreprints(false);
                            setSortBy("relevance");
                            setSearchBy("topic");
                          }}
                          className="sm:col-span-2 justify-self-start flex items-center gap-1 text-[var(--accent)] hover:underline"
                        >
                          <RotateCcw className="w-3 h-3" />
                          <span>Clear these filters</span>
                        </button>
                      )}
                    </div>
                  )}
                </form>
              </div>

              {/* Logged-Out Landing Footer (Disappears When User Is Signed In) */}
              <footer
                className={`mt-16 max-w-4xl mx-auto w-full ${
                  !user ? "pt-10 border-t border-[var(--border-color)]" : ""
                }`}
              >
                {!user && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-8 mb-8 text-xs text-[var(--text-secondary)]">
                    {/* Compliance Column */}
                    <div>
                      <div className="font-semibold text-[var(--text-primary)] uppercase tracking-wider mb-3">
                        Compliance & Governance
                      </div>
                      <ul className="space-y-2">
                        <li>
                          <button
                            onClick={() => setActivePolicyModal("privacy")}
                            className="hover:text-[var(--accent)] transition"
                          >
                            Privacy Policy
                          </button>
                        </li>
                        <li>
                          <button
                            onClick={() => setActivePolicyModal("terms")}
                            className="hover:text-[var(--accent)] transition"
                          >
                            Terms of Service
                          </button>
                        </li>
                        <li>
                          <button
                            onClick={() => setActivePolicyModal("cookie_policy")}
                            className="hover:text-[var(--accent)] transition"
                          >
                            Cookie Policy
                          </button>
                        </li>
                        <li>
                          <button
                            onClick={() => setActivePolicyModal("cookie_prefs")}
                            className="hover:text-[var(--accent)] transition flex items-center gap-1"
                          >
                            <Cookie className="w-3.5 h-3.5" />
                            <span>Cookie Preferences</span>
                          </button>
                        </li>
                      </ul>
                    </div>

                    {/* Sources Column */}
                    <div>
                      <div className="font-semibold text-[var(--text-primary)] uppercase tracking-wider mb-3">
                        Academic Sources
                      </div>
                      <div className="flex flex-wrap gap-2">
                        {["OpenAlex", "Semantic Scholar", "arXiv", "Crossref", "CORE"].map((src) => (
                          <span
                            key={src}
                            className="px-2.5 py-1 rounded-md bg-[var(--bg-surface)] border border-[var(--border-color)] text-[11px] font-medium"
                          >
                            {src}
                          </span>
                        ))}
                      </div>
                      <p className="mt-3 text-[11px] text-[var(--text-muted)]">
                        Real-time discovery across eleven academic databases.
                      </p>
                    </div>
                  </div>
                )}

                <div className="pt-6 pb-6 border-t border-[var(--border-subtle)] flex flex-col sm:flex-row items-center justify-between text-[11px] text-[var(--text-muted)] gap-2">
                  <div>© 2026 {APP_NAME}. All rights reserved.</div>
                  <div>Federated academic discovery across eleven academic databases.</div>
                  <div>
                    Developed by{" "}
                    <a
                      href="https://zcsi-foundation.org"
                      target="_blank"
                      rel="noopener noreferrer"
                      className="font-medium text-[var(--text-secondary)] underline-offset-2 hover:text-[var(--accent)] hover:underline transition"
                    >
                      Zambia Cybersecurity Initiative Foundation
                    </a>
                  </div>
                </div>
              </footer>
            </div>
          ) : (
            /* ======================================================== */
            /* SPLIT WORKSPACE VIEW (Chat Synthesis + Papers Column) */
            /* ======================================================== */
            <div className="flex-1 flex flex-col md:flex-row h-full overflow-hidden">
              {/* Left Chat / Synthesis Column */}
              <div className="flex-1 flex flex-col h-full border-r border-[var(--border-color)] bg-[var(--bg-primary)] overflow-hidden">
                {chatTurns.length > 0 && (
                  <div className="px-4 sm:px-6 py-2 border-b border-[var(--border-color)] bg-[var(--bg-surface)] flex flex-wrap items-center gap-2 text-xs shrink-0">
                    <button
                      type="button"
                      onClick={saveThread}
                      disabled={threadSaving || loading || !unsavedTurns}
                      className="px-2.5 py-1.5 rounded-lg border border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)] disabled:opacity-60 font-medium text-[var(--text-secondary)] flex items-center gap-1.5 transition"
                    >
                      {unsavedTurns ? <Save className="w-3.5 h-3.5" /> : <Check className="w-3.5 h-3.5 text-emerald-600" />}
                      <span>
                        {threadSaving
                          ? "Saving..."
                          : !unsavedTurns
                            ? "Saved"
                            : currentThreadId
                              ? "Save changes"
                              : "Save conversation"}
                      </span>
                    </button>
                    {currentThreadId !== null && (
                      <button
                        type="button"
                        onClick={() => {
                          const t = threads.find((x) => x.id === currentThreadId);
                          if (t) {
                            setShareCopied(false);
                            setShareTarget(t);
                          }
                        }}
                        className="px-2.5 py-1.5 rounded-lg border border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)] font-medium text-[var(--text-secondary)] flex items-center gap-1.5 transition"
                      >
                        <Share2 className="w-3.5 h-3.5" />
                        <span>Share</span>
                      </button>
                    )}
                    {threadNotice && <span className="text-[var(--text-muted)]">{threadNotice}</span>}
                  </div>
                )}
                {/* Scrollable Messages */}
                <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-6">
                  {messages.map((m) => (
                    <div
                      key={m.id}
                      className={`flex flex-col ${
                        m.role === "user" ? "items-end" : "items-start"
                      }`}
                    >
                      <div className="flex items-center gap-2 mb-1 text-[11px] text-[var(--text-muted)]">
                        <span>{m.role === "user" ? "You" : `${APP_NAME} AI`}</span>
                        <span>•</span>
                        <span>{m.timestamp}</span>
                        {m.scopedPaperIds && m.scopedPaperIds.length > 0 && (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-[var(--accent-light)] text-[var(--accent)] font-medium">
                            <Layers className="w-3 h-3" />
                            <span>Scoped to {m.scopedPaperIds.length} paper{m.scopedPaperIds.length > 1 ? "s" : ""}</span>
                          </span>
                        )}
                      </div>

                      <div
                        className={`max-w-2xl rounded-2xl p-4 sm:p-5 text-sm leading-relaxed ${
                          m.role === "user"
                            ? "bg-[var(--accent)] text-white"
                            : "bg-[var(--bg-surface)] text-[var(--text-primary)] border border-[var(--border-color)] shadow-sm"
                        }`}
                      >
                        {m.role === "assistant" && m.modeLabel && (
                          <div className="mb-3 flex flex-wrap items-center gap-1.5 text-[11px]">
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-[var(--accent-light)] text-[var(--accent)] font-semibold">
                              <Sparkles className="w-3 h-3" />
                              {m.modeLabel}
                            </span>
                            {m.subQueries && m.subQueries.length > 0 && (
                              <span className="text-[var(--text-muted)]">Searched: {m.subQueries.join(" · ")}</span>
                            )}
                          </div>
                        )}
                        {m.role === "assistant" ? (
                          <Markdown text={m.content} />
                        ) : (
                          <div className="whitespace-pre-wrap">{m.content}</div>
                        )}

                        {/* Citations block for assistant response */}
                        {m.citations && m.citations.length > 0 && (
                          <div className="mt-4 pt-3 border-t border-[var(--border-color)] space-y-1.5">
                            <div className="text-xs font-semibold text-[var(--text-secondary)] flex items-center gap-1.5">
                              <BookOpen className="w-3.5 h-3.5 text-[var(--accent)]" />
                              <span>Cited References:</span>
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                              {m.citations.map((c) => (
                                <button
                                  key={c.paper_id}
                                  onClick={() => {
                                    const target = results[c.paper_id - 1];
                                    if (target) {
                                      setSelectedPaperForDetail({
                                        paper: target,
                                        index: c.paper_id - 1,
                                      });
                                    }
                                  }}
                                  className="text-xs px-2 py-1 bg-[var(--bg-surface-elevated)] hover:bg-[var(--accent-light)] hover:text-[var(--accent)] border border-[var(--border-color)] rounded-md transition flex items-center gap-1"
                                >
                                  <span className="font-semibold text-[var(--accent)]">[{c.paper_id}]</span>
                                  <span className="truncate max-w-[200px]">{c.title}</span>
                                </button>
                              ))}
                            </div>
                          </div>
                        )}

                        {m.role === "assistant" && m.meter && m.meter.total > 0 && (
                          <EvidenceMeter
                            meter={m.meter}
                            onOpenPaper={(paperId) => {
                              const target = results[paperId - 1];
                              if (target) setSelectedPaperForDetail({ paper: target, index: paperId - 1 });
                            }}
                          />
                        )}

                        {/* Action buttons for assistant answers */}
                        {m.role === "assistant" && (
                          <div className="mt-4 pt-3 border-t border-[var(--border-subtle)] flex flex-wrap items-center justify-end gap-2 text-xs text-[var(--text-secondary)]">
                            {m.question && (m.mode === "quick" || m.mode === "medical") && (
                              <button
                                type="button"
                                onClick={() => handleGoDeeper(m)}
                                disabled={loading}
                                className="mr-auto px-2.5 py-1.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-50 text-white font-semibold transition flex items-center gap-1"
                                title="Run 3 to 5 searches and write a structured review (takes longer)"
                              >
                                <Layers className="w-3.5 h-3.5" />
                                <span>Go deeper</span>
                              </button>
                            )}
                            {m.offerQuick && m.question && (
                              <button
                                type="button"
                                onClick={() => handleQuickFallback(m)}
                                disabled={loading}
                                className="mr-auto px-2.5 py-1.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-50 text-white font-semibold transition flex items-center gap-1"
                              >
                                <Sparkles className="w-3.5 h-3.5" />
                                <span>Get a quick answer</span>
                              </button>
                            )}
                            <button
                              onClick={() => handleCopyAnswer(m)}
                              className="p-1.5 rounded-lg hover:bg-[var(--bg-surface-elevated)] hover:text-[var(--text-primary)] transition flex items-center gap-1"
                              title="Copy answer"
                            >
                              {copiedAnswerId === m.id ? (
                                <>
                                  <Check className="w-3.5 h-3.5 text-emerald-600" />
                                  <span className="text-emerald-600 font-medium">Copied</span>
                                </>
                              ) : (
                                <>
                                  <Copy className="w-3.5 h-3.5" />
                                  <span>Copy</span>
                                </>
                              )}
                            </button>
                            <button
                              onClick={() => handleExportPDF(m)}
                              className="p-1.5 rounded-lg hover:bg-[var(--bg-surface-elevated)] hover:text-[var(--text-primary)] transition flex items-center gap-1"
                              title="Export PDF report"
                            >
                              <FileDown className="w-3.5 h-3.5" />
                              <span>PDF</span>
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}

                  {loading && (
                    <div className="flex items-center gap-3 p-4 rounded-xl bg-[var(--bg-surface)] border border-[var(--border-color)] text-xs text-[var(--text-secondary)]">
                      <Sparkles className="w-4 h-4 text-[var(--accent)] animate-spin" />
                      <span>{loadingStep || "Processing..."}</span>
                    </div>
                  )}

                  {error && (
                    <div className="p-4 rounded-xl bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900/50 text-xs text-red-600 dark:text-red-400">
                      {error}
                    </div>
                  )}

                  <div ref={chatEndRef} />
                </div>

                {/* Sticky Follow-up Input Bar with Scoped Paper Chips */}
                <div className="p-4 border-t border-[var(--border-color)] bg-[var(--bg-surface)]">
                  {selectedPaperIds.length > 0 && (
                    <div className="mb-3 p-2 bg-[var(--bg-surface-elevated)] rounded-xl border border-[var(--border-color)] flex flex-wrap items-center gap-1.5 text-xs">
                      <div className="flex items-center gap-1 text-[var(--accent)] font-semibold mr-1">
                        <Filter className="w-3.5 h-3.5" />
                        <span>Context ({selectedPaperIds.length}):</span>
                      </div>

                      {selectedPaperIds.map((id) => {
                        const paper = results[id - 1];
                        return (
                          <span
                            key={id}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-[var(--bg-surface)] border border-[var(--border-color)] text-[11px] text-[var(--text-primary)]"
                          >
                            <span className="font-bold text-[var(--accent)]">[{id}]</span>
                            <span className="truncate max-w-[140px]">{paper?.title || `Paper ${id}`}</span>
                            <button
                              onClick={() => togglePaperSelection(id)}
                              className="hover:text-[var(--accent)] p-0.5"
                            >
                              <X className="w-3 h-3" />
                            </button>
                          </span>
                        );
                      })}

                      <button
                        onClick={() => setSelectedPaperIds([])}
                        className="text-[11px] text-[var(--accent)] hover:underline ml-auto font-medium"
                      >
                        Clear all
                      </button>
                    </div>
                  )}

                  <form onSubmit={handleFollowUpSubmit} className="relative flex items-center">
                    <input
                      type="text"
                      value={followUpQuery}
                      onChange={(e) => setFollowUpQuery(e.target.value)}
                      placeholder={
                        selectedPaperIds.length > 0
                          ? `Ask question scoped to ${selectedPaperIds.length} selected paper(s)...`
                          : "Ask follow-up question across all retrieved papers..."
                      }
                      className="w-full pl-4 pr-24 py-3 bg-[var(--bg-primary)] border border-[var(--border-color)] rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)] text-[var(--text-primary)] placeholder-[var(--text-muted)] transition"
                    />
                    <button
                      type="submit"
                      disabled={!followUpQuery.trim() || loading}
                      className="absolute right-2 px-3.5 py-1.5 bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-50 text-white rounded-lg text-xs font-semibold transition flex items-center gap-1"
                    >
                      <Send className="w-3.5 h-3.5" />
                      <span>Send</span>
                    </button>
                  </form>
                </div>
              </div>

              {/* Right Column: Search Results List OR Dedicated Paper Detail View */}
              <div className="w-full md:w-[460px] lg:w-[520px] flex flex-col h-full bg-[var(--bg-surface)] overflow-hidden">
                {selectedPaperForDetail ? (
                  /* ======================================================== */
                  /* IN-APP PAPER DETAIL VIEW */
                  /* ======================================================== */
                  <div className="flex-1 flex flex-col h-full overflow-hidden">
                    <div className="p-4 border-b border-[var(--border-color)] flex items-center justify-between">
                      <button
                        onClick={() => setSelectedPaperForDetail(null)}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--bg-surface-elevated)] hover:bg-[var(--border-color)] text-xs font-medium text-[var(--text-primary)] transition"
                      >
                        <ChevronRight className="w-3.5 h-3.5 rotate-180" />
                        <span>Back to Papers</span>
                      </button>

                      <button
                        onClick={() => toggleBookmark(selectedPaperForDetail.paper)}
                        className={`inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border text-xs font-medium transition ${
                          isPaperBookmarked(selectedPaperForDetail.paper)
                            ? "bg-[var(--accent-light)] border-[var(--accent)] text-[var(--accent)]"
                            : "bg-[var(--bg-surface)] border-[var(--border-color)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                        }`}
                      >
                        {isPaperBookmarked(selectedPaperForDetail.paper) ? (
                          <>
                            <BookmarkCheck className="w-3.5 h-3.5 text-[var(--accent)]" />
                            <span>Saved in Library</span>
                          </>
                        ) : (
                          <>
                            <Bookmark className="w-3.5 h-3.5" />
                            <span>Save to Library</span>
                          </>
                        )}
                      </button>
                    </div>

                    <div className="flex border-b border-[var(--border-color)] px-4">
                      <button
                        onClick={() => setDetailTab("overview")}
                        className={`py-2.5 px-4 text-xs font-semibold border-b-2 transition ${
                          detailTab === "overview"
                            ? "border-[var(--accent)] text-[var(--accent)]"
                            : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                        }`}
                      >
                        Overview
                      </button>
                      <button
                        onClick={() => setDetailTab("metadata")}
                        className={`py-2.5 px-4 text-xs font-semibold border-b-2 transition ${
                          detailTab === "metadata"
                            ? "border-[var(--accent)] text-[var(--accent)]"
                            : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                        }`}
                      >
                        Metadata & Identifiers
                      </button>
                    </div>

                    <div className="flex-1 overflow-y-auto p-5 space-y-5">
                      {detailTab === "overview" ? (
                        <>
                          <div className="space-y-2">
                            <h2 className="font-serif-heading text-xl font-bold text-[var(--text-primary)] leading-snug">
                              {selectedPaperForDetail.paper.title}
                            </h2>
                            <div className="text-xs text-[var(--text-secondary)]">
                              <span>
                                {selectedPaperForDetail.paper.authors.length > 0
                                  ? selectedPaperForDetail.paper.authors.join(", ")
                                  : "Unknown Authors"}
                              </span>
                              {selectedPaperForDetail.paper.publication_year && (
                                <span> • {selectedPaperForDetail.paper.publication_year}</span>
                              )}
                              <span> • {selectedPaperForDetail.paper.description}</span>
                            </div>
                          </div>

                          <div className="space-y-2">
                            <div className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                              Abstract
                            </div>
                            <div className="p-4 rounded-xl bg-[var(--bg-primary)] border border-[var(--border-color)] text-xs sm:text-sm text-[var(--text-primary)] leading-relaxed whitespace-pre-wrap">
                              {selectedPaperForDetail.paper.abstract || "No abstract available for this publication."}
                            </div>
                          </div>

                          <div className="flex flex-wrap gap-2 pt-2">
                            <button
                              onClick={() =>
                                handleDeepDive(
                                  selectedPaperForDetail.index,
                                  selectedPaperForDetail.paper
                                )
                              }
                              disabled={deepDiveLoading[selectedPaperForDetail.index]}
                              className="flex items-center gap-1.5 px-3 py-2 bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-xs font-medium rounded-lg transition"
                            >
                              <Sparkles className="w-3.5 h-3.5" />
                              <span>
                                {deepDiveLoading[selectedPaperForDetail.index]
                                  ? "Analyzing..."
                                  : "Analyze this paper"}
                              </span>
                            </button>

                            <button
                              onClick={() => {
                                setCiteCopied(false);
                                setCiteTarget(selectedPaperForDetail.paper);
                              }}
                              className="flex items-center gap-1.5 px-3 py-2 bg-[var(--bg-surface-elevated)] hover:bg-[var(--border-color)] text-[var(--text-primary)] text-xs font-medium rounded-lg border border-[var(--border-color)] transition"
                              title="APA, MLA, Chicago, Harvard, Vancouver, BibTeX or RIS"
                            >
                              <Quote className="w-3.5 h-3.5" />
                              <span>Cite</span>
                            </button>

                            {selectedPaperForDetail.paper.doi && (
                              <button
                                onClick={() => openCitationMap(selectedPaperForDetail.paper)}
                                className="flex items-center gap-1.5 px-3 py-2 bg-[var(--bg-surface-elevated)] hover:bg-[var(--border-color)] text-[var(--text-primary)] text-xs font-medium rounded-lg border border-[var(--border-color)] transition"
                                title="What this paper cites, and what cites it"
                              >
                                <Network className="w-3.5 h-3.5" />
                                <span>Citation map</span>
                              </button>
                            )}

                            <button
                              onClick={() =>
                                handleExportPaperPDF(
                                  selectedPaperForDetail.index,
                                  selectedPaperForDetail.paper
                                )
                              }
                              className="flex items-center gap-1.5 px-3 py-2 bg-[var(--bg-surface-elevated)] hover:bg-[var(--border-color)] text-[var(--text-primary)] text-xs font-medium rounded-lg border border-[var(--border-color)] transition"
                              title="Download this paper's details, abstract, analysis and BibTeX as a PDF"
                            >
                              <FileDown className="w-3.5 h-3.5" />
                              <span>Export PDF</span>
                            </button>

                            {selectedPaperForDetail.paper.pdf_url && (
                              <a
                                href={selectedPaperForDetail.paper.pdf_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-1.5 px-3 py-2 bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-medium rounded-lg transition"
                                title="A free, legal copy found by Unpaywall"
                              >
                                <FileDown className="w-3.5 h-3.5" />
                                <span>Free PDF</span>
                              </a>
                            )}

                            {selectedPaperForDetail.paper.doi && (
                              <a
                                href={doiUrl(selectedPaperForDetail.paper.doi)}
                                target="_blank"
                                rel="noreferrer"
                                className="flex items-center gap-1.5 px-3 py-2 bg-[var(--bg-surface-elevated)] hover:bg-[var(--border-color)] text-[var(--text-primary)] text-xs font-medium rounded-lg border border-[var(--border-color)] transition"
                              >
                                <ExternalLink className="w-3.5 h-3.5" />
                                <span>Open DOI</span>
                              </a>
                            )}
                          </div>

                          {deepDiveErrors[paperKey(selectedPaperForDetail.paper)] && (
                            <div className="mt-4 p-3 rounded-xl border border-red-500/30 bg-red-500/10 text-xs text-red-600">
                              {deepDiveErrors[paperKey(selectedPaperForDetail.paper)]}
                            </div>
                          )}
                          {(() => {
                            const analysis = deepDives[paperKey(selectedPaperForDetail.paper)];
                            if (!analysis) return null;
                            const paper = selectedPaperForDetail.paper;
                            // A study type reported by the source wins over the AI's reading of the abstract.
                            const sourceType = primaryStudyType(paper);
                            const rows: [string, string][] = analysis.glance
                              ? [
                                  ["Study type", sourceType || analysis.glance.study_type],
                                  ["Who or what was studied", analysis.glance.population],
                                  ["Sample size", analysis.glance.sample_size],
                                  ["Main finding", analysis.glance.main_finding],
                                ]
                              : [];
                            return (
                              <>
                                {rows.length > 0 && (
                                  <div className="mt-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-primary)] overflow-hidden">
                                    <div className="px-4 py-2 border-b border-[var(--border-color)] text-xs font-semibold text-[var(--text-primary)] flex items-center gap-1.5">
                                      <Lightbulb className="w-3.5 h-3.5 text-[var(--accent)]" />
                                      <span>At a glance</span>
                                    </div>
                                    <dl className="divide-y divide-[var(--border-subtle)] text-xs">
                                      {rows.map(([label, value]) => (
                                        <div key={label} className="px-4 py-2 grid grid-cols-1 sm:grid-cols-[150px_1fr] gap-0.5 sm:gap-3">
                                          <dt className="text-[var(--text-secondary)] font-medium">{label}</dt>
                                          <dd
                                            className={
                                              value === "Not stated"
                                                ? "text-[var(--text-muted)] italic"
                                                : "text-[var(--text-primary)]"
                                            }
                                          >
                                            {value}
                                          </dd>
                                        </div>
                                      ))}
                                    </dl>
                                  </div>
                                )}
                                <div className="mt-4 p-4 rounded-xl bg-[var(--accent-light)] border border-[var(--accent)]/30 text-xs text-[var(--text-primary)] space-y-2">
                                  <div className="font-semibold text-[var(--accent)] flex items-center gap-1.5">
                                    <Compass className="w-4 h-4" />
                                    <span>Paper analysis:</span>
                                  </div>
                                  <Markdown text={analysis.text} compact />
                                </div>
                              </>
                            );
                          })()}
                        </>
                      ) : (
                        <div className="space-y-4 text-xs">
                          <div className="p-4 rounded-xl bg-[var(--bg-primary)] border border-[var(--border-color)] space-y-3">
                            <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                              <span className="text-[var(--text-secondary)] font-medium">Published In:</span>
                              <span className="font-semibold text-[var(--text-primary)]">
                                {selectedPaperForDetail.paper.description}
                              </span>
                            </div>
                            {selectedPaperForDetail.paper.database && (
                              <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                                <span className="text-[var(--text-secondary)] font-medium">Found Via:</span>
                                <span className="font-semibold text-[var(--text-primary)]">
                                  {selectedPaperForDetail.paper.database}
                                </span>
                              </div>
                            )}
                            <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                              <span className="text-[var(--text-secondary)] font-medium">Citations:</span>
                              <span className="font-semibold text-[var(--text-primary)]">
                                {typeof selectedPaperForDetail.paper.cited_by_count === "number"
                                  ? selectedPaperForDetail.paper.cited_by_count.toLocaleString()
                                  : "Unknown"}
                              </span>
                            </div>
                            <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                              <span className="text-[var(--text-secondary)] font-medium">Study Type:</span>
                              <span className="font-semibold text-[var(--text-primary)] text-right">
                                {selectedPaperForDetail.paper.study_types && selectedPaperForDetail.paper.study_types.length > 0
                                  ? selectedPaperForDetail.paper.study_types
                                      .map((t) => STUDY_TYPE_LABELS[t] || t)
                                      .join(", ")
                                  : "Not stated"}
                              </span>
                            </div>
                            {selectedPaperForDetail.paper.field && (
                              <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                                <span className="text-[var(--text-secondary)] font-medium">Field:</span>
                                <span className="font-semibold text-[var(--text-primary)]">
                                  {selectedPaperForDetail.paper.field}
                                </span>
                              </div>
                            )}
                            {(selectedPaperForDetail.paper.is_retracted || selectedPaperForDetail.paper.is_preprint) && (
                              <div className="flex justify-between items-center py-1 border-b border-[var(--border-subtle)]">
                                <span className="text-[var(--text-secondary)] font-medium">Status:</span>
                                <PaperBadges
                                  paper={{
                                    ...selectedPaperForDetail.paper,
                                    study_types: [],
                                    cited_by_count: null,
                                  }}
                                />
                              </div>
                            )}
                            <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                              <span className="text-[var(--text-secondary)] font-medium">Publication Year:</span>
                              <span className="font-semibold text-[var(--text-primary)]">
                                {selectedPaperForDetail.paper.publication_year || "Unknown"}
                              </span>
                            </div>
                            <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                              <span className="text-[var(--text-secondary)] font-medium">Semantic Similarity:</span>
                              <span className="font-semibold text-[var(--accent)]">
                                {selectedPaperForDetail.paper.similarity_score
                                  ? `${selectedPaperForDetail.paper.similarity_score}%`
                                  : "N/A"}
                              </span>
                            </div>
                            <div className="flex justify-between py-1">
                              <span className="text-[var(--text-secondary)] font-medium">DOI / Identifier:</span>
                              <span className="font-mono text-[11px] text-[var(--text-primary)] truncate max-w-[220px]">
                                {selectedPaperForDetail.paper.doi || "None listed"}
                              </span>
                            </div>
                          </div>

                          <div className="p-4 rounded-xl bg-[var(--bg-primary)] border border-[var(--border-color)] space-y-2">
                            <div className="font-semibold text-[var(--text-primary)] uppercase tracking-wider">
                              Authors Breakdown ({selectedPaperForDetail.paper.authors.length})
                            </div>
                            <ul className="list-disc list-inside space-y-1 text-[var(--text-secondary)]">
                              {selectedPaperForDetail.paper.authors.map((author, i) => (
                                <li key={i}>{author}</li>
                              ))}
                            </ul>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  /* PAPERS RESULTS LIST */
                  <div className="flex-1 flex flex-col h-full overflow-hidden">
                    <div className="p-4 border-b border-[var(--border-color)] shrink-0 space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex items-center gap-2">
                          <BookOpen className="w-4 h-4 text-[var(--accent)]" />
                          <span className="font-semibold text-xs text-[var(--text-primary)]">
                            Retrieved Papers ({results.length})
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => setTableOpen(true)}
                          className="px-2 py-1 rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-elevated)] text-[11px] font-medium text-[var(--text-secondary)] flex items-center gap-1 transition"
                          title="Open all papers in a sortable table"
                        >
                          <Table2 className="w-3.5 h-3.5" />
                          <span>Table view</span>
                        </button>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        {canCompare ? (
                          <button
                            type="button"
                            onClick={handleCompare}
                            className="px-2 py-1 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-[11px] font-semibold flex items-center gap-1 transition"
                          >
                            <Scale className="w-3.5 h-3.5" />
                            <span>Compare ({selectedForCompare.length})</span>
                          </button>
                        ) : (
                          <span className="text-[11px] text-[var(--text-muted)]">
                            {selectedForCompare.length > 5
                              ? "Select at most 5 papers to compare"
                              : "Tick papers to scope questions, or 2 to 5 to compare"}
                          </span>
                        )}
                        {papersMissingTakeaways.length > 0 && (
                          <button
                            type="button"
                            onClick={handleGetTakeaways}
                            disabled={takeawaysLoading}
                            className="px-2 py-1 rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-elevated)] disabled:opacity-60 text-[11px] font-medium text-[var(--text-secondary)] flex items-center gap-1 transition"
                            title="One AI call for the next 10 papers without a takeaway"
                          >
                            <Lightbulb className="w-3.5 h-3.5" />
                            <span>{takeawaysLoading ? "Getting takeaways..." : `Get takeaways (${Math.min(10, papersMissingTakeaways.length)})`}</span>
                          </button>
                        )}
                      </div>
                      {takeawaysError && <div className="text-[11px] text-red-600">{takeawaysError}</div>}
                    </div>

                    <div ref={papersScrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
                      {results.slice(0, visiblePapers).map((paper, idx) => {
                        const paperId = idx + 1;
                        const isSelected = selectedPaperIds.includes(paperId);
                        const bookmarked = isPaperBookmarked(paper);

                        return (
                          <div
                            key={idx}
                            style={{ animationDelay: `${Math.min(idx, 8) * 40}ms` }}
                            onClick={() =>
                              setSelectedPaperForDetail({
                                paper,
                                index: idx,
                              })
                            }
                            className={`sp-rise p-4 rounded-xl border transition cursor-pointer hover:border-[var(--accent)]/50 ${
                              isSelected
                                ? "bg-[var(--accent-light)]/40 border-[var(--accent)]"
                                : "bg-[var(--bg-surface)] border-[var(--border-color)]"
                            }`}
                          >
                            <div className="flex items-start justify-between gap-2 mb-2">
                              <div className="flex items-center gap-2">
                                <button
                                  type="button"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    togglePaperSelection(paperId);
                                  }}
                                  className="text-[var(--accent)] hover:opacity-80 transition"
                                >
                                  {isSelected ? (
                                    <CheckSquare className="w-4 h-4" />
                                  ) : (
                                    <Square className="w-4 h-4 text-[var(--text-muted)]" />
                                  )}
                                </button>
                                <span className="font-bold text-xs text-[var(--accent)]">
                                  [{paperId}]
                                </span>
                              </div>

                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  toggleBookmark(paper);
                                }}
                                className="text-[var(--text-muted)] hover:text-[var(--accent)] p-1 transition"
                                title={bookmarked ? "Remove from Library" : "Save to Library"}
                              >
                                {bookmarked ? (
                                  <BookmarkCheck className="w-4 h-4 text-[var(--accent)]" />
                                ) : (
                                  <Bookmark className="w-4 h-4" />
                                )}
                              </button>
                            </div>

                            <h3 className="font-serif-heading text-sm font-bold text-[var(--text-primary)] line-clamp-2 hover:text-[var(--accent)] transition">
                              {paper.title}
                            </h3>

                            <div className="mt-1 text-[11px] text-[var(--text-secondary)] truncate">
                              {paper.authors.length > 0 ? paper.authors.join(", ") : "Unknown"}
                              {paper.publication_year && ` (${paper.publication_year})`}
                            </div>

                            <div className="mt-1.5">
                              <PaperBadges paper={paper} />
                            </div>

                            {takeaways[paperKey(paper)] && (
                              <p className="mt-2 text-xs text-[var(--text-primary)] leading-relaxed flex gap-1.5">
                                <Lightbulb className="w-3.5 h-3.5 mt-0.5 shrink-0 text-[var(--accent)]" />
                                <span>{takeaways[paperKey(paper)]}</span>
                              </p>
                            )}

                            <p className="mt-2 text-xs text-[var(--text-secondary)] line-clamp-2 leading-relaxed">
                              {paper.abstract || "No abstract preview available."}
                            </p>

                            <div className="mt-3 flex items-center justify-between text-[10px] text-[var(--text-muted)] pt-2 border-t border-[var(--border-subtle)]">
                              <span className="font-medium text-[var(--text-secondary)] truncate">
                                {paper.description}
                              </span>
                              {paper.pdf_url && (
                                <a
                                  href={paper.pdf_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  onClick={(e) => e.stopPropagation()}
                                  className="shrink-0 px-1.5 py-0.5 rounded-md bg-emerald-500/15 text-emerald-700 border border-emerald-500/30 font-semibold hover:bg-emerald-500/25"
                                  title="A free, legal copy found by Unpaywall"
                                >
                                  Free PDF
                                </a>
                              )}
                              {paper.similarity_score && (
                                <span className="font-semibold text-[var(--accent)]">
                                  {paper.similarity_score}% match
                                </span>
                              )}
                            </div>
                          </div>
                        );
                      })}

                      {visiblePapers < results.length && (
                        <div ref={loadMoreRef} className="py-4 text-center text-xs text-[var(--text-muted)]">
                          Loading more papers...
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </main>

      {/* ======================================================== */}
      {/* 2z. DOCUMENT CHAT (Phase 8) */}
      {/* ======================================================== */}
      <AnimatePresence>
      {docChatIds && (
        <ModalShell
          key="docChat"
          className="fixed inset-0 z-[65] flex items-stretch sm:items-center justify-center bg-black/50 backdrop-blur-xs p-0 sm:p-4"
          onClick={() => setDocChatIds(null)}
        >
          <ModalPanel
            role="dialog"
            aria-modal="true"
            aria-label="Chat with documents"
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-3xl h-full sm:h-[85vh] bg-[var(--bg-surface)] border border-[var(--border-color)] sm:rounded-2xl shadow-2xl flex flex-col overflow-hidden"
          >
            <div className="p-3 sm:p-4 border-b border-[var(--border-color)] flex items-start gap-2 shrink-0">
              <FileText className="w-5 h-5 text-[var(--accent)] shrink-0 mt-0.5" />
              <div className="min-w-0 mr-auto">
                <h3 className="font-serif-heading text-base font-bold text-[var(--text-primary)]">Chat with your documents</h3>
                <div className="text-[11px] text-[var(--text-muted)] truncate">
                  {documents.filter((d) => docChatIds.includes(d.id)).map((d) => d.title).join(" · ")}
                </div>
              </div>
              <button
                onClick={() => setDocChatIds(null)}
                aria-label="Close"
                className="p-1.5 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3 sm:p-5 space-y-4">
              {docMessages.length === 0 && (
                <p className="text-sm text-[var(--text-secondary)]">
                  Ask anything about the full text, for example &quot;What methods did they use?&quot; or &quot;Summarise the
                  limitations.&quot; Answers quote the matching pages.
                </p>
              )}
              {docMessages.map((m) => (
                <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div
                    className={`max-w-[90%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                      m.role === "user" ? "bg-[var(--accent)] text-white whitespace-pre-wrap" : "bg-[var(--bg-primary)] border border-[var(--border-color)]"
                    }`}
                  >
                    {m.role === "assistant" ? <Markdown text={m.content} /> : m.content}
                    {m.citations && m.citations.length > 0 && (
                      <div className="mt-3 pt-2 border-t border-[var(--border-color)] flex flex-wrap gap-1.5">
                        {m.citations.map((c) => (
                          <span key={c.n} className="text-[11px] px-2 py-0.5 rounded-md bg-[var(--bg-surface-elevated)] border border-[var(--border-color)]">
                            <span className="font-semibold text-[var(--accent)]">[{c.n}]</span> {c.title.slice(0, 40)}
                            {c.title.length > 40 ? "…" : ""}, p. {c.page}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {docAsking && <p className="text-xs text-[var(--text-muted)]">Reading the matching pages...</p>}
              {docChatError && <p className="text-xs text-red-600">{docChatError}</p>}
            </div>
            <form onSubmit={askDocuments} className="p-3 border-t border-[var(--border-color)] flex gap-2 shrink-0">
              <input
                value={docQuestion}
                onChange={(e) => setDocQuestion(e.target.value)}
                placeholder="Ask about these documents..."
                maxLength={2000}
                className="flex-1 min-w-0 px-3 py-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] text-sm text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
                aria-label="Question about the documents"
              />
              <button
                type="submit"
                disabled={docAsking || !docQuestion.trim()}
                className="px-3 py-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-50 text-white text-xs font-semibold flex items-center gap-1"
              >
                <Send className="w-3.5 h-3.5" /> Ask
              </button>
            </form>
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>

      {/* ======================================================== */}
      {/* 2y. CITATION MAP (Phase 8) */}
      {/* ======================================================== */}
      <AnimatePresence>
      {graphPaper && (
        <ModalShell
          key="graph"
          className="fixed inset-0 z-[65] flex items-stretch sm:items-center justify-center bg-black/50 backdrop-blur-xs p-0 sm:p-4"
          onClick={() => setGraphPaper(null)}
        >
          <ModalPanel
            role="dialog"
            aria-modal="true"
            aria-label="Citation map"
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-6xl h-full sm:h-[88vh] bg-[var(--bg-surface)] border border-[var(--border-color)] sm:rounded-2xl shadow-2xl flex flex-col overflow-hidden"
          >
            <div className="p-3 sm:p-4 border-b border-[var(--border-color)] flex items-start gap-2 shrink-0">
              <Network className="w-5 h-5 text-[var(--accent)] shrink-0 mt-0.5" />
              <div className="min-w-0 mr-auto">
                <h3 className="font-serif-heading text-base font-bold text-[var(--text-primary)]">Citation map</h3>
                <div className="text-[11px] text-[var(--text-muted)] truncate">{graphPaper.title}</div>
              </div>
              <button
                onClick={() => setGraphPaper(null)}
                aria-label="Close"
                className="p-1.5 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-3 sm:p-5">
              {!graphData && !graphError && <p className="text-sm text-[var(--text-secondary)]">Loading citations from OpenAlex...</p>}
              {graphError && <p className="text-sm text-red-600">{graphError}</p>}
              {graphData && (() => {
                const refs = graphData.nodes.filter((n) => n.kind === "reference");
                const citing = graphData.nodes.filter((n) => n.kind === "citing");
                const W = 1000;
                const rowH = 34;
                const H = Math.max(refs.length, citing.length, 3) * rowH + 40;
                const radius = (n: GraphNode) => 4 + Math.min(10, Math.log10((n.cited_by_count || 0) + 1) * 3);
                const label = (t: string) => (t.length > 46 ? `${t.slice(0, 45)}…` : t);
                const y = (i: number, count: number) => 20 + (H - 40) * ((i + 0.5) / Math.max(count, 1));
                const column = (title: string, list: GraphNode[], total: number, empty: string) => (
                  <div className="min-w-0">
                    <div className="text-xs font-semibold text-[var(--text-secondary)] mb-2">
                      {title} ({list.length}
                      {total > list.length ? ` of ${total.toLocaleString()}` : ""})
                    </div>
                    {list.length === 0 && <p className="text-xs text-[var(--text-muted)]">{empty}</p>}
                    <ul className="space-y-1.5">
                      {list.map((n) => (
                        <li key={n.id}>
                          <button
                            onClick={() => exploreGraphNode(n)}
                            disabled={!n.doi}
                            className="w-full text-left text-xs p-2 rounded-lg border border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)] disabled:opacity-70 disabled:cursor-default"
                            title={n.doi ? "Search for this paper" : "No DOI"}
                          >
                            <span className="font-medium text-[var(--text-primary)]">{n.title}</span>
                            <span className="block text-[10px] text-[var(--text-muted)]">
                              {n.year || "n.d."}
                              {typeof n.cited_by_count === "number" ? ` · ${n.cited_by_count.toLocaleString()} citations` : ""}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
                return (
                  <div className="space-y-5">
                    <svg viewBox={`0 0 ${W} ${H}`} className="hidden md:block w-full h-auto" role="img"
                      aria-label={`${refs.length} papers this paper cites on the left, ${citing.length} papers citing it on the right`}>
                      {refs.map((n, i) => (
                        <line key={`l-${n.id}`} x1={300} y1={y(i, refs.length)} x2={W / 2} y2={H / 2} stroke="var(--border-color)" />
                      ))}
                      {citing.map((n, i) => (
                        <line key={`r-${n.id}`} x1={W - 300} y1={y(i, citing.length)} x2={W / 2} y2={H / 2} stroke="var(--border-color)" />
                      ))}
                      {refs.map((n, i) => (
                        <g key={n.id} className="cursor-pointer" onClick={() => exploreGraphNode(n)}>
                          <title>{`${n.title} (${n.year || "n.d."})`}</title>
                          <circle cx={300} cy={y(i, refs.length)} r={radius(n)} fill="#64748b" />
                          <text x={288 - radius(n)} y={y(i, refs.length) + 4} textAnchor="end" fontSize="11" fill="var(--text-secondary)">
                            {label(n.title)}
                          </text>
                        </g>
                      ))}
                      {citing.map((n, i) => (
                        <g key={n.id} className="cursor-pointer" onClick={() => exploreGraphNode(n)}>
                          <title>{`${n.title} (${n.year || "n.d."})`}</title>
                          <circle cx={W - 300} cy={y(i, citing.length)} r={radius(n)} fill="#059669" />
                          <text x={W - 288 + radius(n)} y={y(i, citing.length) + 4} fontSize="11" fill="var(--text-secondary)">
                            {label(n.title)}
                          </text>
                        </g>
                      ))}
                      <circle cx={W / 2} cy={H / 2} r={16} fill="var(--accent)" />
                      <text x={W / 2} y={H / 2 + 34} textAnchor="middle" fontSize="12" fontWeight="bold" fill="var(--text-primary)">
                        This paper{graphData.seed.year ? ` (${graphData.seed.year})` : ""}
                      </text>
                      <text x={300} y={14} textAnchor="middle" fontSize="11" fill="var(--text-muted)">It cites ←</text>
                      <text x={W - 300} y={14} textAnchor="middle" fontSize="11" fill="var(--text-muted)">→ Cited by</text>
                    </svg>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      {column("This paper cites", refs, graphData.reference_total, "No references listed in OpenAlex.")}
                      {column("Cited by (most cited first)", citing, graphData.citing_total, "No citing papers found yet.")}
                    </div>
                    <p className="text-[11px] text-[var(--text-muted)]">
                      From OpenAlex. Dot size follows citation count. Choose a paper to search for it.
                    </p>
                  </div>
                );
              })()}
            </div>
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>

      {/* ======================================================== */}
      {/* 2a0. CITE DIALOG (Phase 7) */}
      {/* ======================================================== */}
      <AnimatePresence>
      {citeTarget && (
        <ModalShell
          key="citeTarget"
          className="fixed inset-0 z-[70] flex items-center justify-center bg-black/50 backdrop-blur-xs p-4"
          onClick={() => setCiteTarget(null)}
        >
          <ModalPanel
            role="dialog"
            aria-modal="true"
            aria-label="Cite this paper"
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-lg bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl p-5 space-y-4"
          >
            <div className="flex items-center justify-between gap-2">
              <h3 className="font-serif-heading text-base font-bold text-[var(--text-primary)] flex items-center gap-2">
                <Quote className="w-5 h-5 text-[var(--accent)]" /> Cite this paper
              </h3>
              <button
                onClick={() => setCiteTarget(null)}
                aria-label="Close"
                className="p-1 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div role="tablist" aria-label="Citation style" className="flex flex-wrap gap-1.5">
              {[...CITATION_STYLES, { id: "bibtex" as const, label: "BibTeX" }].map((style) => (
                <button
                  key={style.id}
                  role="tab"
                  aria-selected={citeStyle === style.id}
                  onClick={() => {
                    setCiteStyle(style.id);
                    setCiteCopied(false);
                  }}
                  className={`px-2.5 py-1 rounded-full border text-xs transition ${
                    citeStyle === style.id
                      ? "bg-[var(--accent)] text-white border-[var(--accent)]"
                      : "border-[var(--border-color)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-elevated)]"
                  }`}
                >
                  {style.label}
                </button>
              ))}
            </div>
            <div className="p-3 rounded-xl bg-[var(--bg-primary)] border border-[var(--border-color)] text-sm text-[var(--text-primary)] leading-relaxed break-words select-all">
              {citeStyle === "bibtex" ? (
                <pre className="text-xs whitespace-pre-wrap font-mono">{buildBibtex(citeTarget)}</pre>
              ) : (
                formatCitation(citeTarget, citeStyle).map((segment, i) =>
                  segment.italic ? <i key={i}>{segment.text}</i> : <span key={i}>{segment.text}</span>,
                )
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                onClick={() => copyCitation(citeTarget, citeStyle)}
                className="px-3 py-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-xs font-semibold flex items-center gap-1.5"
              >
                {citeCopied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                {citeCopied ? "Copied" : "Copy"}
              </button>
              <button
                onClick={() => exportReferences([citeTarget], "ris", citeTarget.title)}
                className="px-3 py-2 rounded-lg border border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)] text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1.5"
                title="Opens in Zotero, Mendeley and EndNote"
              >
                <Download className="w-3.5 h-3.5" /> RIS file
              </button>
              <button
                onClick={() => exportReferences([citeTarget], "bibtex", citeTarget.title)}
                className="px-3 py-2 rounded-lg border border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)] text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1.5"
              >
                <Download className="w-3.5 h-3.5" /> BibTeX file
              </button>
            </div>
            <p className="text-[11px] text-[var(--text-muted)]">
              Volume, issue and page numbers are not available from the search sources, so check them before submitting.
            </p>
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>

      {/* ======================================================== */}
      {/* 2a. SHARE DIALOG (Phase 6) */}
      {/* ======================================================== */}
      <AnimatePresence>
      {shareTarget && (
        <ModalShell
          key="shareTarget"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4"
          onClick={() => setShareTarget(null)}
        >
          <ModalPanel
            role="dialog"
            aria-modal="true"
            aria-label="Share conversation"
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-md bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl p-5 space-y-4"
          >
            <div className="flex items-center justify-between gap-2">
              <h3 className="font-serif-heading text-base font-bold text-[var(--text-primary)] flex items-center gap-2 truncate">
                <Share2 className="w-5 h-5 text-[var(--accent)] shrink-0" />
                <span className="truncate">Share &quot;{shareTarget.title}&quot;</span>
              </h3>
              <button
                onClick={() => setShareTarget(null)}
                aria-label="Close"
                className="p-1 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            {shareTarget.shared && shareTarget.share_token ? (
              <>
                <p className="text-xs text-[var(--text-secondary)]">
                  Anyone with this link can read the questions, answers and papers. They cannot see your name or email,
                  and cannot change anything. Saving changes later updates what the link shows.
                </p>
                <div className="flex gap-2">
                  <input
                    readOnly
                    value={shareUrl(shareTarget.share_token)}
                    onFocus={(e) => e.currentTarget.select()}
                    className="flex-1 min-w-0 px-3 py-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] text-xs text-[var(--text-primary)]"
                    aria-label="Share link"
                  />
                  <button
                    onClick={() => copyShareLink(shareTarget.share_token as string)}
                    className="px-3 py-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-xs font-semibold flex items-center gap-1"
                  >
                    {shareCopied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                    {shareCopied ? "Copied" : "Copy"}
                  </button>
                </div>
                <button
                  onClick={() => setSharing(shareTarget, false)}
                  disabled={shareBusy}
                  className="text-xs text-red-600 hover:underline disabled:opacity-60"
                >
                  Stop sharing (the link stops working at once)
                </button>
              </>
            ) : (
              <>
                <p className="text-xs text-[var(--text-secondary)]">
                  Create a read-only link to this conversation. Only people you give the link to can open it, it is hidden
                  from search engines, and you can switch it off at any time.
                </p>
                <button
                  onClick={() => setSharing(shareTarget, true)}
                  disabled={shareBusy}
                  className="w-full px-3 py-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-60 text-white text-xs font-semibold flex items-center justify-center gap-1.5"
                >
                  <Link2 className="w-3.5 h-3.5" />
                  {shareBusy ? "Creating link..." : "Create share link"}
                </button>
              </>
            )}
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>

      {/* ======================================================== */}
      {/* 2b. TABLE VIEW OVERLAY (Phase 2) */}
      {/* ======================================================== */}
      <AnimatePresence>
      {tableOpen && (
        <ModalShell
          key="tableOpen"
          className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/50 backdrop-blur-xs p-2 sm:p-4"
          onClick={() => setTableOpen(false)}
        >
          <ModalPanel
            role="dialog"
            aria-modal="true"
            aria-label="Papers table"
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-7xl bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl overflow-hidden flex flex-col"
          >
            <div className="p-3 sm:p-4 border-b border-[var(--border-color)] flex flex-wrap items-center gap-2 shrink-0">
              <Table2 className="w-5 h-5 text-[var(--accent)]" />
              <h3 className="font-serif-heading text-base font-bold text-[var(--text-primary)] mr-auto">
                Papers ({results.length})
              </h3>
              {canCompare && (
                <button
                  type="button"
                  onClick={handleCompare}
                  className="px-2.5 py-1.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-xs font-semibold flex items-center gap-1 transition"
                >
                  <Scale className="w-3.5 h-3.5" />
                  <span>Compare ({selectedForCompare.length})</span>
                </button>
              )}
              {papersMissingTakeaways.length > 0 && (
                <button
                  type="button"
                  onClick={handleGetTakeaways}
                  disabled={takeawaysLoading}
                  className="px-2.5 py-1.5 rounded-lg border border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)] disabled:opacity-60 text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1 transition"
                >
                  <Lightbulb className="w-3.5 h-3.5" />
                  <span>{takeawaysLoading ? "Getting takeaways..." : `Get takeaways (${Math.min(10, papersMissingTakeaways.length)})`}</span>
                </button>
              )}
              <button
                type="button"
                onClick={handleExportCsv}
                className="px-2.5 py-1.5 rounded-lg border border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)] text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1 transition"
              >
                <Download className="w-3.5 h-3.5" />
                <span>Export CSV</span>
              </button>
              {(["ris", "bibtex"] as const).map((format) => {
                const chosen = selectedForCompare.length > 0 ? selectedForCompare.map((id) => results[id - 1]) : sortedTableRows.map((r) => r.paper);
                return (
                  <button
                    key={format}
                    type="button"
                    onClick={() => exportReferences(chosen, format, query || "papers")}
                    className="px-2.5 py-1.5 rounded-lg border border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)] text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1 transition"
                    title={`${selectedForCompare.length > 0 ? "Ticked papers" : "All papers"} as ${format === "ris" ? "RIS (Zotero, Mendeley, EndNote)" : "BibTeX (LaTeX)"}`}
                  >
                    <Quote className="w-3.5 h-3.5" />
                    <span>
                      {format === "ris" ? "RIS" : "BibTeX"}
                      {selectedForCompare.length > 0 ? ` (${selectedForCompare.length})` : ""}
                    </span>
                  </button>
                );
              })}
              <button
                type="button"
                onClick={() => setTableOpen(false)}
                aria-label="Close table"
                className="p-1.5 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            {takeawaysError && (
              <div className="px-4 py-2 text-xs text-red-600 border-b border-[var(--border-color)]">{takeawaysError}</div>
            )}

            <div className="flex-1 overflow-auto">
              <table className="w-full min-w-[960px] text-xs text-left border-collapse">
                <thead className="sticky top-0 z-10 bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)]">
                  <tr>
                    <th className="p-2 w-8" aria-label="Select" />
                    {(
                      [
                        ["rank", "#", "w-10"],
                        ["title", "Title", "min-w-[260px]"],
                        ["year", "Year", "w-16"],
                        ["citations", "Citations", "w-20"],
                      ] as [TableSortKey, string, string][]
                    ).map(([key, label, width]) => (
                      <th key={key} className={`p-2 font-semibold ${width}`}>
                        <button
                          type="button"
                          onClick={() => toggleTableSort(key)}
                          className="flex items-center gap-1 hover:text-[var(--accent)] transition"
                        >
                          <span>{label}</span>
                          {tableSort.key === key ? (
                            tableSort.dir === "asc" ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />
                          ) : (
                            <ArrowUpDown className="w-3 h-3 opacity-40" />
                          )}
                        </button>
                      </th>
                    ))}
                    <th className="p-2 font-semibold w-28">Study type</th>
                    <th className="p-2 font-semibold w-28">Status</th>
                    <th className="p-2 font-semibold w-36">Source</th>
                    <th className="p-2 font-semibold min-w-[240px]">Takeaway</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedTableRows.map(({ paper, id }) => {
                    const selected = selectedPaperIds.includes(id);
                    const takeaway = takeaways[paperKey(paper)];
                    return (
                      <tr
                        key={id}
                        className={`border-t border-[var(--border-subtle)] align-top ${
                          selected ? "bg-[var(--accent-light)]/40" : "hover:bg-[var(--bg-surface-elevated)]"
                        }`}
                      >
                        <td className="p-2">
                          <button
                            type="button"
                            onClick={() => togglePaperSelection(id)}
                            aria-label={selected ? `Unselect paper ${id}` : `Select paper ${id}`}
                            className="text-[var(--accent)] hover:opacity-80 transition"
                          >
                            {selected ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4 text-[var(--text-muted)]" />}
                          </button>
                        </td>
                        <td className="p-2 font-bold text-[var(--accent)]">[{id}]</td>
                        <td className="p-2">
                          <button
                            type="button"
                            onClick={() => {
                              setSelectedPaperForDetail({ paper, index: id - 1 });
                              setTableOpen(false);
                            }}
                            className="text-left font-semibold text-[var(--text-primary)] hover:text-[var(--accent)] hover:underline transition"
                          >
                            {paper.title}
                          </button>
                          <div className="mt-0.5 text-[11px] text-[var(--text-muted)] line-clamp-1">
                            {paper.authors.length > 0 ? paper.authors.slice(0, 3).join(", ") : "Unknown"}
                            {paper.authors.length > 3 ? " et al." : ""}
                          </div>
                        </td>
                        <td className="p-2 text-[var(--text-secondary)]">{paper.publication_year ?? "-"}</td>
                        <td className="p-2 text-[var(--text-secondary)]">
                          {typeof paper.cited_by_count === "number" ? paper.cited_by_count.toLocaleString() : "-"}
                        </td>
                        <td className="p-2 text-[var(--text-secondary)]">{primaryStudyType(paper) || "-"}</td>
                        <td className="p-2">
                          <div className="flex flex-wrap gap-1">
                            {paper.is_retracted && (
                              <span className="px-1.5 py-0.5 rounded-md text-[10px] font-bold uppercase bg-red-600 text-white">Retracted</span>
                            )}
                            {paper.is_preprint && (
                              <span className="px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-amber-500/15 text-amber-600 border border-amber-500/30">Preprint</span>
                            )}
                            {paper.is_open_access === true && (
                              <span className="px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-emerald-500/15 text-emerald-600 border border-emerald-500/30">Open access</span>
                            )}
                            {!paper.is_retracted && !paper.is_preprint && paper.is_open_access !== true && (
                              <span className="text-[var(--text-muted)]">-</span>
                            )}
                          </div>
                        </td>
                        <td className="p-2 text-[var(--text-secondary)]">
                          <div className="line-clamp-2">{paper.description}</div>
                          {paper.database && <div className="text-[10px] text-[var(--text-muted)]">via {paper.database}</div>}
                        </td>
                        <td className="p-2 text-[var(--text-primary)] leading-relaxed">
                          {takeaway ? (
                            takeaway
                          ) : !hasEvidence(paper) ? (
                            <span className="text-[var(--text-muted)]">{paper.is_retracted ? "Retracted" : "No abstract"}</span>
                          ) : paperKey(paper) in takeaways ? (
                            <span className="text-[var(--text-muted)]">Not available</span>
                          ) : (
                            <span className="text-[var(--text-muted)]">Not generated yet</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="px-4 py-2 border-t border-[var(--border-color)] text-[11px] text-[var(--text-muted)] shrink-0">
              Click a column title to sort. Ticked papers scope your follow-up questions; tick 2 to 5 to compare. Takeaways are
              AI-written from abstracts only.
            </div>
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>

      {/* ======================================================== */}
      {/* 2c. COMPARE OVERLAY (Phase 2) */}
      {/* ======================================================== */}
      <AnimatePresence>
      {compareOpen && (
        <ModalShell
          key="compareOpen"
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/50 backdrop-blur-xs p-2 sm:p-4"
          onClick={() => setCompareOpen(false)}
        >
          <ModalPanel
            role="dialog"
            aria-modal="true"
            aria-label="Paper comparison"
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-6xl max-h-[92vh] bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl overflow-hidden flex flex-col"
          >
            <div className="p-3 sm:p-4 border-b border-[var(--border-color)] flex flex-wrap items-center gap-2 shrink-0">
              <Scale className="w-5 h-5 text-[var(--accent)]" />
              <h3 className="font-serif-heading text-base font-bold text-[var(--text-primary)] mr-auto">
                Compare papers
              </h3>
              {comparison && (
                <>
                  <button
                    type="button"
                    onClick={handleCopyComparison}
                    className="px-2.5 py-1.5 rounded-lg border border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)] text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1 transition"
                  >
                    {compareCopied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
                    <span>{compareCopied ? "Copied" : "Copy as table"}</span>
                  </button>
                  <button
                    type="button"
                    onClick={handleExportComparisonPdf}
                    className="px-2.5 py-1.5 rounded-lg border border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)] text-xs font-medium text-[var(--text-secondary)] flex items-center gap-1 transition"
                  >
                    <FileDown className="w-3.5 h-3.5" />
                    <span>Export PDF</span>
                  </button>
                </>
              )}
              <button
                type="button"
                onClick={() => setCompareOpen(false)}
                aria-label="Close comparison"
                className="p-1.5 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex-1 overflow-auto p-3 sm:p-4 space-y-4">
              {compareLoading && (
                <div className="py-12 text-center text-sm text-[var(--text-secondary)]">
                  Comparing {selectedForCompare.length} papers from their abstracts...
                </div>
              )}
              {compareError && !compareLoading && (
                <div className="p-3 rounded-xl border border-red-500/30 bg-red-500/10 text-sm text-red-600">{compareError}</div>
              )}
              {comparison && !compareLoading && (
                <>
                  <div className="p-4 rounded-xl bg-[var(--bg-primary)] border border-[var(--border-color)] text-sm">
                    <Markdown text={comparison.summary} compact />
                  </div>
                  <div className="overflow-x-auto rounded-xl border border-[var(--border-color)]">
                    <table className="w-full min-w-[720px] text-xs text-left border-collapse">
                      <thead className="bg-[var(--bg-surface-elevated)]">
                        <tr>
                          <th className="p-2 w-32" />
                          {comparison.rows.map((row) => (
                            <th key={row.paper_id} className="p-2 align-top font-semibold text-[var(--text-primary)]">
                              <span className="text-[var(--accent)]">[{row.paper_id}]</span> {row.title}
                              {row.publication_year ? (
                                <span className="font-normal text-[var(--text-muted)]"> ({row.publication_year})</span>
                              ) : null}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {COMPARE_LABELS.map(([key, label]) => (
                          <tr key={key} className="border-t border-[var(--border-subtle)] align-top">
                            <th scope="row" className="p-2 font-semibold text-[var(--text-secondary)]">{label}</th>
                            {comparison.rows.map((row) => {
                              const value = String(row[key] ?? "");
                              return (
                                <td
                                  key={row.paper_id}
                                  className={`p-2 leading-relaxed ${value === "Not stated" ? "text-[var(--text-muted)] italic" : "text-[var(--text-primary)]"}`}
                                >
                                  {value || "Not stated"}
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="text-[11px] text-[var(--text-muted)]">
                    AI-written from the abstracts only. &quot;Not stated&quot; means the abstract does not say. Papers without an
                    abstract and retracted papers are left out.
                  </p>
                </>
              )}
            </div>
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>

      {/* ======================================================== */}
      {/* 3. SETTINGS MODAL */}
      {/* ======================================================== */}
      <AnimatePresence>
      {settingsModalOpen && (
        <ModalShell key="settingsModalOpen" className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <ModalPanel className="w-full max-w-lg bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
            <div className="p-4 border-b border-[var(--border-color)] flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Settings className="w-5 h-5 text-[var(--accent)]" />
                <h3 className="font-serif-heading text-base font-bold text-[var(--text-primary)]">
                  Account Settings
                </h3>
              </div>
              <button
                onClick={() => setSettingsModalOpen(false)}
                className="p-1 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Settings Tabs */}
            <div className="flex border-b border-[var(--border-color)] px-4">
              <button
                onClick={() => setSettingsTab("account")}
                className={`py-2.5 px-4 text-xs font-semibold border-b-2 transition ${
                  settingsTab === "account"
                    ? "border-[var(--accent)] text-[var(--accent)]"
                    : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                }`}
              >
                Profile & Overview
              </button>
              <button
                onClick={() => setSettingsTab("password")}
                className={`py-2.5 px-4 text-xs font-semibold border-b-2 transition ${
                  settingsTab === "password"
                    ? "border-[var(--accent)] text-[var(--accent)]"
                    : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                }`}
              >
                Change Password
              </button>
              <button
                onClick={() => setSettingsTab("danger")}
                className={`py-2.5 px-4 text-xs font-semibold border-b-2 transition ${
                  settingsTab === "danger"
                    ? "border-red-600 text-red-600"
                    : "border-transparent text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                }`}
              >
                Danger Zone
              </button>
            </div>

            <div className="p-5 flex-1 overflow-y-auto space-y-4 text-xs">
              {settingsTab === "account" && (
                <div className="space-y-4">
                  <div className="p-4 rounded-xl bg-[var(--bg-primary)] border border-[var(--border-color)] space-y-3">
                    <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                      <span className="text-[var(--text-secondary)]">Full Name:</span>
                      <span className="font-semibold text-[var(--text-primary)]">
                        {user?.full_name || "Not specified"}
                      </span>
                    </div>
                    <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                      <span className="text-[var(--text-secondary)]">Account Email:</span>
                      <span className="font-semibold text-[var(--text-primary)]">{user?.email}</span>
                    </div>
                    <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                      <span className="text-[var(--text-secondary)]">Email Verification:</span>
                      <span
                        className={`font-semibold ${
                          user?.is_verified ? "text-emerald-600" : "text-amber-600"
                        }`}
                      >
                        {user?.is_verified ? "Verified" : "Pending Verification"}
                      </span>
                    </div>
                    <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                      <span className="text-[var(--text-secondary)]">Saved Library Papers:</span>
                      <span className="font-semibold text-[var(--accent)]">{bookmarks.length}</span>
                    </div>
                    <div className="flex justify-between py-1">
                      <span className="text-[var(--text-secondary)]">Total Search History:</span>
                      <span className="font-semibold text-[var(--text-primary)]">{history.length}</span>
                    </div>
                  </div>
                </div>
              )}

              {settingsTab === "password" && (
                <form onSubmit={handleChangePassword} className="space-y-4">
                  {passwordError && (
                    <div className="p-3 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900/50 text-red-600 dark:text-red-400">
                      {passwordError}
                    </div>
                  )}
                  {passwordSuccess && (
                    <div className="p-3 rounded-lg bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-900/50 text-emerald-600 dark:text-emerald-400">
                      {passwordSuccess}
                    </div>
                  )}

                  <div className="space-y-1">
                    <label className="text-[var(--text-secondary)] font-medium">Current Password</label>
                    <input
                      type="password"
                      value={currentPassword}
                      onChange={(e) => setCurrentPassword(e.target.value)}
                      required
                      className="w-full p-2.5 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] focus:ring-2 focus:ring-[var(--accent)] focus:outline-none"
                    />
                  </div>

                  <div className="space-y-1">
                    <label className="text-[var(--text-secondary)] font-medium">
                      New Password (min 8 characters)
                    </label>
                    <input
                      type="password"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      required
                      minLength={8}
                      className="w-full p-2.5 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] focus:ring-2 focus:ring-[var(--accent)] focus:outline-none"
                    />
                  </div>

                  <div className="space-y-1">
                    <label className="text-[var(--text-secondary)] font-medium">Confirm New Password</label>
                    <input
                      type="password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      required
                      minLength={8}
                      className="w-full p-2.5 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] focus:ring-2 focus:ring-[var(--accent)] focus:outline-none"
                    />
                  </div>

                  <button
                    type="submit"
                    disabled={passwordLoading}
                    className="w-full py-2.5 bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white font-medium rounded-lg transition"
                  >
                    {passwordLoading ? "Updating..." : "Update Password"}
                  </button>
                </form>
              )}

              {settingsTab === "danger" && (
                <div className="p-4 rounded-xl border border-red-200 dark:border-red-900/50 bg-red-50/50 dark:bg-red-950/20 space-y-3">
                  <div className="font-semibold text-red-600 dark:text-red-400">
                    Delete Account Permanently
                  </div>
                  <p className="text-[var(--text-secondary)] leading-relaxed">
                    Deleting your account will permanently delete your profile, saved library bookmarks, and all associated search history. This action cannot be undone.
                  </p>
                  <button
                    onClick={() => setDeleteAccountModalOpen(true)}
                    className="py-2 px-4 bg-red-600 hover:bg-red-700 text-white font-semibold rounded-lg transition"
                  >
                    Delete My Account
                  </button>
                </div>
              )}
            </div>
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>

      {/* Delete Confirmation Modal */}
      <AnimatePresence>
      {deleteAccountModalOpen && (
        <ModalShell key="deleteAccountModalOpen" className="fixed inset-0 z-60 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4">
          <ModalPanel className="w-full max-w-sm bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl p-5 space-y-4 shadow-2xl">
            <h4 className="font-serif-heading text-base font-bold text-red-600">
              Confirm Account Deletion
            </h4>
            <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
              Please enter your password to confirm that you want to permanently delete your account.
            </p>

            {deleteError && (
              <div className="p-2.5 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900/50 text-xs text-red-600">
                {deleteError}
              </div>
            )}

            <form onSubmit={handleDeleteAccount} className="space-y-3">
              <input
                type="password"
                placeholder="Confirm password"
                value={deletePasswordConfirm}
                onChange={(e) => setDeletePasswordConfirm(e.target.value)}
                className="w-full p-2.5 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] focus:ring-2 focus:ring-red-600 focus:outline-none"
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setDeleteAccountModalOpen(false)}
                  className="flex-1 py-2 rounded-lg bg-[var(--bg-surface-elevated)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] font-medium"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={deleteLoading}
                  className="flex-1 py-2 rounded-lg bg-red-600 hover:bg-red-700 text-white text-xs font-semibold transition"
                >
                  {deleteLoading ? "Deleting..." : "Confirm Delete"}
                </button>
              </div>
            </form>
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>

      {/* ======================================================== */}
      {/* 4. COMPLIANCE & POLICY MODALS */}
      {/* ======================================================== */}
      <AnimatePresence>
      {activePolicyModal && (
        <ModalShell key="activePolicyModal" className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <ModalPanel className="w-full max-w-xl bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh]">
            <div className="p-4 border-b border-[var(--border-color)] flex items-center justify-between">
              <h3 className="font-serif-heading text-base font-bold text-[var(--text-primary)]">
                {activePolicyModal === "privacy" && "Privacy Policy"}
                {activePolicyModal === "terms" && "Terms of Service"}
                {activePolicyModal === "cookie_policy" && "Cookie Policy"}
                {activePolicyModal === "cookie_prefs" && "Cookie Preferences"}
              </h3>
              <button
                onClick={() => setActivePolicyModal(null)}
                className="p-1 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-6 flex-1 overflow-y-auto space-y-4 text-xs text-[var(--text-secondary)] leading-relaxed">
              {activePolicyModal === "privacy" && (
                <>
                  <p className="font-semibold text-[var(--text-primary)]">Last updated: September 2026</p>
                  <p>
                    {APP_NAME} is committed to protecting your academic research privacy. We process query metadata strictly to provide federated academic search, citation generation, and synthesized literature insights.
                  </p>
                  <h4 className="font-bold text-[var(--text-primary)]">Data Collection & Use</h4>
                  <p>
                    When you sign up, we securely store your email and hashed password. We do not sell your personal data or search queries to third-party advertisers.
                  </p>
                  <h4 className="font-bold text-[var(--text-primary)]">Third-Party Academic APIs</h4>
                  <p>
                    Queries are dispatched to public academic APIs (OpenAlex, Semantic Scholar, arXiv, Crossref, CORE, PubMed, Europe PMC, DOAJ, ERIC, OpenAIRE, and African Journals Online, plus Unpaywall for free PDF links) to retrieve research metadata.
                  </p>
                </>
              )}

              {activePolicyModal === "terms" && (
                <>
                  <p className="font-semibold text-[var(--text-primary)]">Last updated: September 2026</p>
                  <p>
                    By using {APP_NAME}, you agree to comply with standard academic integrity and acceptable use guidelines.
                  </p>
                  <h4 className="font-bold text-[var(--text-primary)]">Academic Synthesis Disclaimer</h4>
                  <p>
                    AI-synthesized research summaries and citations are generated for academic acceleration and exploratory discovery. Users are advised to review original publication DOIs before formal publication.
                  </p>
                </>
              )}

              {activePolicyModal === "cookie_policy" && (
                <>
                  <p className="font-semibold text-[var(--text-primary)]">Last updated: September 2026</p>
                  <p>
                    <span className="font-semibold text-[var(--text-primary)]">Strictly necessary (always on).</span>{" "}
                    A secure, httpOnly, SameSite session cookie that keeps you signed in, plus a random anonymous session ID and a
                    guest search counter stored in your browser so guest history and usage limits work.
                  </p>
                  <p>
                    <span className="font-semibold text-[var(--text-primary)]">Preferences (optional).</span>{" "}
                    Remembers your light/dark theme and your cookie choice. If you decline, the theme resets on your next visit.
                  </p>
                  <p>
                    We do not use advertising, tracking or analytics cookies. You can change your choice at any time from Cookie Preferences in the footer.
                  </p>
                </>
              )}

              {activePolicyModal === "cookie_prefs" && (
                <div className="space-y-4">
                  <div className="flex items-center justify-between p-3 rounded-xl bg-[var(--bg-primary)] border border-[var(--border-color)]">
                    <div>
                      <div className="font-semibold text-[var(--text-primary)]">Strictly Necessary Cookies</div>
                      <div className="text-[11px] text-[var(--text-muted)]">Essential httpOnly authentication and CSRF security tokens.</div>
                    </div>
                    <span className="font-semibold text-[var(--accent)] text-xs">Always Active</span>
                  </div>

                  <div className="flex items-center justify-between p-3 rounded-xl bg-[var(--bg-primary)] border border-[var(--border-color)]">
                    <div>
                      <div className="font-semibold text-[var(--text-primary)]">Preferences</div>
                      <div className="text-[11px] text-[var(--text-muted)]">Remembers your light/dark theme between visits.</div>
                    </div>
                    <input
                      type="checkbox"
                      checked={cookiePrefs.preferences}
                      onChange={(e) =>
                        setCookiePrefs((prev) => ({ ...prev, preferences: e.target.checked }))
                      }
                      aria-label="Allow preference storage"
                      className="w-4 h-4 accent-[var(--accent)] rounded"
                    />
                  </div>

                  <p className="text-[11px] text-[var(--text-muted)]">
                    {APP_NAME} does not use analytics or advertising cookies.
                  </p>

                  <button
                    onClick={() => saveCookieConsent(cookiePrefs.preferences)}
                    className="w-full py-2 bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white font-medium rounded-lg transition"
                  >
                    Save Preferences
                  </button>
                </div>
              )}
            </div>
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>

      {/* ======================================================== */}
      {/* 4b. COOKIE CONSENT BANNER (until a choice is stored) */}
      {/* ======================================================== */}
      <AnimatePresence>
      {!cookieDecided && activePolicyModal !== "cookie_prefs" && (
        <SlideUp
          key="cookie-banner"
          role="dialog"
          aria-label="Cookie consent"
          className="glass fixed bottom-4 left-4 right-4 sm:left-auto sm:right-6 sm:bottom-6 sm:max-w-md z-[60] rounded-2xl border border-[var(--border-color)] shadow-2xl p-4 text-xs text-[var(--text-secondary)]"
        >
          <div className="flex items-start gap-3">
            <Cookie className="w-5 h-5 text-[var(--accent)] shrink-0 mt-0.5" />
            <div className="space-y-3">
              <div>
                <div className="font-semibold text-[var(--text-primary)] text-sm">We use cookies</div>
                <p className="mt-1 leading-relaxed">
                  Essential cookies keep you signed in and the site secure. Optional preference storage remembers your
                  theme. We do not use advertising or tracking cookies.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={() => saveCookieConsent(true)}
                  className="px-3 py-1.5 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white font-medium transition"
                >
                  Allow all
                </button>
                <button
                  onClick={() => saveCookieConsent(false)}
                  className="px-3 py-1.5 rounded-lg border border-[var(--border-color)] bg-[var(--bg-surface)] hover:bg-[var(--bg-surface-elevated)] text-[var(--text-primary)] font-medium transition"
                >
                  Necessary only
                </button>
                <button
                  onClick={() => setActivePolicyModal("cookie_prefs")}
                  className="px-2 py-1.5 underline underline-offset-2 hover:text-[var(--accent)] transition"
                >
                  Customize
                </button>
              </div>
            </div>
          </div>
        </SlideUp>
      )}
      </AnimatePresence>

      {/* ======================================================== */}
      {/* 5. AUTH MODAL (LOGIN / REGISTER WITH FULL NAME) */}
      {/* ======================================================== */}
      <AnimatePresence>
      {authModalOpen && (
        <ModalShell key="authModalOpen" className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <ModalPanel className="w-full max-w-sm bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <BookOpen className="w-5 h-5 text-[var(--accent)]" />
                <h3 className="font-serif-heading text-lg font-bold text-[var(--text-primary)]">
                  {authMode === "login" ? "Welcome back" : "Create your account"}
                </h3>
              </div>
              <button
                onClick={() => setAuthModalOpen(false)}
                className="p-1 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {authPromptMessage && (
              <div className="p-3 rounded-xl bg-[var(--accent-light)] border border-[var(--accent)]/30 text-xs text-[var(--text-primary)]">
                {authPromptMessage}
              </div>
            )}

            {authError && (
              <div className="p-3 rounded-xl bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900/50 text-xs text-red-600">
                {authError}
              </div>
            )}

            {authSuccess && (
              <div className="p-3 rounded-xl bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-900/50 text-xs text-emerald-600">
                {authSuccess}
              </div>
            )}

            <form onSubmit={handleAuthSubmit} className="space-y-3 text-xs">
              {authMode === "register" && (
                <div className="space-y-1">
                  <label className="text-[var(--text-secondary)] font-medium">Full name (optional)</label>
                  <input
                    type="text"
                    value={authFullName}
                    onChange={(e) => setAuthFullName(e.target.value)}
                    placeholder="e.g. Dr. Jane Smith"
                    className="w-full p-2.5 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] focus:ring-2 focus:ring-[var(--accent)] focus:outline-none"
                  />
                </div>
              )}

              <div className="space-y-1">
                <label className="text-[var(--text-secondary)] font-medium">Email address</label>
                <input
                  type="email"
                  value={authEmail}
                  onChange={(e) => setAuthEmail(e.target.value)}
                  required
                  placeholder="name@university.edu"
                  className="w-full p-2.5 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] focus:ring-2 focus:ring-[var(--accent)] focus:outline-none"
                />
              </div>

              <div className="space-y-1">
                <div className="flex items-center justify-between">
                  <label className="text-[var(--text-secondary)] font-medium">Password</label>
                  {authMode === "login" && (
                    <button
                      type="button"
                      onClick={() => {
                        setAuthModalOpen(false);
                        setForgotEmail(authEmail);
                        setForgotPasswordModalOpen(true);
                      }}
                      className="text-[11px] text-[var(--accent)] hover:underline"
                    >
                      Forgot password?
                    </button>
                  )}
                </div>
                <input
                  type="password"
                  value={authPassword}
                  onChange={(e) => setAuthPassword(e.target.value)}
                  required
                  minLength={8}
                  placeholder="Minimum 8 characters"
                  className="w-full p-2.5 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] focus:ring-2 focus:ring-[var(--accent)] focus:outline-none"
                />
              </div>

              <button
                type="submit"
                disabled={authLoading}
                className="w-full py-2.5 bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white font-medium rounded-lg transition"
              >
                {authLoading ? "Processing..." : authMode === "login" ? "Sign In" : "Create Account"}
              </button>
            </form>

            <div className="text-center text-xs text-[var(--text-secondary)] pt-2 border-t border-[var(--border-subtle)]">
              {authMode === "login" ? (
                <div>
                  Don&apos;t have an account?{" "}
                  <button
                    onClick={() => {
                      setAuthMode("register");
                      setAuthError("");
                    }}
                    className="text-[var(--accent)] font-semibold hover:underline"
                  >
                    Register free
                  </button>
                </div>
              ) : (
                <div>
                  Already have an account?{" "}
                  <button
                    onClick={() => {
                      setAuthMode("login");
                      setAuthError("");
                    }}
                    className="text-[var(--accent)] font-semibold hover:underline"
                  >
                    Sign in
                  </button>
                </div>
              )}
            </div>
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>

      {/* ======================================================== */}
      {/* 6. FORGOT PASSWORD MODAL */}
      {/* ======================================================== */}
      <AnimatePresence>
      {forgotPasswordModalOpen && (
        <ModalShell key="forgotPasswordModalOpen" className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <ModalPanel className="w-full max-w-sm bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Key className="w-5 h-5 text-[var(--accent)]" />
                <h3 className="font-serif-heading text-lg font-bold text-[var(--text-primary)]">
                  Reset Password
                </h3>
              </div>
              <button
                onClick={() => setForgotPasswordModalOpen(false)}
                className="p-1 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
              Enter your registered email address to receive password reset instructions.
            </p>

            {forgotError && (
              <div className="p-3 rounded-xl bg-red-50 dark:bg-red-950/30 border border-red-200 text-xs text-red-600">
                {forgotError}
              </div>
            )}

            {forgotSuccess && (
              <div className="p-3 rounded-xl bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 text-xs text-emerald-600">
                {forgotSuccess}
              </div>
            )}

            <form onSubmit={handleForgotPasswordSubmit} className="space-y-3 text-xs">
              <input
                type="email"
                value={forgotEmail}
                onChange={(e) => setForgotEmail(e.target.value)}
                required
                placeholder="name@university.edu"
                className="w-full p-2.5 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] focus:ring-2 focus:ring-[var(--accent)] focus:outline-none"
              />
              <button
                type="submit"
                disabled={forgotLoading}
                className="w-full py-2.5 bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white font-medium rounded-lg transition"
              >
                {forgotLoading ? "Sending..." : "Send Reset Instructions"}
              </button>
            </form>
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>

      {/* ======================================================== */}
      {/* 7. RESET PASSWORD MODAL (FROM URL TOKEN) */}
      {/* ======================================================== */}
      <AnimatePresence>
      {resetPasswordModalOpen && (
        <ModalShell key="resetPasswordModalOpen" className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <ModalPanel className="w-full max-w-sm bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Key className="w-5 h-5 text-[var(--accent)]" />
                <h3 className="font-serif-heading text-lg font-bold text-[var(--text-primary)]">
                  Set New Password
                </h3>
              </div>
              <button
                onClick={() => setResetPasswordModalOpen(false)}
                className="p-1 rounded-lg hover:bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {resetError && (
              <div className="p-3 rounded-xl bg-red-50 dark:bg-red-950/30 border border-red-200 text-xs text-red-600">
                {resetError}
              </div>
            )}

            {resetSuccess && (
              <div className="p-3 rounded-xl bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 text-xs text-emerald-600">
                {resetSuccess}
              </div>
            )}

            <form onSubmit={handleResetPasswordSubmit} className="space-y-3 text-xs">
              <div className="space-y-1">
                <label className="text-[var(--text-secondary)] font-medium">New Password (min 8 chars)</label>
                <input
                  type="password"
                  value={resetNewPassword}
                  onChange={(e) => setResetNewPassword(e.target.value)}
                  required
                  minLength={8}
                  className="w-full p-2.5 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] focus:ring-2 focus:ring-[var(--accent)] focus:outline-none"
                />
              </div>

              <div className="space-y-1">
                <label className="text-[var(--text-secondary)] font-medium">Confirm New Password</label>
                <input
                  type="password"
                  value={resetConfirmPassword}
                  onChange={(e) => setResetConfirmPassword(e.target.value)}
                  required
                  minLength={8}
                  className="w-full p-2.5 rounded-lg bg-[var(--bg-primary)] border border-[var(--border-color)] text-xs text-[var(--text-primary)] focus:ring-2 focus:ring-[var(--accent)] focus:outline-none"
                />
              </div>

              <button
                type="submit"
                disabled={resetLoading}
                className="w-full py-2.5 bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white font-medium rounded-lg transition"
              >
                {resetLoading ? "Resetting..." : "Save New Password"}
              </button>
            </form>
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>

      {/* Soft Gate Modal */}
      <AnimatePresence>
      {softGateModalOpen && (
        <ModalShell key="softGateModalOpen" className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4">
          <ModalPanel className="w-full max-w-sm bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl p-6 space-y-4 shadow-2xl text-center">
            <div className="w-12 h-12 rounded-full bg-[var(--accent-light)] text-[var(--accent)] flex items-center justify-center mx-auto">
              <Sparkles className="w-6 h-6" />
            </div>

            <h3 className="font-serif-heading text-lg font-bold text-[var(--text-primary)]">
              Enjoying {APP_NAME}?
            </h3>

            <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
              You&apos;ve completed {GUEST_SEARCH_LIMIT} free guest searches. Create a free account to unlock unlimited searches, persist your research library, and access multi-turn research.
            </p>

            <div className="space-y-2 pt-2">
              <button
                onClick={() => {
                  setSoftGateModalOpen(false);
                  openAuthModal("register", "Create your free account to continue unlimited research.");
                }}
                className="w-full py-2.5 bg-[var(--accent)] hover:bg-[var(--accent-hover)] text-white text-xs font-semibold rounded-lg transition"
              >
                Create Free Account
              </button>
              <button
                onClick={() => setSoftGateModalOpen(false)}
                className="w-full py-2 text-xs text-[var(--text-muted)] hover:text-[var(--text-secondary)] transition"
              >
                Maybe later
              </button>
            </div>
          </ModalPanel>
        </ModalShell>
      )}
      </AnimatePresence>
    </div>
  );
}
