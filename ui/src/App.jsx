import React, { useEffect, useRef, useState, useCallback } from "react";
import StartForm from "./components/StartForm.jsx";
import PhaseCard from "./components/PhaseCard.jsx";
import LogPanel from "./components/LogPanel.jsx";

// ---------------------------------------------------------------------------
// Initial phase shape builder
// ---------------------------------------------------------------------------

function makePhase(name, label) {
  return {
    name,
    label,
    status: "pending", // "pending" | "running" | "awaiting_decision" | "approved" | "regenerating"
    output: null,
    attempt: 1,
  };
}

// Base (non-scene) phases
const BASE_PHASES = [
  makePhase("story", "Story"),
  makePhase("characters", "Characters"),
  makePhase("portraits", "Character Portraits"),
  makePhase("script", "Script"),
];

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

export default function App() {
  const [phases, setPhases] = useState(BASE_PHASES);
  const [running, setRunning] = useState(false);
  const [currentPhase, setCurrentPhase] = useState(null);
  const [logs, setLogs] = useState([]);
  const [completed, setCompleted] = useState(null);   // { videoPath }
  const [pipelineError, setPipelineError] = useState(null);

  const wsRef = useRef(null);
  const reconnectTimerRef = useRef(null);

  // ------------------------------------------------------------------
  // Helpers to update a single phase by name
  // ------------------------------------------------------------------

  const updatePhase = useCallback((name, patch) => {
    setPhases((prev) => {
      const idx = prev.findIndex((p) => p.name === name);
      if (idx === -1) {
        // Dynamically add new scene phase
        return [...prev, { ...makePhase(name, name), ...patch }];
      }
      const updated = [...prev];
      updated[idx] = { ...updated[idx], ...patch };
      return updated;
    });
  }, []);

  const ensurePhase = useCallback((name, label) => {
    setPhases((prev) => {
      if (prev.find((p) => p.name === name)) return prev;
      return [...prev, makePhase(name, label)];
    });
  }, []);

  // ------------------------------------------------------------------
  // WebSocket connection
  // ------------------------------------------------------------------

  const connectWS = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState < 2) return; // already open/connecting

    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${window.location.host}/ws`);
    wsRef.current = ws;

    ws.onmessage = (evt) => {
      let msg;
      try {
        msg = JSON.parse(evt.data);
      } catch {
        return;
      }
      handleWsMessage(msg);
    };

    ws.onclose = () => {
      reconnectTimerRef.current = setTimeout(connectWS, 2000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    connectWS();
    return () => {
      clearTimeout(reconnectTimerRef.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [connectWS]);

  // ------------------------------------------------------------------
  // WebSocket message handler
  // ------------------------------------------------------------------

  function handleWsMessage(msg) {
    switch (msg.type) {
      case "phase_start": {
        const { phase, label } = msg;
        ensurePhase(phase, label);
        updatePhase(phase, { status: "running", label });
        setCurrentPhase(phase);
        break;
      }

      case "phase_complete": {
        const { phase, label, output } = msg;
        updatePhase(phase, { status: "awaiting_decision", output, label });
        break;
      }

      case "phase_approved": {
        updatePhase(msg.phase, { status: "approved" });
        break;
      }

      case "phase_regen": {
        updatePhase(msg.phase, { status: "regenerating", attempt: msg.attempt });
        break;
      }

      case "log": {
        const text = msg.message || "";
        setLogs((prev) => [...prev, { text, error: text.toLowerCase().startsWith("error") }]);
        break;
      }

      case "pipeline_complete": {
        setRunning(false);
        setCurrentPhase(null);
        setCompleted({ videoPath: msg.video_path });
        break;
      }

      case "pipeline_error": {
        setRunning(false);
        setCurrentPhase(null);
        setPipelineError(msg.error);
        break;
      }

      default:
        break;
    }
  }

  // ------------------------------------------------------------------
  // Start pipeline
  // ------------------------------------------------------------------

  async function handleStart(formData) {
    setRunning(true);
    setCompleted(null);
    setPipelineError(null);
    setLogs([]);
    setCurrentPhase(null);
    // Reset to base phases
    setPhases(BASE_PHASES.map((p) => ({ ...p, status: "pending", output: null, attempt: 1 })));

    try {
      const res = await fetch("/api/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to start pipeline");
      }
    } catch (e) {
      setRunning(false);
      setPipelineError(e.message);
    }
  }

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------

  return (
    <div className="app-layout">
      {/* Main area */}
      <div className="main-area">
        {/* Header */}
        <div className="app-header">
          <h1>ViMax</h1>
          <span className="subtitle">Interactive Pipeline</span>
        </div>

        {/* Start form (shown when not running) */}
        {!running && !completed && !pipelineError && (
          <StartForm onStart={handleStart} />
        )}

        {/* Restart button when done */}
        {(completed || pipelineError) && (
          <button
            className="btn-primary"
            style={{ width: "fit-content" }}
            onClick={() => {
              setCompleted(null);
              setPipelineError(null);
            }}
          >
            Start New Pipeline
          </button>
        )}

        {/* Pipeline complete banner */}
        {completed && (
          <div className="banner success">
            Pipeline complete! Video saved to:{" "}
            <a href={`/api/media/${encodeURIComponent(completed.videoPath)}`} target="_blank" rel="noreferrer">
              {completed.videoPath}
            </a>
          </div>
        )}

        {/* Pipeline error banner */}
        {pipelineError && (
          <div className="banner error">
            Pipeline error: {pipelineError}
          </div>
        )}

        {/* Phase cards */}
        {phases.map((phase) => (
          <PhaseCard
            key={phase.name}
            phase={phase}
            isActive={phase.name === currentPhase}
          />
        ))}
      </div>

      {/* Log sidebar */}
      <div className="sidebar">
        <div className="sidebar-header">Pipeline Logs</div>
        <LogPanel logs={logs} />
      </div>
    </div>
  );
}
