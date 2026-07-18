import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface KeyFeaturesProps {
  startFrame: number;
  endFrame: number;
}

const features = [
  { icon: "🧠", title: "Hardware-Aware Generation", desc: "LLMs understand board specs, pinouts, and memory constraints" },
  { icon: "🔍", title: "Validation Pipeline", desc: "Static analysis, formatting checks, and semantic verification" },
  { icon: "🔄", title: "Deterministic Retry Engine", desc: "Auto-correct and retry on build failures with context" },
  { icon: "⚡", title: "Streaming Execution", desc: "Real-time output as commands run in the workspace" },
  { icon: "📂", title: "Workspace Manager", desc: "Full project scaffolding with config presets per board" },
  { icon: "✏️", title: "Monaco Editor", desc: "VS Code-quality code editor with syntax highlighting" },
  { icon: "🔬", title: "Wokwi Simulation", desc: "Run and debug firmware in-browser without hardware" },
  { icon: "📋", title: "Workflow Inspector", desc: "Visualize and replay every stage of the firmware pipeline" },
];

export const KeyFeatures: React.FC<KeyFeaturesProps> = ({ startFrame, endFrame }) => {
  const duration = endFrame - startFrame;

  return (
    <Sequence from={startFrame} duration={duration}>
      <AbsoluteFill
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          background: "linear-gradient(135deg, #0a0a0f 0%, #16213e 50%, #0a0a0f 100%)",
          padding: "80px",
        }}
      >
        <div style={{ opacity: spring({ frame: startFrame, fps: 30, startFrame, endFrame: startFrame + 40, config: { damping: 20, stiffness: 150 } }) }}>
          <span
            style={{
              fontSize: "48px",
              fontWeight: "700",
              color: "#ffffff",
              textAlign: "center",
              marginBottom: "48px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            Key Features
          </span>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(4, 1fr)",
            gap: "20px",
            maxWidth: "1200px",
            width: "100%",
          }}
        >
          {features.map((feature, index) => (
            <div
              key={index}
              style={{
                opacity: spring({
                  frame: startFrame,
                  fps: 30,
                  startFrame: startFrame + 60 + index * 12,
                  endFrame: startFrame + 100 + index * 12,
                  config: { damping: 20, stiffness: 150 },
                }),
                transform: `translateY(${interpolate(
                  spring({
                    frame: startFrame,
                    fps: 30,
                    startFrame: startFrame + 60 + index * 12,
                    endFrame: startFrame + 100 + index * 12,
                    config: { damping: 20, stiffness: 150 },
                  }),
                  [0, 1],
                  [40, 0]
                )}px)`,
                background: "rgba(0, 212, 255, 0.04)",
                border: "1px solid rgba(0, 212, 255, 0.15)",
                borderRadius: "12px",
                padding: "20px",
                textAlign: "center",
                backdropFilter: "blur(10px)",
              }}
            >
              <span style={{ fontSize: "32px", marginBottom: "10px" }}>{feature.icon}</span>
              <span
                style={{
                  fontSize: "15px",
                  fontWeight: "600",
                  color: "#ffffff",
                  marginBottom: "6px",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                }}
              >
                {feature.title}
              </span>
              <span
                style={{
                  fontSize: "12px",
                  color: "#a0aec0",
                  lineHeight: "1.5",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                }}
              >
                {feature.desc}
              </span>
            </div>
          ))}
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};
