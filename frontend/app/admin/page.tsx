"use client";

/**
 * Admin dashboard. Every check happens on the server: this page only shows what the API allows.
 * Access needs the normal sign-in, admin rights (granted on the server with make_admin.py) and a
 * 6-digit authenticator code, which unlocks the admin area for 30 minutes.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, KeyRound, Lock, RefreshCw, ScrollText, Shield, Users, BarChart3, Database } from "lucide-react";
import { APP_NAME } from "../brand";

const API = process.env.NEXT_PUBLIC_API_URL as string;

type Status = { is_admin: boolean; unlocked: boolean; expires_at: number | null; encryption_configured: boolean };
type Overview = {
  users: { total: number; verified: number; new_7_days: number; active_today: number; disabled: number; admins: number };
  answers: { today: number; last_7_days: number };
  ai: { calls_today: number; daily_cap: number; guest_cap: number; user_cap: number };
  deep: { runs_today: number; daily_cap: number; guest_cap: number; user_cap: number };
  uploads?: { documents: number; text_mb: number };
  counters_shared: boolean;
};
type AdminUser = {
  id: number;
  email: string;
  full_name: string | null;
  is_verified: boolean;
  is_admin: boolean;
  is_disabled: boolean;
  created_at: string | null;
  last_login_at: string | null;
  answers: number;
};
type KeyRow = { name: string; label: string; source: "panel" | "env" | "none"; last4: string | null; updated_at: string | null; env_set: boolean };
type AuditEntry = { id: number; admin_email: string | null; action: string; target: string | null; detail: string | null; ip: string | null; created_at: string | null };
type SourceRow = {
  name: string;
  label: string;
  enabled: boolean;
  key_name: string | null;
  key_source: "panel" | "env" | "none" | null;
  key_required: boolean;
  calls: number;
  failures: number;
  results: number;
  avg_ms: number | null;
  last_ok_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
  needs_email: boolean;
};
type Tab = "overview" | "users" | "sources" | "keys" | "audit";
type Phase = "loading" | "signed-out" | "not-admin" | "locked" | "unlocked" | "error";

class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(typeof data.detail === "string" ? data.detail : "Request failed.", res.status);
  return data as T;
}

function when(iso: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? "-" : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="p-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
      <div className="text-[11px] uppercase tracking-wider text-[var(--text-muted)]">{label}</div>
      <div className="mt-1 text-2xl font-bold text-[var(--text-primary)] tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-[var(--text-secondary)]">{sub}</div>}
    </div>
  );
}

function Meter({ used, cap }: { used: number; cap: number }) {
  const pct = cap > 0 ? Math.min(100, Math.round((used / cap) * 100)) : 0;
  const color = pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-amber-400" : "bg-emerald-500";
  return (
    <div className="mt-2 h-2 rounded-full bg-[var(--bg-surface-elevated)] overflow-hidden" aria-hidden="true">
      <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

const inputClass =
  "px-3 py-2 rounded-lg border border-[var(--border-color)] bg-[var(--bg-primary)] text-[var(--text-primary)] text-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent)]";
const buttonClass =
  "px-3 py-2 rounded-lg bg-[var(--accent)] hover:bg-[var(--accent-hover)] disabled:opacity-50 text-white text-xs font-semibold transition";
const ghostClass =
  "px-2.5 py-1.5 rounded-lg border border-[var(--border-color)] hover:bg-[var(--bg-surface-elevated)] disabled:opacity-50 text-xs font-medium text-[var(--text-secondary)] transition";

export default function AdminPage() {
  const [phase, setPhase] = useState<Phase>("loading");
  const [status, setStatus] = useState<Status | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const [code, setCode] = useState("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [usersTotal, setUsersTotal] = useState(0);
  const [userQuery, setUserQuery] = useState("");
  const [userPage, setUserPage] = useState(1);
  const [keys, setKeys] = useState<KeyRow[]>([]);
  const [keysEditable, setKeysEditable] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [keyValue, setKeyValue] = useState("");
  const [keyCode, setKeyCode] = useState("");
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [sources, setSources] = useState<SourceRow[]>([]);

  function handleLocked(err: unknown) {
    if (err instanceof ApiError && err.status === 401) {
      setPhase("signed-out");
      return true;
    }
    if (err instanceof ApiError && err.status === 403 && /locked/i.test(err.message)) {
      setPhase("locked");
      setMessage({ kind: "error", text: "The admin session ended. Enter a new code." });
      return true;
    }
    return false;
  }

  async function loadTab(which: Tab, page = userPage, query = userQuery) {
    try {
      if (which === "overview") setOverview(await api<Overview>("/admin/overview"));
      if (which === "users") {
        const params = new URLSearchParams({ q: query, page: String(page), page_size: "25" });
        const data = await api<{ users: AdminUser[]; total: number }>(`/admin/users?${params.toString()}`);
        setUsers(data.users);
        setUsersTotal(data.total);
      }
      if (which === "keys") {
        const data = await api<{ keys: KeyRow[]; editable: boolean }>("/admin/keys");
        setKeys(data.keys);
        setKeysEditable(data.editable);
      }
      if (which === "audit") setAudit((await api<{ entries: AuditEntry[] }>("/admin/audit")).entries);
      if (which === "sources") setSources((await api<{ sources: SourceRow[] }>("/admin/sources")).sources);
    } catch (err) {
      if (!handleLocked(err)) setMessage({ kind: "error", text: err instanceof Error ? err.message : "Could not load." });
    }
  }

  function applyStatus(data: Status) {
    setStatus(data);
    if (!data.is_admin) setPhase("not-admin");
    else if (!data.unlocked) setPhase("locked");
    else {
      setPhase("unlocked");
      void loadTab("overview");
    }
  }

  function statusFailed(err: unknown) {
    if (err instanceof ApiError && err.status === 401) setPhase("signed-out");
    else setPhase("error");
  }

  function refreshStatus() {
    return api<Status>("/admin/status").then(applyStatus, statusFailed);
  }

  useEffect(() => {
    // Match the site's saved theme (stored by the main page).
    try {
      const saved = localStorage.getItem("researchai-theme");
      if (saved === "dark" || saved === "light") {
        document.documentElement.setAttribute("data-theme", saved);
        document.documentElement.classList.toggle("dark", saved === "dark");
      }
    } catch {
      // storage unavailable: keep the default theme
    }
    // State is only set when the request finishes (in the callbacks), never during the effect itself.
    api<Status>("/admin/status").then(applyStatus, statusFailed);
    // Runs once when the page opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function unlock(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      await api("/admin/unlock", { method: "POST", body: JSON.stringify({ code: code.trim() }) });
      setCode("");
      await refreshStatus();
    } catch (err) {
      setMessage({ kind: "error", text: err instanceof Error ? err.message : "Could not unlock." });
    } finally {
      setBusy(false);
    }
  }

  async function lock() {
    await api("/admin/lock", { method: "POST" }).catch(() => undefined);
    setPhase("locked");
    setOverview(null);
    setUsers([]);
    setKeys([]);
    setAudit([]);
  }

  function openTab(which: Tab) {
    setTab(which);
    setMessage(null);
    void loadTab(which);
  }

  async function userAction(user: AdminUser, action: "disable" | "enable" | "logout-all" | "resend-verification") {
    const prompts: Record<string, string> = {
      disable: `Disable ${user.email}? They are signed out now and cannot sign in until you enable the account again.`,
      "logout-all": `Sign ${user.email} out of every device?`,
    };
    if (prompts[action] && !window.confirm(prompts[action])) return;
    setBusy(true);
    setMessage(null);
    try {
      await api(`/admin/users/${user.id}/${action}`, { method: "POST" });
      const done: Record<string, string> = {
        disable: "Account disabled.",
        enable: "Account enabled.",
        "logout-all": "Signed out everywhere.",
        "resend-verification": "Verification email sent.",
      };
      setMessage({ kind: "ok", text: `${user.email}: ${done[action]}` });
      await loadTab("users");
    } catch (err) {
      if (!handleLocked(err)) setMessage({ kind: "error", text: err instanceof Error ? err.message : "Action failed." });
    } finally {
      setBusy(false);
    }
  }

  async function toggleSource(row: SourceRow) {
    setBusy(true);
    setMessage(null);
    try {
      await api(`/admin/sources/${row.name}`, { method: "POST", body: JSON.stringify({ enabled: !row.enabled }) });
      setMessage({ kind: "ok", text: `${row.label} switched ${row.enabled ? "off" : "on"}.` });
      await loadTab("sources");
    } catch (err) {
      if (!handleLocked(err)) setMessage({ kind: "error", text: err instanceof Error ? err.message : "Could not change the source." });
    } finally {
      setBusy(false);
    }
  }

  async function saveKey(e: React.FormEvent) {
    e.preventDefault();
    if (!editingKey) return;
    setBusy(true);
    setMessage(null);
    try {
      await api(`/admin/keys/${editingKey}`, {
        method: "POST",
        body: JSON.stringify({ value: keyValue.trim(), code: keyCode.trim() }),
      });
      setMessage({ kind: "ok", text: "Key tested and saved. It is in use now." });
      setEditingKey(null);
      await loadTab("keys");
    } catch (err) {
      if (!handleLocked(err)) setMessage({ kind: "error", text: err instanceof Error ? err.message : "Could not save the key." });
    } finally {
      // The value is never kept in the page after a save attempt.
      setKeyValue("");
      setKeyCode("");
      setBusy(false);
    }
  }

  async function clearKey(row: KeyRow) {
    const typed = window.prompt(
      `Stop using the panel value for ${row.label}? ${row.env_set ? "The key in the server configuration will be used again." : "There is no key in the server configuration, so this source will stop working."}\n\nEnter a fresh 6-digit authenticator code to confirm:`
    );
    if (!typed) return;
    setBusy(true);
    setMessage(null);
    try {
      await api(`/admin/keys/${row.name}/clear`, { method: "POST", body: JSON.stringify({ code: typed.trim() }) });
      setMessage({ kind: "ok", text: `${row.label}: panel value removed.` });
      await loadTab("keys");
    } catch (err) {
      if (!handleLocked(err)) setMessage({ kind: "error", text: err instanceof Error ? err.message : "Could not remove the key." });
    } finally {
      setBusy(false);
    }
  }

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "overview", label: "Overview", icon: <BarChart3 className="w-4 h-4" /> },
    { id: "users", label: "Users", icon: <Users className="w-4 h-4" /> },
    { id: "sources", label: "Sources", icon: <Database className="w-4 h-4" /> },
    { id: "keys", label: "API keys", icon: <KeyRound className="w-4 h-4" /> },
    { id: "audit", label: "Audit log", icon: <ScrollText className="w-4 h-4" /> },
  ];

  return (
    <main className="min-h-screen bg-[var(--bg-primary)] text-[var(--text-primary)]">
      <header className="border-b border-[var(--border-color)] bg-[var(--bg-surface)]">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center gap-3">
          <Link href="/" className="flex items-center gap-1 text-xs text-[var(--text-secondary)] hover:text-[var(--accent)]">
            <ArrowLeft className="w-4 h-4" />
            <span>{APP_NAME}</span>
          </Link>
          <div className="flex items-center gap-1.5 font-semibold text-sm mr-auto">
            <Shield className="w-4 h-4 text-[var(--accent)]" />
            <span>Admin</span>
          </div>
          {phase === "unlocked" && status?.expires_at && (
            <span className="hidden sm:inline text-[11px] text-[var(--text-muted)]">
              Locks at {new Date(status.expires_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
            </span>
          )}
          {phase === "unlocked" && (
            <button type="button" onClick={lock} className={ghostClass + " flex items-center gap-1"}>
              <Lock className="w-3.5 h-3.5" />
              <span>Lock</span>
            </button>
          )}
        </div>
      </header>

      <div className="max-w-6xl mx-auto px-4 py-6">
        {message && (
          <div
            role="status"
            className={`mb-4 p-3 rounded-xl text-sm border ${
              message.kind === "ok"
                ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700"
                : "border-red-500/30 bg-red-500/10 text-red-600"
            }`}
          >
            {message.text}
          </div>
        )}

        {phase === "loading" && <p className="text-sm text-[var(--text-secondary)]">Checking access...</p>}

        {phase === "error" && (
          <p className="text-sm text-[var(--text-secondary)]">The server could not be reached. Please try again later.</p>
        )}

        {phase === "signed-out" && (
          <div className="max-w-md p-6 rounded-2xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
            <h1 className="text-lg font-bold">Sign in first</h1>
            <p className="mt-2 text-sm text-[var(--text-secondary)]">
              Sign in on the main site with your admin account, then come back to this page.
            </p>
            <Link href="/" className={buttonClass + " inline-block mt-4"}>
              Go to sign in
            </Link>
          </div>
        )}

        {phase === "not-admin" && (
          <div className="max-w-md p-6 rounded-2xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
            <h1 className="text-lg font-bold">Not authorized</h1>
            <p className="mt-2 text-sm text-[var(--text-secondary)]">This account does not have admin access.</p>
          </div>
        )}

        {phase === "locked" && (
          <form onSubmit={unlock} className="max-w-md p-6 rounded-2xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
            <h1 className="text-lg font-bold flex items-center gap-2">
              <Lock className="w-5 h-5 text-[var(--accent)]" /> Unlock the admin area
            </h1>
            <p className="mt-2 text-sm text-[var(--text-secondary)]">
              Enter the 6-digit code from your authenticator app. The admin area stays unlocked for 30 minutes.
            </p>
            {status && !status.encryption_configured && (
              <p className="mt-3 text-xs text-red-600">
                The server is missing SETTINGS_ENCRYPTION_KEY, so the admin area cannot be unlocked yet.
              </p>
            )}
            <label className="block mt-4 text-xs font-medium text-[var(--text-secondary)]" htmlFor="admin-code">
              Authenticator code
            </label>
            <input
              id="admin-code"
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{6}"
              className={inputClass + " mt-1 w-full tracking-[0.4em] text-center text-lg"}
              placeholder="000000"
              autoFocus
            />
            <button type="submit" disabled={busy || code.length !== 6} className={buttonClass + " mt-4 w-full"}>
              {busy ? "Checking..." : "Unlock"}
            </button>
          </form>
        )}

        {phase === "unlocked" && (
          <>
            <nav className="flex flex-wrap gap-2 mb-5" aria-label="Admin sections">
              {tabs.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => openTab(t.id)}
                  aria-current={tab === t.id ? "page" : undefined}
                  className={`px-3 py-2 rounded-lg text-xs font-semibold flex items-center gap-1.5 border transition ${
                    tab === t.id
                      ? "bg-[var(--accent)] text-white border-[var(--accent)]"
                      : "bg-[var(--bg-surface)] border-[var(--border-color)] text-[var(--text-secondary)] hover:bg-[var(--bg-surface-elevated)]"
                  }`}
                >
                  {t.icon}
                  {t.label}
                </button>
              ))}
              <button type="button" onClick={() => loadTab(tab)} className={ghostClass + " ml-auto flex items-center gap-1"}>
                <RefreshCw className="w-3.5 h-3.5" /> Refresh
              </button>
            </nav>

            {tab === "overview" && overview && (
              <div className="space-y-4">
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                  <Stat label="Users" value={overview.users.total} sub={`${overview.users.verified} verified`} />
                  <Stat label="New this week" value={overview.users.new_7_days} />
                  <Stat label="Signed in today" value={overview.users.active_today} />
                  <Stat label="Disabled" value={overview.users.disabled} sub={`${overview.users.admins} admin(s)`} />
                  <Stat label="Answers today" value={overview.answers.today} sub={`${overview.answers.last_7_days} this week`} />
                  {overview.uploads && (
                    <Stat label="Uploaded PDFs" value={overview.uploads.documents} sub={`${overview.uploads.text_mb} MB of text stored`} />
                  )}
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div className="p-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
                    <div className="text-sm font-semibold">AI calls today</div>
                    <div className="mt-1 text-2xl font-bold tabular-nums">
                      {overview.ai.calls_today} <span className="text-sm font-normal text-[var(--text-muted)]">/ {overview.ai.daily_cap}</span>
                    </div>
                    <Meter used={overview.ai.calls_today} cap={overview.ai.daily_cap} />
                    <div className="mt-2 text-[11px] text-[var(--text-secondary)]">
                      Per guest {overview.ai.guest_cap} a day, per account {overview.ai.user_cap} a day.
                    </div>
                  </div>
                  <div className="p-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
                    <div className="text-sm font-semibold">Deep research runs today</div>
                    <div className="mt-1 text-2xl font-bold tabular-nums">
                      {overview.deep.runs_today} <span className="text-sm font-normal text-[var(--text-muted)]">/ {overview.deep.daily_cap}</span>
                    </div>
                    <Meter used={overview.deep.runs_today} cap={overview.deep.daily_cap} />
                    <div className="mt-2 text-[11px] text-[var(--text-secondary)]">
                      Per guest {overview.deep.guest_cap} a day, per account {overview.deep.user_cap} a day.
                    </div>
                  </div>
                </div>
                {!overview.counters_shared && (
                  <p className="text-[11px] text-[var(--text-muted)]">
                    Redis is not connected, so today&apos;s counters reset when the API restarts.
                  </p>
                )}
              </div>
            )}

            {tab === "users" && (
              <div className="space-y-3">
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    setUserPage(1);
                    void loadTab("users", 1, userQuery);
                  }}
                  className="flex gap-2"
                >
                  <input
                    value={userQuery}
                    onChange={(e) => setUserQuery(e.target.value)}
                    placeholder="Search by email or name"
                    className={inputClass + " flex-1"}
                    aria-label="Search users"
                  />
                  <button type="submit" className={buttonClass}>Search</button>
                </form>
                <div className="sm:hidden space-y-2">
                  {users.map((u) => (
                    <div key={u.id} className="p-3 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] text-xs space-y-2">
                      <div>
                        <div className="font-semibold text-sm text-[var(--text-primary)] break-all">{u.email}</div>
                        {u.full_name && <div className="text-[var(--text-muted)]">{u.full_name}</div>}
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {u.is_admin && <span className="px-1.5 py-0.5 rounded bg-[var(--accent-light)] text-[var(--accent)] font-semibold">Admin</span>}
                        {u.is_disabled && <span className="px-1.5 py-0.5 rounded bg-red-600 text-white font-semibold">Disabled</span>}
                        <span className={u.is_verified ? "text-emerald-600" : "text-amber-600"}>{u.is_verified ? "Verified" : "Unverified"}</span>
                      </div>
                      <div className="grid grid-cols-2 gap-1 text-[var(--text-secondary)]">
                        <span>Joined: {when(u.created_at)}</span>
                        <span>Last sign-in: {when(u.last_login_at)}</span>
                        <span>Answers: {u.answers}</span>
                      </div>
                      <div className="flex flex-wrap gap-1">
                        {!u.is_admin &&
                          (u.is_disabled ? (
                            <button type="button" disabled={busy} onClick={() => userAction(u, "enable")} className={ghostClass}>Enable</button>
                          ) : (
                            <button type="button" disabled={busy} onClick={() => userAction(u, "disable")} className={ghostClass}>Disable</button>
                          ))}
                        {!u.is_admin && (
                          <button type="button" disabled={busy} onClick={() => userAction(u, "logout-all")} className={ghostClass}>Sign out everywhere</button>
                        )}
                        {!u.is_verified && (
                          <button type="button" disabled={busy} onClick={() => userAction(u, "resend-verification")} className={ghostClass}>Resend verification</button>
                        )}
                      </div>
                    </div>
                  ))}
                  {users.length === 0 && <p className="p-4 text-center text-xs text-[var(--text-muted)]">No users found.</p>}
                </div>
                <div className="hidden sm:block overflow-x-auto rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
                  <table className="w-full min-w-[820px] text-xs text-left">
                    <thead className="bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)]">
                      <tr>
                        <th className="p-2">Email</th>
                        <th className="p-2">Status</th>
                        <th className="p-2">Joined</th>
                        <th className="p-2">Last sign-in</th>
                        <th className="p-2">Answers</th>
                        <th className="p-2">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {users.map((u) => (
                        <tr key={u.id} className="border-t border-[var(--border-subtle)] align-top">
                          <td className="p-2">
                            <div className="font-semibold text-[var(--text-primary)]">{u.email}</div>
                            {u.full_name && <div className="text-[var(--text-muted)]">{u.full_name}</div>}
                          </td>
                          <td className="p-2">
                            <div className="flex flex-wrap gap-1">
                              {u.is_admin && <span className="px-1.5 py-0.5 rounded bg-[var(--accent-light)] text-[var(--accent)] font-semibold">Admin</span>}
                              {u.is_disabled && <span className="px-1.5 py-0.5 rounded bg-red-600 text-white font-semibold">Disabled</span>}
                              <span className={u.is_verified ? "text-emerald-600" : "text-amber-600"}>
                                {u.is_verified ? "Verified" : "Unverified"}
                              </span>
                            </div>
                          </td>
                          <td className="p-2 text-[var(--text-secondary)]">{when(u.created_at)}</td>
                          <td className="p-2 text-[var(--text-secondary)]">{when(u.last_login_at)}</td>
                          <td className="p-2 tabular-nums">{u.answers}</td>
                          <td className="p-2">
                            <div className="flex flex-wrap gap-1">
                              {!u.is_admin &&
                                (u.is_disabled ? (
                                  <button type="button" disabled={busy} onClick={() => userAction(u, "enable")} className={ghostClass}>Enable</button>
                                ) : (
                                  <button type="button" disabled={busy} onClick={() => userAction(u, "disable")} className={ghostClass}>Disable</button>
                                ))}
                              {!u.is_admin && (
                                <button type="button" disabled={busy} onClick={() => userAction(u, "logout-all")} className={ghostClass}>Sign out everywhere</button>
                              )}
                              {!u.is_verified && (
                                <button type="button" disabled={busy} onClick={() => userAction(u, "resend-verification")} className={ghostClass}>Resend verification</button>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                      {users.length === 0 && (
                        <tr>
                          <td colSpan={6} className="p-4 text-center text-[var(--text-muted)]">No users found.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <div className="flex items-center justify-between text-xs text-[var(--text-secondary)]">
                  <span>{usersTotal} user(s)</span>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={userPage <= 1}
                      onClick={() => {
                        const p = userPage - 1;
                        setUserPage(p);
                        void loadTab("users", p);
                      }}
                      className={ghostClass}
                    >
                      Previous
                    </button>
                    <span className="self-center">Page {userPage}</span>
                    <button
                      type="button"
                      disabled={userPage * 25 >= usersTotal}
                      onClick={() => {
                        const p = userPage + 1;
                        setUserPage(p);
                        void loadTab("users", p);
                      }}
                      className={ghostClass}
                    >
                      Next
                    </button>
                  </div>
                </div>
              </div>
            )}

            {tab === "sources" && (
              <div className="space-y-3">
                <p className="text-xs text-[var(--text-secondary)] max-w-3xl">
                  Switch a source off to stop searching it (for example while it is slow or failing). Changes apply at once,
                  with no restart. Numbers are counted since the API last restarted.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {sources.map((src) => {
                    const failRate = src.calls > 0 ? Math.round((src.failures / src.calls) * 100) : 0;
                    const health =
                      !src.enabled
                        ? { text: "Off", cls: "text-[var(--text-muted)]" }
                        : src.key_required && src.key_source === "none"
                          ? { text: "Needs a key", cls: "text-amber-600" }
                          : src.needs_email
                            ? { text: "Needs a contact email", cls: "text-amber-600" }
                            : src.calls === 0
                              ? { text: "No searches yet", cls: "text-[var(--text-muted)]" }
                              : failRate >= 50
                                ? { text: "Failing", cls: "text-red-600" }
                                : failRate > 0
                                  ? { text: "Some errors", cls: "text-amber-600" }
                                  : { text: "Working", cls: "text-emerald-600" };
                    return (
                      <div key={src.name} className="p-3 sm:p-4 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] text-xs">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="text-sm font-semibold text-[var(--text-primary)]">{src.label}</div>
                            <div className={`mt-0.5 font-medium ${health.cls}`}>{health.text}</div>
                          </div>
                          <button
                            type="button"
                            role="switch"
                            aria-checked={src.enabled}
                            aria-label={`${src.label} ${src.enabled ? "on" : "off"}`}
                            disabled={busy}
                            onClick={() => toggleSource(src)}
                            className={`relative shrink-0 w-11 h-6 rounded-full transition disabled:opacity-60 ${
                              src.enabled ? "bg-emerald-500" : "bg-[var(--border-color)]"
                            }`}
                          >
                            <span
                              className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
                                src.enabled ? "translate-x-5" : ""
                              }`}
                            />
                          </button>
                        </div>
                        <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-[var(--text-secondary)]">
                          <div><div className="text-[var(--text-muted)]">Searches</div><div className="font-semibold tabular-nums">{src.calls}</div></div>
                          <div><div className="text-[var(--text-muted)]">Errors</div><div className="font-semibold tabular-nums">{src.failures}</div></div>
                          <div><div className="text-[var(--text-muted)]">{src.name === "unpaywall" ? "PDF links" : "Papers"}</div><div className="font-semibold tabular-nums">{src.results}</div></div>
                          <div><div className="text-[var(--text-muted)]">Avg time</div><div className="font-semibold tabular-nums">{src.avg_ms !== null ? `${(src.avg_ms / 1000).toFixed(1)} s` : "-"}</div></div>
                        </div>
                        {src.key_name && (
                          <div className="mt-2 text-[var(--text-muted)]">
                            Key ({src.key_required ? "required" : "optional"}):{" "}
                            {src.key_source === "panel" ? "set in this panel" : src.key_source === "env" ? "from server configuration" : "not set"}
                            {" · "}
                            <button type="button" onClick={() => openTab("keys")} className="text-[var(--accent)] hover:underline">
                              Manage keys
                            </button>
                          </div>
                        )}
                        {src.last_error && (
                          <div className="mt-1 text-[var(--text-muted)]">
                            Last error: {src.last_error} ({when(src.last_error_at)})
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {tab === "keys" && (
              <div className="space-y-3">
                <p className="text-xs text-[var(--text-secondary)] max-w-3xl">
                  Keys are write-only: they are never shown again, only their last 4 characters. A new key is tested with the
                  provider before it is saved, so a wrong key never goes live. Saved keys are encrypted and used at once, with no
                  restart. Removing a panel value goes back to the key in the server configuration.
                </p>
                {!keysEditable && (
                  <p className="text-xs text-red-600">Key editing needs SETTINGS_ENCRYPTION_KEY on the server.</p>
                )}
                <div className="rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] divide-y divide-[var(--border-subtle)]">
                  {keys.map((k) => (
                    <div key={k.name} className="p-3 sm:p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <div className="mr-auto">
                          <div className="text-sm font-semibold">{k.label}</div>
                          <div className="text-[11px] text-[var(--text-muted)]">
                            {k.source === "panel" && <>Set in this panel{k.last4 ? `, ends in …${k.last4}` : ""}{k.updated_at ? ` · ${when(k.updated_at)}` : ""}</>}
                            {k.source === "env" && <>From server configuration{k.last4 ? `, ends in …${k.last4}` : ""}</>}
                            {k.source === "none" && <span className="text-amber-600">Not set</span>}
                          </div>
                        </div>
                        <button
                          type="button"
                          disabled={!keysEditable || busy}
                          onClick={() => {
                            setEditingKey(editingKey === k.name ? null : k.name);
                            setKeyValue("");
                            setKeyCode("");
                          }}
                          className={ghostClass}
                        >
                          {editingKey === k.name ? "Cancel" : k.source === "none" ? "Add key" : "Replace"}
                        </button>
                        {k.source === "panel" && (
                          <button type="button" disabled={busy} onClick={() => clearKey(k)} className={ghostClass}>
                            Remove panel value
                          </button>
                        )}
                      </div>
                      {editingKey === k.name && (
                        <form onSubmit={saveKey} className="mt-3 grid grid-cols-1 sm:grid-cols-[1fr_140px_auto] gap-2">
                          <input
                            type="password"
                            value={keyValue}
                            onChange={(e) => setKeyValue(e.target.value)}
                            placeholder="Paste the new key"
                            autoComplete="off"
                            spellCheck={false}
                            className={inputClass}
                            aria-label={`New key for ${k.label}`}
                          />
                          <input
                            value={keyCode}
                            onChange={(e) => setKeyCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                            inputMode="numeric"
                            autoComplete="one-time-code"
                            placeholder="Fresh code"
                            className={inputClass + " text-center tracking-widest"}
                            aria-label="Fresh authenticator code"
                          />
                          <button type="submit" disabled={busy || keyValue.trim().length < 8 || keyCode.length !== 6} className={buttonClass}>
                            {busy ? "Testing..." : "Test and save"}
                          </button>
                          <p className="sm:col-span-3 text-[11px] text-[var(--text-muted)]">
                            Use a new code: each code works only once. If you just unlocked, wait for the next one.
                          </p>
                        </form>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {tab === "audit" && (
              <div className="sm:hidden space-y-2">
                {audit.map((a) => (
                  <div key={a.id} className="p-3 rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)] text-xs space-y-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono font-semibold">{a.action}</span>
                      <span className="text-[var(--text-muted)]">{when(a.created_at)}</span>
                    </div>
                    <div className="text-[var(--text-secondary)] break-all">
                      {a.admin_email || "server command"}
                      {a.target ? ` → ${a.target}` : ""}
                    </div>
                    {(a.detail || a.ip) && (
                      <div className="text-[var(--text-muted)]">
                        {a.detail || ""}
                        {a.detail && a.ip ? " · " : ""}
                        {a.ip ? <span className="font-mono">{a.ip}</span> : null}
                      </div>
                    )}
                  </div>
                ))}
                {audit.length === 0 && <p className="p-4 text-center text-xs text-[var(--text-muted)]">No entries yet.</p>}
              </div>
            )}

            {tab === "audit" && (
              <div className="hidden sm:block overflow-x-auto rounded-xl border border-[var(--border-color)] bg-[var(--bg-surface)]">
                <table className="w-full min-w-[720px] text-xs text-left">
                  <thead className="bg-[var(--bg-surface-elevated)] text-[var(--text-secondary)]">
                    <tr>
                      <th className="p-2">When</th>
                      <th className="p-2">Admin</th>
                      <th className="p-2">Action</th>
                      <th className="p-2">Target</th>
                      <th className="p-2">Detail</th>
                      <th className="p-2">IP</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audit.map((a) => (
                      <tr key={a.id} className="border-t border-[var(--border-subtle)]">
                        <td className="p-2 whitespace-nowrap text-[var(--text-secondary)]">{when(a.created_at)}</td>
                        <td className="p-2">{a.admin_email || "server command"}</td>
                        <td className="p-2 font-mono">{a.action}</td>
                        <td className="p-2">{a.target || "-"}</td>
                        <td className="p-2 text-[var(--text-secondary)]">{a.detail || "-"}</td>
                        <td className="p-2 font-mono text-[var(--text-muted)]">{a.ip || "-"}</td>
                      </tr>
                    ))}
                    {audit.length === 0 && (
                      <tr>
                        <td colSpan={6} className="p-4 text-center text-[var(--text-muted)]">No entries yet.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </main>
  );
}
