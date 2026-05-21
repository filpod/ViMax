import React, { useState } from "react";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function postDecision(body) {
  const res = await fetch("/api/decision", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to submit decision");
  }
  return res.json();
}

function contentToString(output) {
  if (!output) return "";
  const { type, content } = output;
  if (type === "text") return typeof content === "string" ? content : String(content);
  if (type === "json") return JSON.stringify(content, null, 2);
  // portraits / images / videos – not directly editable as text
  return JSON.stringify(content, null, 2);
}

// ---------------------------------------------------------------------------
// ActionBar
// ---------------------------------------------------------------------------

export default function ActionBar({ phase, output }) {
  const [mode, setMode] = useState(null); // null | "regen" | "edit"
  const [feedback, setFeedback] = useState("");
  const [editContent, setEditContent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  function resetState() {
    setMode(null);
    setFeedback("");
    setEditContent("");
    setError(null);
  }

  // ---- Approve ----
  async function handleApprove() {
    setSubmitting(true);
    setError(null);
    try {
      await postDecision({ action: "approve" });
      resetState();
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  // ---- Regenerate ----
  function openRegen() {
    setMode("regen");
    setEditContent("");
  }

  async function handleRegenSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      await postDecision({ action: "regenerate", feedback: feedback.trim() });
      resetState();
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  // ---- Edit / Save ----
  function openEdit() {
    setMode("edit");
    setEditContent(contentToString(output));
  }

  async function handleSaveSubmit() {
    setSubmitting(true);
    setError(null);
    try {
      await postDecision({ action: "save", content: editContent });
      resetState();
    } catch (e) {
      setError(e.message);
    } finally {
      setSubmitting(false);
    }
  }

  const canEdit =
    output &&
    (output.type === "text" || output.type === "json");

  return (
    <div className="action-bar">
      {error && (
        <div style={{ color: "#ef4444", fontSize: 13 }}>{error}</div>
      )}

      {/* Primary buttons */}
      {mode === null && (
        <div className="action-bar-buttons">
          <button
            className="btn-approve"
            onClick={handleApprove}
            disabled={submitting}
          >
            Approve
          </button>
          <button
            className="btn-regen"
            onClick={openRegen}
            disabled={submitting}
          >
            Regenerate
          </button>
          {canEdit && (
            <button
              className="btn-edit"
              onClick={openEdit}
              disabled={submitting}
            >
              Edit
            </button>
          )}
        </div>
      )}

      {/* Regenerate panel */}
      {mode === "regen" && (
        <div className="action-bar-extra">
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="Optional feedback for regeneration..."
            rows={3}
          />
          <div className="action-bar-extra-buttons">
            <button
              className="btn-submit-small btn-regen"
              onClick={handleRegenSubmit}
              disabled={submitting}
            >
              {submitting ? "Sending…" : "Regenerate"}
            </button>
            <button
              className="btn-cancel"
              onClick={resetState}
              disabled={submitting}
              style={{ padding: "7px 14px", fontSize: 13 }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Edit panel */}
      {mode === "edit" && (
        <div className="action-bar-extra">
          <textarea
            value={editContent}
            onChange={(e) => setEditContent(e.target.value)}
            rows={12}
            style={{ fontFamily: "monospace", fontSize: 12 }}
          />
          <div className="action-bar-extra-buttons">
            <button
              className="btn-submit-small btn-edit"
              onClick={handleSaveSubmit}
              disabled={submitting}
            >
              {submitting ? "Saving…" : "Save & Continue"}
            </button>
            <button
              className="btn-cancel"
              onClick={resetState}
              disabled={submitting}
              style={{ padding: "7px 14px", fontSize: 13 }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
