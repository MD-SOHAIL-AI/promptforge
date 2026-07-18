import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface WorkflowInspectorProps {
  startFrame: number;
  endFrame: number;
}

const stages = [
  { name: "Plan", status: "completed", duration: "0.4s", color: "#27c93f" },
  { name: "Generate", status: "completed", duration: "2.1s", color: "#27c93f" },
  { name: "Validate", status: "completed", duration: "1.2s", color: "#27c93f" },
  { name: "Build", status: "running", duration: "3.7s", color: "#00d4ff" },
  { name: "Flash", status: "pending", duration: "—", color: "#4a4a5a" },
  { name: "Monitor", status: "pending", duration: "—", color: "#4a4a5a" },
  { name: "Simulate", status: "pending", duration: "—", color: "#4a4a5a" },
];

export const WorkflowInspector: React.FC<WorkflowInspectorProps> = ({ startFrame, endFrame }) => {
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
              marginBottom: "16px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            Workflow Inspector
          </span>
          <span
            style={{
              fontSize: "20px",
              color: "#a0aec0",
              textAlign: "center",
              maxWidth: "700px",
              lineHeight: "1.6",
              marginBottom: "48px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            Real-time visibility into every stage of the firmware pipeline.
          </span>
        </div>

        <div
          style={{
            opacity: spring({ frame: startFrame, fps: 30, startFrame: startFrame + 40, endFrame: startFrame + 80, config: { damping: 20, stiffness: 150 } }),
            background: "rgba(0, 0, 0, 0.4)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
            borderRadius: "12px",
            padding: "24px",
            maxWidth: "700px",
            width: "100%",
            backdropFilter: "blur(10px)",
          }}
        >
          {stages.map((stage, index) => (
            <div
              key={index}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "16px",
                padding: "12px 0",
                borderBottom: index < stages.length - 1 ? "1px solid rgba(255, 255, 255, 0.05)" : "none",
              }}
            >
              <div
                style={{
                  width: "10px",
                  height: "10px",
                  borderRadius: "50%",
                  background: stage.color,
                  boxShadow: stage.status === "running" ? `0 0 12px ${stage.color}` : "none",
                  animation: stage.status === "running" ? "pulse 1s infinite" : "none",
                }}
              />
              <span
                style={{
                  fontSize: "15px",
                  fontWeight: "500",
                  color: stage.status === "pending" ? "#4a4a5a" : "#ffffff",
                  minWidth: "80px",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                }}
              >
                {stage.name}
              </span>
              <span
                style={{
                  fontSize: "13px",
                  color: stage.status === "completed" ? "#27c93f" : stage.status === "running" ? "#00d4ff" : "#4a4a5a",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                }}
              >
                {stage.status === "completed" && "✓ "}
                {stage.status === "running" && "⟳ "}
                {stage.status === "pending" && "— "}
                {stage.duration}
              </span>
              <div style={{ flex: 1 }} />
              <div
                style={{
                  height: "6px",
                  borderRadius: "3px",
                  width: "120px",
                  background: "rgba(255, 255, 255, 0.08)",
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    height: "100%",
                    width: stage.status === "completed" ? "100%" : stage.status === "running" ? "60%" : "0%",
                    borderRadius: "3px",
                    background: `linear-gradient(90deg, ${stage.color}, ${stage.color}88)`,
                    transition: "width 0.3s ease",
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};
