"use client";

import jsPDF from "jspdf";
import {
  ArrowRight,
  BookOpen,
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
  Library,
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
  User as UserIcon,
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
};

type Citation = {
  paper_id: number;
  title: string;
};

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  timestamp: string;
  scopedPaperIds?: number[];
};

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
};

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
  const [copiedBibtexIndex, setCopiedBibtexIndex] = useState<number | null>(null);
  const [copiedAnswerId, setCopiedAnswerId] = useState<string | null>(null);

  // Deep dive states
  const [deepDiveLoading, setDeepDiveLoading] = useState<Record<number, boolean>>({});
  const [deepDives, setDeepDives] = useState<Record<number, string>>({});

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

  // Feature 3: Bookmarks / My Library
  const [bookmarks, setBookmarks] = useState<BookmarkItem[]>([]);
  const [libraryLoading, setLibraryLoading] = useState(false);

  // Theme mode: 'light' or 'dark'
  const [theme, setTheme] = useState<"light" | "dark">("light");

  // Sidebar state: always starts collapsed on page load (open it with the toggle button)
  const [sidebarOpen, setSidebarOpen] = useState(false);
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

  const chatEndRef = useRef<HTMLDivElement>(null);
  const accountMenuRef = useRef<HTMLDivElement>(null);

  // Set at build time (see frontend/.env.example); next.config.ts refuses to start without it.
  const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL as string;

  // ============================================================
  // INITIALIZATION & URL PARAMETER HANDLING
  // ============================================================

  useEffect(() => {
    // 0. Cookie consent: show the banner until a choice has been stored
    try {
      const rawConsent = localStorage.getItem("scholarpulse-cookie-consent");
      if (rawConsent) {
        const stored = JSON.parse(rawConsent);
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
  }, []);

  useEffect(() => {
    if (messages.length > 0) {
      chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [messages, loading]);

  useEffect(() => {
    setVisiblePapers(10);
    setSelectedPaperIds([]);
  }, [results]);

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
          message: "Your email has been verified! Welcome to ScholarPulse.",
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
  }

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
    setQuery(item.question);
    if (item.papers && item.papers.length > 0) {
      setResults(item.papers);
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

  // ============================================================
  // SEARCH & QUESTION WORKFLOW
  // ============================================================

  function handleNewSearch() {
    setQuery("");
    setFollowUpQuery("");
    setMessages([]);
    setResults([]);
    setError("");
    setSelectedPaperForDetail(null);
    setSelectedPaperIds([]);
    if (window.innerWidth < 768) {
      setSidebarOpen(false);
    }
  }

  async function executeSearch(searchQuery: string) {
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
    setResults([]);
    setSelectedPaperForDetail(null);
    setSelectedPaperIds([]);
    setLoadingStep("Querying OpenAlex, Semantic Scholar, arXiv, Crossref, and CORE...");

    const userMessage: Message = {
      id: Date.now().toString(),
      role: "user",
      content: searchQuery,
      timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    };
    setMessages([userMessage]);

    try {
      const currentYear = new Date().getFullYear();
      let yearMin: number | undefined;
      if (yearFilter === "3") yearMin = currentYear - 2;
      if (yearFilter === "5") yearMin = currentYear - 4;
      if (yearFilter === "10") yearMin = currentYear - 9;

      const searchParams = new URLSearchParams({
        q: searchQuery,
        open_access_only: openAccessOnly.toString(),
      });
      if (yearMin) searchParams.append("year_min", yearMin.toString());

      const searchRes = await fetch(`${apiBaseUrl}/search?${searchParams.toString()}`, {
        credentials: "include",
      });
      if (!searchRes.ok) {
        const errData = await searchRes.json().catch(() => ({}));
        throw new Error(errData.detail || "Paper search failed.");
      }

      const searchData = await searchRes.json();
      const fetchedResults: Result[] = searchData.results || [];
      setResults(fetchedResults);

      if (fetchedResults.length === 0) {
        setMessages((prev) => [
          ...prev,
          {
            id: (Date.now() + 1).toString(),
            role: "assistant",
            content: "No matching academic papers found across OpenAlex, Semantic Scholar, arXiv, Crossref, or CORE. Try adjusting your search query or filters.",
            timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
          },
        ]);
        setLoading(false);
        setLoadingStep("");
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
        }),
      });

      if (!askRes.ok) {
        const errData = await askRes.json().catch(() => ({}));
        throw new Error(errData.detail || "AI synthesis failed.");
      }

      const askData = await askRes.json();
      const assistantMessage: Message = {
        id: (Date.now() + 2).toString(),
        role: "assistant",
        content: askData.answer || "Synthesized analysis completed.",
        citations: askData.citations || [],
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      };

      setMessages((prev) => [...prev, assistantMessage]);
      loadHistory();
    } catch (err: any) {
      setError(err.message || "An unexpected error occurred.");
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
      id: Date.now().toString(),
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
        }),
      });

      if (!askRes.ok) {
        const errData = await askRes.json().catch(() => ({}));
        throw new Error(errData.detail || "Follow-up synthesis failed.");
      }

      const askData = await askRes.json();
      const assistantMessage: Message = {
        id: (Date.now() + 1).toString(),
        role: "assistant",
        content: askData.answer || "Synthesis complete.",
        citations: askData.citations || [],
        timestamp: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
        scopedPaperIds: selectedPaperIds.length > 0 ? [...selectedPaperIds] : undefined,
      };

      setMessages((prev) => [...prev, assistantMessage]);
      loadHistory();
    } catch (err: any) {
      setError(err.message || "Failed to process follow-up question.");
    } finally {
      setLoading(false);
      setLoadingStep("");
    }
  }

  async function handleDeepDive(index: number, paper: Result) {
    if (deepDives[index]) return;
    setDeepDiveLoading((prev) => ({ ...prev, [index]: true }));

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

      if (!res.ok) throw new Error("Deep dive generation failed.");
      const data = await res.json();
      setDeepDives((prev) => ({ ...prev, [index]: data.deep_dive || data.analysis || "Analysis complete." }));
    } catch (err: any) {
      setDeepDives((prev) => ({ ...prev, [index]: "Unable to generate deep dive analysis for this paper." }));
    } finally {
      setDeepDiveLoading((prev) => ({ ...prev, [index]: false }));
    }
  }

  function handleCopyBibtex(index: number, paper: Result) {
    const firstAuthor = (paper.authors[0] || "Unknown").split(" ").pop()?.toLowerCase() || "author";
    const year = paper.publication_year || new Date().getFullYear();
    const citeKey = `${firstAuthor}${year}`;
    const bibtex = `@article{${citeKey},
  title={${paper.title}},
  author={${paper.authors.join(" and ")}},
  year={${year}},
  doi={${paper.doi || "N/A"}}
}`;
    navigator.clipboard.writeText(bibtex);
    setCopiedBibtexIndex(index);
    setTimeout(() => setCopiedBibtexIndex(null), 2500);
  }

  function handleCopyAnswer(message: Message) {
    navigator.clipboard.writeText(message.content);
    setCopiedAnswerId(message.id);
    setTimeout(() => setCopiedAnswerId(null), 2500);
  }

  function handleExportPDF(message: Message) {
    const doc = new jsPDF();
    doc.setFont("helvetica", "bold");
    doc.setFontSize(16);
    doc.text("ScholarPulse Synthesis Report", 14, 20);

    doc.setFontSize(10);
    doc.setFont("helvetica", "normal");
    doc.text(`Generated: ${new Date().toLocaleDateString()} | Query: ${query}`, 14, 28);

    doc.setFontSize(11);
    const splitText = doc.splitTextToSize(message.content, 180);
    doc.text(splitText, 14, 38);

    if (message.citations && message.citations.length > 0) {
      let yPos = 38 + splitText.length * 5 + 10;
      doc.setFont("helvetica", "bold");
      doc.setFontSize(12);
      doc.text("Cited References:", 14, yPos);
      yPos += 6;

      doc.setFont("helvetica", "normal");
      doc.setFontSize(10);
      message.citations.forEach((c) => {
        const citText = `[${c.paper_id}] ${c.title}`;
        const splitCit = doc.splitTextToSize(citText, 180);
        doc.text(splitCit, 14, yPos);
        yPos += splitCit.length * 5;
      });
    }

    doc.save(`research_synthesis_${Date.now()}.pdf`);
  }

  function togglePaperSelection(id: number) {
    setSelectedPaperIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  }

  const displayName = getDisplayName(user);
  const userInitials = getInitials(user);

  // ============================================================
  // RENDER
  // ============================================================

  return (
    <div className="flex h-screen w-full overflow-hidden bg-[var(--bg-primary)] text-[var(--text-primary)]">
      {/* Mobile Sidebar Backdrop Overlay */}
      {sidebarOpen && (
        <div
          onClick={() => setSidebarOpen(false)}
          className="fixed inset-0 z-40 bg-black/60 backdrop-blur-xs md:hidden transition-opacity"
        />
      )}

      {/* ======================================================== */}
      {/* 1. PERSISTENT & MOBILE SLIDE-OVER LEFT SIDEBAR */}
      {/* ======================================================== */}
      <aside
        className={`flex flex-col border-r border-[var(--border-color)] bg-[var(--bg-surface)] transition-all duration-200 z-50 ${
          sidebarOpen
            ? "fixed inset-y-0 left-0 w-72 shadow-2xl md:relative md:w-72 md:shadow-none"
            : "w-0 min-w-0 overflow-hidden border-none -translate-x-full md:translate-x-0"
        }`}
      >
        {/* Sidebar Header: Brand & New Search */}
        <div className="p-4 border-b border-[var(--border-color)] flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BookOpen className="w-5 h-5 text-[var(--accent)]" />
              <span className="font-serif-heading text-lg font-bold tracking-tight text-[var(--text-primary)]">
                ScholarPulse
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
                {!user ? (
                  <div className="text-xs text-[var(--text-muted)] px-2 py-2 italic">
                    No saved papers yet.
                  </div>
                ) : libraryLoading ? (
                  <div className="text-xs text-[var(--text-muted)] px-2 py-2">Loading library...</div>
                ) : bookmarks.length === 0 ? (
                  <div className="text-xs text-[var(--text-muted)] px-2 py-2 italic">
                    No bookmarked papers yet.
                  </div>
                ) : (
                  bookmarks.map((bm) => (
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
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          deleteBookmarkById(bm.id);
                        }}
                        className="opacity-0 group-hover:opacity-100 p-1 hover:text-[var(--accent)] transition"
                        title="Remove bookmark"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  ))
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
      </aside>

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
                  ScholarPulse
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
                  Search five academic databases at once: OpenAlex, Semantic Scholar, arXiv, Crossref and CORE.
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
                  </div>
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
                        Real-time discovery across five academic databases.
                      </p>
                    </div>
                  </div>
                )}

                <div className="pt-6 pb-6 border-t border-[var(--border-subtle)] flex flex-col sm:flex-row items-center justify-between text-[11px] text-[var(--text-muted)] gap-2">
                  <div>© 2026 ScholarPulse Inc. All rights reserved.</div>
                  <div>Federated academic discovery across five academic databases.</div>
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
                        <span>{m.role === "user" ? "You" : "ScholarPulse AI"}</span>
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
                        <div className="whitespace-pre-wrap">{m.content}</div>

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

                        {/* Action buttons for assistant answers */}
                        {m.role === "assistant" && (
                          <div className="mt-4 pt-3 border-t border-[var(--border-subtle)] flex items-center justify-end gap-2 text-xs text-[var(--text-secondary)]">
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
                                  : "Generate Deep Dive"}
                              </span>
                            </button>

                            <button
                              onClick={() =>
                                handleCopyBibtex(
                                  selectedPaperForDetail.index,
                                  selectedPaperForDetail.paper
                                )
                              }
                              className="flex items-center gap-1.5 px-3 py-2 bg-[var(--bg-surface-elevated)] hover:bg-[var(--border-color)] text-[var(--text-primary)] text-xs font-medium rounded-lg border border-[var(--border-color)] transition"
                            >
                              {copiedBibtexIndex === selectedPaperForDetail.index ? (
                                <>
                                  <Check className="w-3.5 h-3.5 text-emerald-600" />
                                  <span className="text-emerald-600">Copied BibTeX</span>
                                </>
                              ) : (
                                <>
                                  <Copy className="w-3.5 h-3.5" />
                                  <span>Copy BibTeX</span>
                                </>
                              )}
                            </button>

                            {selectedPaperForDetail.paper.doi && (
                              <a
                                href={`https://doi.org/${selectedPaperForDetail.paper.doi}`}
                                target="_blank"
                                rel="noreferrer"
                                className="flex items-center gap-1.5 px-3 py-2 bg-[var(--bg-surface-elevated)] hover:bg-[var(--border-color)] text-[var(--text-primary)] text-xs font-medium rounded-lg border border-[var(--border-color)] transition"
                              >
                                <ExternalLink className="w-3.5 h-3.5" />
                                <span>Open DOI</span>
                              </a>
                            )}
                          </div>

                          {deepDives[selectedPaperForDetail.index] && (
                            <div className="mt-4 p-4 rounded-xl bg-[var(--accent-light)] border border-[var(--accent)]/30 text-xs text-[var(--text-primary)] space-y-2">
                              <div className="font-semibold text-[var(--accent)] flex items-center gap-1.5">
                                <Compass className="w-4 h-4" />
                                <span>Deep Dive Synthesis:</span>
                              </div>
                              <p className="whitespace-pre-wrap leading-relaxed">
                                {deepDives[selectedPaperForDetail.index]}
                              </p>
                            </div>
                          )}
                        </>
                      ) : (
                        <div className="space-y-4 text-xs">
                          <div className="p-4 rounded-xl bg-[var(--bg-primary)] border border-[var(--border-color)] space-y-3">
                            <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                              <span className="text-[var(--text-secondary)] font-medium">Source Database:</span>
                              <span className="font-semibold text-[var(--text-primary)]">
                                {selectedPaperForDetail.paper.description}
                              </span>
                            </div>
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
                    <div className="p-4 border-b border-[var(--border-color)] flex items-center justify-between shrink-0">
                      <div className="flex items-center gap-2">
                        <BookOpen className="w-4 h-4 text-[var(--accent)]" />
                        <span className="font-semibold text-xs text-[var(--text-primary)]">
                          Retrieved Papers ({results.length})
                        </span>
                      </div>
                      <span className="text-[11px] text-[var(--text-muted)]">
                        Select checkboxes to scope context
                      </span>
                    </div>

                    <div ref={papersScrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
                      {results.slice(0, visiblePapers).map((paper, idx) => {
                        const paperId = idx + 1;
                        const isSelected = selectedPaperIds.includes(paperId);
                        const bookmarked = isPaperBookmarked(paper);

                        return (
                          <div
                            key={idx}
                            onClick={() =>
                              setSelectedPaperForDetail({
                                paper,
                                index: idx,
                              })
                            }
                            className={`p-4 rounded-xl border transition cursor-pointer hover:border-[var(--accent)]/50 ${
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

                            <p className="mt-2 text-xs text-[var(--text-secondary)] line-clamp-2 leading-relaxed">
                              {paper.abstract || "No abstract preview available."}
                            </p>

                            <div className="mt-3 flex items-center justify-between text-[10px] text-[var(--text-muted)] pt-2 border-t border-[var(--border-subtle)]">
                              <span className="font-medium text-[var(--text-secondary)]">
                                {paper.description}
                              </span>
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
      {/* 3. SETTINGS MODAL */}
      {/* ======================================================== */}
      {settingsModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <div className="w-full max-w-lg bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
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
          </div>
        </div>
      )}

      {/* Delete Confirmation Modal */}
      {deleteAccountModalOpen && (
        <div className="fixed inset-0 z-60 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4">
          <div className="w-full max-w-sm bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl p-5 space-y-4 shadow-2xl">
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
          </div>
        </div>
      )}

      {/* ======================================================== */}
      {/* 4. COMPLIANCE & POLICY MODALS */}
      {/* ======================================================== */}
      {activePolicyModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <div className="w-full max-w-xl bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[85vh]">
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
                    ScholarPulse is committed to protecting your academic research privacy. We process query metadata strictly to provide federated academic search, citation generation, and synthesized literature insights.
                  </p>
                  <h4 className="font-bold text-[var(--text-primary)]">Data Collection & Use</h4>
                  <p>
                    When you sign up, we securely store your email and hashed password. We do not sell your personal data or search queries to third-party advertisers.
                  </p>
                  <h4 className="font-bold text-[var(--text-primary)]">Third-Party Academic APIs</h4>
                  <p>
                    Queries are dispatched to public academic APIs (OpenAlex, Semantic Scholar, arXiv, Crossref, and CORE) to retrieve research metadata.
                  </p>
                </>
              )}

              {activePolicyModal === "terms" && (
                <>
                  <p className="font-semibold text-[var(--text-primary)]">Last updated: September 2026</p>
                  <p>
                    By using ScholarPulse, you agree to comply with standard academic integrity and acceptable use guidelines.
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
                    ScholarPulse does not use analytics or advertising cookies.
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
          </div>
        </div>
      )}

      {/* ======================================================== */}
      {/* 4b. COOKIE CONSENT BANNER (until a choice is stored) */}
      {/* ======================================================== */}
      {!cookieDecided && activePolicyModal !== "cookie_prefs" && (
        <div
          role="dialog"
          aria-label="Cookie consent"
          className="fixed bottom-4 left-4 right-4 sm:left-auto sm:right-6 sm:bottom-6 sm:max-w-md z-[60] rounded-2xl border border-[var(--border-color)] bg-[var(--bg-surface)] shadow-2xl p-4 text-xs text-[var(--text-secondary)]"
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
        </div>
      )}

      {/* ======================================================== */}
      {/* 5. AUTH MODAL (LOGIN / REGISTER WITH FULL NAME) */}
      {/* ======================================================== */}
      {authModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <div className="w-full max-w-sm bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl p-6 space-y-4">
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
                  Don't have an account?{" "}
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
          </div>
        </div>
      )}

      {/* ======================================================== */}
      {/* 6. FORGOT PASSWORD MODAL */}
      {/* ======================================================== */}
      {forgotPasswordModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <div className="w-full max-w-sm bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl p-6 space-y-4">
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
          </div>
        </div>
      )}

      {/* ======================================================== */}
      {/* 7. RESET PASSWORD MODAL (FROM URL TOKEN) */}
      {/* ======================================================== */}
      {resetPasswordModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4">
          <div className="w-full max-w-sm bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl shadow-2xl p-6 space-y-4">
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
          </div>
        </div>
      )}

      {/* Soft Gate Modal */}
      {softGateModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-xs p-4">
          <div className="w-full max-w-sm bg-[var(--bg-surface)] border border-[var(--border-color)] rounded-2xl p-6 space-y-4 shadow-2xl text-center">
            <div className="w-12 h-12 rounded-full bg-[var(--accent-light)] text-[var(--accent)] flex items-center justify-center mx-auto">
              <Sparkles className="w-6 h-6" />
            </div>

            <h3 className="font-serif-heading text-lg font-bold text-[var(--text-primary)]">
              Enjoying ScholarPulse?
            </h3>

            <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
              You've completed {GUEST_SEARCH_LIMIT} free guest searches. Create a free account to unlock unlimited searches, persist your research library, and access multi-turn deep dives.
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
          </div>
        </div>
      )}
    </div>
  );
}
