import React, { useEffect, useRef } from "react";

export default function LogPanel({ logs }) {
  const bottomRef = useRef(null);

  // Auto-scroll to bottom whenever new logs arrive
  useEffect(() => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs]);

  return (
    <div className="log-panel">
      {logs.length === 0 && (
        <span style={{ color: "#444", fontSize: 12 }}>
          Pipeline logs will appear here…
        </span>
      )}
      {logs.map((entry, i) => (
        <div key={i} className={`log-entry${entry.error ? " error" : ""}`}>
          {entry.text}
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
