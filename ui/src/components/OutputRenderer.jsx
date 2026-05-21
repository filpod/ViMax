import React from "react";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mediaUrl(absolutePath) {
  // absolutePath is a filesystem path on the server.
  // Strip any leading slash so we can safely append after /api/media/
  const clean = absolutePath.replace(/^\//, "");
  return `/api/media/${encodeURIComponent(clean)}`;
}

// ---------------------------------------------------------------------------
// Sub-renderers
// ---------------------------------------------------------------------------

function TextOutput({ content }) {
  return (
    <div className="output-text">
      <pre>{content}</pre>
    </div>
  );
}

function JsonOutput({ content }) {
  const text =
    typeof content === "string"
      ? content
      : JSON.stringify(content, null, 2);
  return (
    <div className="output-json">
      <pre>{text}</pre>
    </div>
  );
}

function PortraitsOutput({ content }) {
  // content = [{name, views: {front: path, side: path, back: path}}]
  if (!Array.isArray(content) || content.length === 0) {
    return <span style={{ color: "#888", fontSize: 13 }}>No portraits generated.</span>;
  }
  return (
    <div className="output-portraits">
      {content.map((char) => (
        <div key={char.name} className="portrait-character">
          <div className="portrait-name">{char.name}</div>
          <div className="portrait-views">
            {Object.entries(char.views || {}).map(([view, path]) => (
              <div key={view} className="portrait-view">
                <img
                  src={mediaUrl(path)}
                  alt={`${char.name} ${view}`}
                  loading="lazy"
                />
                <span className="portrait-view-label">{view}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ImagesOutput({ content }) {
  // content = [{shot_idx, path, frame_type}]
  if (!Array.isArray(content) || content.length === 0) {
    return <span style={{ color: "#888", fontSize: 13 }}>No frames generated.</span>;
  }
  return (
    <div className="output-images">
      {content.map((item) => (
        <div
          key={`${item.shot_idx}-${item.frame_type}`}
          className="output-image-item"
        >
          <img src={mediaUrl(item.path)} alt={`Shot ${item.shot_idx} ${item.frame_type}`} loading="lazy" />
          <span className="output-image-caption">
            Shot {item.shot_idx} · {item.frame_type}
          </span>
        </div>
      ))}
    </div>
  );
}

function VideosOutput({ content }) {
  // content = [{shot_idx, path}]
  if (!Array.isArray(content) || content.length === 0) {
    return <span style={{ color: "#888", fontSize: 13 }}>No clips generated.</span>;
  }
  return (
    <div className="output-videos">
      {content.map((item) => (
        <div key={item.shot_idx} className="output-video-item">
          <video controls preload="metadata" src={mediaUrl(item.path)}>
            Your browser does not support HTML5 video.
          </video>
          <span className="output-video-caption">Shot {item.shot_idx}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main OutputRenderer
// ---------------------------------------------------------------------------

export default function OutputRenderer({ output }) {
  if (!output) return null;

  const { type, content } = output;

  switch (type) {
    case "text":
      return <TextOutput content={content} />;
    case "json":
      return <JsonOutput content={content} />;
    case "portraits":
      return <PortraitsOutput content={content} />;
    case "images":
      return <ImagesOutput content={content} />;
    case "videos":
      return <VideosOutput content={content} />;
    default:
      return (
        <div className="output-json">
          <pre>{JSON.stringify(output, null, 2)}</pre>
        </div>
      );
  }
}
