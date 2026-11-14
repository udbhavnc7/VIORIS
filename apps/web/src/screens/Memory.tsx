import { useCallback, useEffect, useMemo, useState } from "react";

import { api, formatError, type MemoryItem } from "../lib/api";
import { relativeTime, titleCase } from "../lib/format";
import { PageHeader } from "../components/EmptyState";
import { Icon } from "../components/Icon";
import { ConfirmDialog, Modal } from "../components/Modal";
import { useToast } from "../components/Toast";

const CATEGORIES = ["preferences", "people", "projects", "routines", "sensitive"] as const;
type Category = (typeof CATEGORIES)[number];

const CATEGORY_META: Record<Category, { label: string; blurb: string }> = {
  preferences: { label: "Preferences", blurb: "How you like things done." },
  people: { label: "People", blurb: "The relationships that matter." },
  projects: { label: "Projects", blurb: "Work in flight." },
  routines: { label: "Routines", blurb: "Repeating rhythms." },
  sensitive: { label: "Sensitive", blurb: "Opt-in only. Never inferred." },
};

interface Draft {
  category: Category;
  content: string;
  source: string;
  confidence: number;
  optedIn: boolean;
}

const EMPTY: Draft = {
  category: "preferences",
  content: "",
  source: "user",
  confidence: 1,
  optedIn: false,
};

