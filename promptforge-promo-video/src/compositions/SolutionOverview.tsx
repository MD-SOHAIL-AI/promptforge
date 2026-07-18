import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface SolutionOverviewProps {
  startFrame: number;
  endFrame: number;
}

const pillars = [
  { icon: "🧠", title: "AI-Native Workflow", desc: "Natural language to firmware in one place" },
  { icon: "🔗", title: "Unified Interface", desc: "No more juggling separate tools and terminals" },
  { icon: "⚡", title: "Instant Feedback", desc: "Build, flash, simulate in seconds, not minutes" },
  { icon: "🛡️", title: "Hardware-Aware AI", desc: "LLMs guided by board specs and validation" },
];

export const SolutionOverview: React.FC<SolutionOverviewProps> = ({ startFrame, endFrame }) => {
  const duration = endFrame - startFrame;

  return (
    <Sequence from={startFrame} duration={duration}>
      <AbsoluteFill
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          background: "linear-gradient(135deg, #0a0a0f 0%, #0a1a2e 50%, #0a0a0f 100%)",
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
            Enter PromptForge
          </span>
          <span
            style={{
              fontSize: "20px",
              color: "#a0aec0",
              textAlign: "center",
              maxWidth: "800px",
              lineHeight: "1.6",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            An AI-powered embedded development environment that unifies the entire firmware workflow
            into a single, intelligent interface.
          </span>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(2, 1fr)",
            gap: "24px",
            marginTop: "60px",
            maxWidth: "900px",
            width: "100%",
          }}
        >
          {pillars.map((pillar, index) => (
            <div
              key={index}
              style={{
                opacity: spring({
                  frame: startFrame,
                  fps: 30,
                  startFrame: startFrame + 60 + index * 20,
                  endFrame: startFrame + 100 + index * 20,
                  config: { damping: 20, stiffness: 150 },
                }),
                transform: `translateY(${interpolate(
                  spring({
                    frame: startFrame,
                    fps: 30,
                    startFrame: startFrame + 60 + index * 20,
                    endFrame: startFrame + 100 + index * 20,
                    config: { damping: 20, stiffness: 150 },
                  }),
                  [0, 1],
                  [40, 0]
                )}px)`,
                background: "rgba(0, 212, 255, 0.05)",
                border: "1px solid rgba(0, 212, 255, 0.2)",
                borderRadius: "12px",
                padding: "24px",
                textAlign: "center",
                backdropFilter: "blur(10px)",
              }}
            >
              <span style={{ fontSize: "40px", marginBottom: "12px" }}>{pillar.icon}</span>
              <span
                style={{
                  fontSize: "18px",
                  fontWeight: "600",
                  color: "#ffffff",
                  marginBottom: "8px",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                }}
              >
                {pillar.title}
              </span>
              <span
                style={{
                  fontSize: "14px",
                  color: "#a0aec0",
                  lineHeight: "1.5",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                }}
              >
                {pillar.desc}
              </span>
            </div>
          ))}
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};
