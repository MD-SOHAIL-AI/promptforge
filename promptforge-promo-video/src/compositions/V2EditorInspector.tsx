import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface V2EditorInspectorProps {
  startFrame: number;
  endFrame: number;
}

const editor = ["tabs", "breadcrumbs", "minimap", "inline agent actions"];
const timeline = ["plan", "generate", "build", "flash", "monitor"];

const bullets = [
  "Workflow Timeline across every stage",
  "Generation Progress panel (live per-file, provider, repair/fallback counts)",
  "Chunked incremental generation with truth-artifact validation",
  "Task Inspector",
];

export const V2EditorInspector: React.FC<V2EditorInspectorProps> = ({ startFrame, endFrame }) => {
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
            Editor & Inspector
          </span>
          <span
            style={{
              fontSize: "20px",
              color: "#a0aec0",
              textAlign: "center",
              maxWidth: "800px",
              lineHeight: "1.6",
              marginBottom: "28px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            A Monaco workbench wired into the agent pipeline.
          </span>
        </div>

        <div style={{ display: "flex", gap: "40px", flexWrap: "wrap", justifyContent: "center", maxWidth: "1000px", marginBottom: "28px" }}>
          <div
            style={{
              opacity: spring({ frame: startFrame, fps: 30, startFrame: startFrame + 60, endFrame: startFrame + 100, config: { damping: 20, stiffness: 150 } }),
              background: "rgba(0, 212, 255, 0.05)",
              border: "1px solid rgba(0, 212, 255, 0.2)",
              borderRadius: "12px",
              padding: "18px 24px",
              maxWidth: "440px",
            }}
          >
            <span style={{ color: "#00d4ff", fontSize: "16px", fontWeight: "600", fontFamily: "system-ui, -apple-system, sans-serif" }}>Monaco workbench</span>
            <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", marginTop: "12px" }}>
              {editor.map((e, i) => (
                <span key={i} style={{ background: "rgba(0,0,0,0.3)", border: "1px solid rgba(0,212,255,0.15)", borderRadius: "8px", padding: "6px 12px", color: "#ffffff", fontSize: "13px", fontFamily: "system-ui, -apple-system, sans-serif" }}>{e}</span>
              ))}
            </div>
          </div>
          <div
            style={{
              opacity: spring({ frame: startFrame, fps: 30, startFrame: startFrame + 80, endFrame: startFrame + 120, config: { damping: 20, stiffness: 150 } }),
              background: "rgba(0, 212, 255, 0.05)",
              border: "1px solid rgba(0, 212, 255, 0.2)",
              borderRadius: "12px",
              padding: "18px 24px",
              maxWidth: "440px",
            }}
          >
            <span style={{ color: "#00d4ff", fontSize: "16px", fontWeight: "600", fontFamily: "system-ui, -apple-system, sans-serif" }}>Workflow Timeline</span>
            <div style={{ display: "flex", gap: "10px", flexWrap: "wrap", marginTop: "12px" }}>
              {timeline.map((t, i) => (
                <span key={i} style={{ background: "rgba(0,0,0,0.3)", border: "1px solid rgba(0,212,255,0.15)", borderRadius: "8px", padding: "6px 12px", color: "#ffffff", fontSize: "13px", fontFamily: "system-ui, -apple-system, sans-serif" }}>{t}</span>
              ))}
            </div>
          </div>
        </div>

        <div style={{ maxWidth: "900px", width: "100%" }}>
          {bullets.map((bullet, index) => (
            <div
              key={index}
              style={{
                opacity: spring({
                  frame: startFrame,
                  fps: 30,
                  startFrame: startFrame + 130 + index * 12,
                  endFrame: startFrame + 170 + index * 12,
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
