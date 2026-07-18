import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface CoreWorkflowProps {
  startFrame: number;
  endFrame: number;
}

const stages = [
  { icon: "📝", title: "Plan", desc: "Describe intent in natural language" },
  { icon: "⚙️", title: "Generate", desc: "AI produces hardware-aware firmware" },
  { icon: "✅", title: "Validate", desc: "Static & semantic checks pass" },
  { icon: "🔨", title: "Build", desc: "Compile with target toolchain" },
  { icon: "📦", title: "Flash", desc: "Deploy to target board" },
  { icon: "📊", title: "Monitor", desc: "Live serial & debug output" },
  { icon: "🔬", title: "Simulate", desc: "Wokwi in-browser simulation" },
];

export const CoreWorkflow: React.FC<CoreWorkflowProps> = ({ startFrame, endFrame }) => {
  const duration = endFrame - startFrame;

  return (
    <Sequence from={startFrame} duration={duration}>
      <AbsoluteFill
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          background: "linear-gradient(135deg, #0a0a0f 0%, #1a1a2e 50%, #0a0a0f 100%)",
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
              marginBottom: "16px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            Core Workflow
          </span>
          <span
            style={{
              fontSize: "20px",
              color: "#a0aec0",
              textAlign: "center",
              maxWidth: "800px",
              lineHeight: "1.6",
              marginBottom: "48px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            From idea to running firmware in seven seamless stages.
          </span>
        </div>

        <div
          style={{
            display: "flex",
            flexDirection: "row",
            flexWrap: "wrap",
            justifyContent: "center",
            gap: "16px",
            maxWidth: "1200px",
            width: "100%",
          }}
        >
          {stages.map((stage, index) => (
            <div
              key={index}
              style={{
                opacity: spring({
                  frame: startFrame,
                  fps: 30,
                  startFrame: startFrame + 60 + index * 15,
                  endFrame: startFrame + 100 + index * 15,
                  config: { damping: 20, stiffness: 150 },
                }),
                transform: `translateY(${interpolate(
                  spring({
                    frame: startFrame,
                    fps: 30,
                    startFrame: startFrame + 60 + index * 15,
                    endFrame: startFrame + 100 + index * 15,
                    config: { damping: 20, stiffness: 150 },
                  }),
                  [0, 1],
                  [40, 0]
                )}px)`,
                background: "rgba(0, 212, 255, 0.05)",
                border: "1px solid rgba(0, 212, 255, 0.2)",
                borderRadius: "12px",
                padding: "20px 24px",
                textAlign: "center",
                minWidth: "130px",
                flex: "1 1 0",
                backdropFilter: "blur(10px)",
                position: "relative",
              }}
            >
              <span style={{ fontSize: "32px", marginBottom: "8px" }}>{stage.icon}</span>
              <span
                style={{
                  fontSize: "16px",
                  fontWeight: "600",
                  color: "#ffffff",
                  marginBottom: "6px",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                }}
              >
                {stage.title}
              </span>
              <span
                style={{
                  fontSize: "12px",
                  color: "#a0aec0",
                  lineHeight: "1.4",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                }}
              >
                {stage.desc}
              </span>
              {index < stages.length - 1 && (
                <div
                  style={{
                    position: "absolute",
                    top: "50%",
                    right: "-14px",
                    transform: "translateY(-50%)",
                    color: "#00d4ff",
                    fontSize: "18px",
                    fontWeight: "700",
                    opacity: 0.5,
                  }}
                >
                  →
                </div>
              )}
            </div>
          ))}
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};
