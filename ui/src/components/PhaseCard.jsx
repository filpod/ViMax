import React from "react";
import OutputRenderer from "./OutputRenderer.jsx";
import ActionBar from "./ActionBar.jsx";

// Status badge label map
const STATUS_LABELS = {
  pending: "Pending",
  running: "Running",
  awaiting_decision: "Awaiting Decision",
  approved: "Approved",
  regenerating: "Regenerating",
};

export default function PhaseCard({ phase, isActive }) {
  const { name, label, status, output, attempt } = phase;

  return (
    <div className={`phase-card ${status}`}>
      {/* Header row */}
      <div className="phase-card-header">
        {/* Spinner while running */}
        {(status === "running" || status === "regenerating") && (
          <div className="spinner" />
        )}

        <span className="phase-label">{label}</span>

        <span className={`status-badge ${status}`}>
          {STATUS_LABELS[status] || status}
        </span>

        {attempt > 1 && (
          <span className="phase-attempt">Attempt {attempt}</span>
        )}
      </div>

      {/* Output */}
      {output && <OutputRenderer output={output} />}

      {/* Action bar (only when awaiting decision) */}
      {status === "awaiting_decision" && (
        <ActionBar phase={name} output={output} />
      )}
    </div>
  );
}
