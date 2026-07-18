import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface V2StorageWorkspacesProps {
  startFrame: number;
  endFrame: number;
}

const tables = [
  "settings", "providers", "models", "boards", "devices",
  "workspaces", "agents", "approvals", "events",
];

const bullets = [
  "Workspace Manager (workspaces / projects / build history / logs)",
  "Artifact Service",
  "v1 filesystem layout preserved",
  "External PlatformIO / Arduino import",
];

export const V2StorageWorkspaces: React.FC<V2StorageWorkspacesProps> = ({ startFrame, endFrame }) => {
  const duration = endFrame - startFrame;

  return (
    <Sequence from={startFrame} duration={duration}>
      <AbsoluteFill
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          background: "linear-gradient(135deg, #0a0a0f 0%, #1a1a2e 50%, #16213e 100%)",
          padding: "80px",
        }}
      >
        <div style={{ opacity: spring({ frame: startFrame, fps: 30, startFrame, endFrame: startFrame + 40, config: { damping: 20, stiffness: 150 } }) }}>
          <span
            style={{
              fontSize: "48px",
              fontWeight: "700",
              color: "#00d4ff",
              textAlign: "center",
              marginBottom: "16px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            Storage & Workspaces
          </span>
          <span
            style={{
              fontSize: "20px",
              color: "#a0aec0",
              textAlign: "center",
              maxWidth: "800px",
              lineHeight: "1.6",
              marginBottom: "32px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            Local SQLite with migrations — your projects, your data.
          </span>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: "12px",
            maxWidth: "800px",
            width: "100%",
            marginBottom: "32px",
          }}
        >
          {tables.map((tbl, index) => (
            <div
              key={index}
              style={{
                opacity: spring({
                  frame: startFrame,
                  fps: 30,
                  startFrame: startFrame + 50 + index * 9,
                  endFrame: startFrame + 90 + index * 9,
                  config: { damping: 20, stiffness: 150 },
                }),
                background: "rgba(0, 212, 255, 0.05)",
                border: "1px solid rgba(0, 212, 255, 0.2)",
                borderRadius: "10px",
                padding: "12px",
                textAlign: "center",
                color: "#ffffff",
                fontSize: "14px",
                fontWeight: "600",
                fontFamily: "system-ui, -apple-system, sans-serif",
              }}
            >
              {tbl}
            </div>
          ))}
        </div>

        <div style={{ maxWidth: "900px", width: "100%" }}>
          {bullets.map((bullet, index) => (
            <div
              key={index}
              style={{
                opacity: spring({
                  frame: startFrame,
                  fps: 30,
                  startFrame: startFrame + 140 + index * 12,
                  endFrame: startFrame + 180 + index * 12,
                  config: { damping: 20, stiffness: 150 },
                }),
                display: "flex",
                alignItems: "center",
                gap: "16px",
                background: "rgba(0, 212, 255, 0.04)",
                border: "1px solid rgba(0, 212, 255, 0.15)",
                borderRadius: "12px",
                padding: "13px 24px",
                marginBottom: "10px",
                fontFamily: "system-ui, -apple-system, sans-serif",
              }}
            >
              <span style={{ color: "#00d4ff", fontSize: "20px" }}>▸</span>
              <span style={{ fontSize: "17px", color: "#ffffff", lineHeight: "1.5" }}>{bullet}</span>
            </div>
          ))}
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};
