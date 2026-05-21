import React, { useState } from "react";

export default function StartForm({ onStart }) {
  const [idea, setIdea] = useState("");
  const [userRequirement, setUserRequirement] = useState("");
  const [style, setStyle] = useState("");
  const [configPath, setConfigPath] = useState("configs/idea2video.yaml");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!idea.trim()) return;
    setLoading(true);
    await onStart({
      idea: idea.trim(),
      user_requirement: userRequirement.trim(),
      style: style.trim(),
      config_path: configPath.trim() || "configs/idea2video.yaml",
    });
    setLoading(false);
  }

  return (
    <form className="start-form" onSubmit={handleSubmit}>
      <h2>New Pipeline</h2>

      <div className="form-group">
        <label>Idea *</label>
        <textarea
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          placeholder="Describe your video idea..."
          rows={3}
          required
        />
      </div>

      <div className="form-group">
        <label>User Requirement</label>
        <textarea
          value={userRequirement}
          onChange={(e) => setUserRequirement(e.target.value)}
          placeholder="Any specific requirements or constraints..."
          rows={2}
        />
      </div>

      <div className="form-group">
        <label>Style</label>
        <textarea
          value={style}
          onChange={(e) => setStyle(e.target.value)}
          placeholder="Visual style description (e.g. cinematic, anime, photorealistic)..."
          rows={2}
        />
      </div>

      <div className="form-group">
        <label>Config Path</label>
        <input
          type="text"
          value={configPath}
          onChange={(e) => setConfigPath(e.target.value)}
          placeholder="configs/idea2video.yaml"
        />
      </div>

      <button
        type="submit"
        className="btn-primary"
        disabled={loading || !idea.trim()}
        style={{ alignSelf: "flex-start" }}
      >
        {loading ? "Starting…" : "Start Pipeline"}
      </button>
    </form>
  );
}