export function Memory() {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Category | "all">("all");
  const [addOpen, setAddOpen] = useState(false);
  const [editing, setEditing] = useState<MemoryItem | null>(null);
  const [deleting, setDeleting] = useState<MemoryItem | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const { push } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setItems(await api.memory());
      setError(null);
    } catch (err) {
      setError(formatError(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = useMemo(() => {
    const map = new Map<string, number>();
    for (const item of items) map.set(item.category, (map.get(item.category) ?? 0) + 1);
    return map;
  }, [items]);

  const visible = filter === "all" ? items : items.filter((item) => item.category === filter);

  const openAdd = () => {
    setDraft(EMPTY);
    setFormError(null);
    setAddOpen(true);
  };

  const openEdit = (item: MemoryItem) => {
    setDraft({
      category: item.category as Category,
      content: item.content,
      source: item.source,
      confidence: item.confidence,
      optedIn: item.user_opted_in,
    });
    setFormError(null);
    setEditing(item);
  };

  const save = async () => {
    if (!draft.content.trim()) {
      setFormError("A memory needs some content.");
      return;
    }
    if (draft.category === "sensitive" && !draft.optedIn) {
      setFormError("Sensitive memories require explicit opt-in.");
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      if (editing) {
        await api.correctMemory(editing.memory_id, {
          content: draft.content.trim(),
          category: draft.category,
        });
        push({ title: "Memory corrected", message: "The old value was replaced, not appended.", tone: "ok" });
        setEditing(null);
      } else {
        await api.remember({
          category: draft.category,
          content: draft.content.trim(),
          source: draft.source.trim() || "user",
          confidence: draft.confidence,
          user_opted_in: draft.optedIn,
        });
        push({ title: "Memory stored", message: "You can correct or delete it at any time.", tone: "ok" });
        setAddOpen(false);
      }
      setDraft(EMPTY);
      await load();
    } catch (err) {
      setFormError(formatError(err));
    } finally {
      setBusy(false);
    }
  };

  const forget = async () => {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.forget(deleting.memory_id);
      push({ title: "Memory deleted", tone: "warn" });
      setDeleting(null);
      await load();
    } catch (err) {
      push({ title: "Could not delete", message: formatError(err), tone: "bad" });
    } finally {
      setBusy(false);
    }
  };

  const form = (
    <div className="memory-form">
      <label className="field">
        <span>Category</span>
        <select
          value={draft.category}
          onChange={(event) => setDraft({ ...draft, category: event.target.value as Category })}
        >
          {CATEGORIES.map((category) => (
            <option key={category} value={category}>
              {CATEGORY_META[category].label}
            </option>
          ))}
        </select>
        <em>{CATEGORY_META[draft.category].blurb}</em>
      </label>

      <label className="field">
        <span>Content</span>
        <textarea
          value={draft.content}
          rows={4}
          maxLength={500}
          placeholder="A fact worth remembering, in your own words."
          onChange={(event) => setDraft({ ...draft, content: event.target.value })}
        />
        <em>{draft.content.length}/500</em>
      </label>

      <div className="field-row">
        <label className="field">
          <span>Source</span>
          <input
            value={draft.source}
            onChange={(event) => setDraft({ ...draft, source: event.target.value })}
            placeholder="user"
          />
        </label>
        <label className="field">
          <span>Confidence · {Math.round(draft.confidence * 100)}%</span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={draft.confidence}
            onChange={(event) => setDraft({ ...draft, confidence: Number(event.target.value) })}
          />
        </label>
      </div>

      {draft.category === "sensitive" ? (
        <div className={`optin-box ${draft.optedIn ? "is-on" : ""}`}>
          <label className="switch-row">
            <input
              type="checkbox"
              checked={draft.optedIn}
              onChange={(event) => setDraft({ ...draft, optedIn: event.target.checked })}
            />
            <span className="switch" aria-hidden="true" />
            <span>
              <strong>I explicitly opt in to storing this.</strong>
              <em>
                Sensitive memory is never inferred, never auto-captured, and can be deleted in one
                action.
              </em>
            </span>
          </label>
        </div>
      ) : null}

      {formError ? <p className="inline-error">{formError}</p> : null}
    </div>
  );

  return (
    <div className="page memory-page">
      <PageHeader
        eyebrow="Personal memory"
        title="Remember with permission"
        intro="Memory is a ledger you can read, correct, and delete — not a hidden profile. Sensitive entries require an explicit opt-in every time."
        actions={
          <button type="button" className="btn btn-primary" onClick={openAdd}>
            <Icon name="plus" size={15} /> Add memory
          </button>
        }
      />

      <div className="memory-summary">
        <div className="memory-total">
          <strong>{items.length}</strong>
          <span>memories on this machine</span>
        </div>
        <div className="memory-breakdown">
          {CATEGORIES.map((category) => (
            <span className={`category-chip category-${category}`} key={category}>
              {CATEGORY_META[category].label}
              <em>{counts.get(category) ?? 0}</em>
            </span>
          ))}
        </div>
        <button type="button" className="icon-btn" aria-label="Refresh memory" onClick={() => void load()}>
          <span className={loading ? "spin" : ""}>
            <Icon name="refresh" size={16} />
          </span>
        </button>
      </div>

      {error ? (
        <div className="callout callout-bad">
          <Icon name="alert" size={16} />
          <div>
            <strong>Memory service did not answer</strong>
            <span>
              {error}. Start it with <code>uvicorn services.memory_service.app.api:app --port 8441</code>.
            </span>
            <button type="button" className="btn btn-ghost btn-xs" onClick={() => void load()}>
              Retry
            </button>
          </div>
        </div>
      ) : null}

      <div className="segmented memory-tabs" role="tablist" aria-label="Memory category">
        <button
          type="button"
          role="tab"
          aria-selected={filter === "all"}
          className={filter === "all" ? "is-active" : ""}
          onClick={() => setFilter("all")}
        >
          All <em>{items.length}</em>
        </button>
        {CATEGORIES.map((category) => (
          <button
            key={category}
            type="button"
            role="tab"
            aria-selected={filter === category}
            className={filter === category ? "is-active" : ""}
            onClick={() => setFilter(category)}
          >
            {CATEGORY_META[category].label} <em>{counts.get(category) ?? 0}</em>
          </button>
        ))}
      </div>

      {loading && items.length === 0 ? (
        <div className="rolling">
          <span className="spinner" /> Reading local memory…
        </div>
      ) : visible.length === 0 ? (
        <div className="empty-state">
          <span className="empty-icon">
            <Icon name="brain" size={22} />
          </span>
          <h3>{items.length === 0 ? "Memory is empty" : "Nothing in this category"}</h3>
          <p>
            Vioris remembers only what you tell it to. Add a preference, a person, or a project — or
            leave it empty.
          </p>
          <div className="empty-action">
            <button type="button" className="btn btn-primary" onClick={openAdd}>
              <Icon name="plus" size={15} /> Add the first memory
            </button>
          </div>
        </div>
      ) : (
        <div className="memory-grid">
          {visible.map((item) => (
            <article className={`memory-card memory-${item.category}`} key={item.memory_id}>
              <div className="memory-top">
                <span className={`category-chip category-${item.category}`}>
                  {CATEGORY_META[item.category as Category]?.label ?? titleCase(item.category)}
                </span>
                <span className="memory-confidence" title="Confidence">
                  {Math.round(item.confidence * 100)}%
                </span>
              </div>
              <p className="memory-content">{item.content}</p>
              <div className="memory-meta">
                <span>
                  <Icon name="contact" size={12} /> {item.source}
                </span>
                <span>
                  <Icon name="clock" size={12} /> {relativeTime(item.updated_at)}
                </span>
                {item.user_opted_in ? (
                  <span className="optin-tag">
                    <Icon name="check" size={12} /> opted in
                  </span>
                ) : null}
              </div>
              <div className="memory-actions">
                <button type="button" className="btn btn-ghost btn-xs" onClick={() => openEdit(item)}>
                  <Icon name="edit" size={13} /> Correct
                </button>
                <button
                  type="button"
                  className="btn btn-danger-ghost btn-xs"
                  onClick={() => setDeleting(item)}
                >
                  <Icon name="trash" size={13} /> Forget
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      <Modal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title="Add a memory"
        eyebrow="Explicit capture"
        footer={
          <>
            <button type="button" className="btn btn-ghost" onClick={() => setAddOpen(false)}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={busy}>
              {busy ? <span className="spinner" aria-hidden="true" /> : <Icon name="plus" size={15} />}
              Store memory
            </button>
          </>
        }
      >
        {form}
      </Modal>

      <Modal
        open={Boolean(editing)}
        onClose={() => setEditing(null)}
        title="Correct a memory"
        eyebrow="Rewrite, don't append"
        footer={
          <>
            <button type="button" className="btn btn-ghost" onClick={() => setEditing(null)}>
              Cancel
            </button>
            <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={busy}>
              {busy ? <span className="spinner" aria-hidden="true" /> : <Icon name="check" size={15} />}
              Save correction
            </button>
          </>
        }
      >
        {form}
      </Modal>

      <ConfirmDialog
        open={Boolean(deleting)}
        title="Forget this memory?"
        body={
          <div className="confirm-copy">
            <p className="memory-quote">“{deleting?.content}”</p>
            <p>This removes it from local memory. Vioris will not use it again.</p>
          </div>
        }
        confirmLabel="Forget it"
        busy={busy}
        onCancel={() => setDeleting(null)}
        onConfirm={() => void forget()}
      />
    </div>
  );
}
