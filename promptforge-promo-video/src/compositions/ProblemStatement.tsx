import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface ProblemStatementProps {
  startFrame: number;
  endFrame: number;
}

const problems = [
  { icon: "🔧", title: "Fragmented Toolchain", desc: "Separate tools for editing, building, flashing, monitoring" },
  { icon: "📚", title: "Steep Learning Curve", desc: "Hardware registers, datasheets, HALs, RTOS concepts" },
  { icon: "🐛", title: "Debugging Blind Spots", desc: "Limited visibility into hardware behavior at runtime" },
  { icon: "⏱️", title: "Slow Iteration Cycles", desc: "Edit → Build → Flash → Test → Repeat takes minutes" },
  { icon: "🔒", title: "Safety & Correctness", desc: "One register mistake can brick hardware or cause undefined behavior" },
  { icon: "🤖", title: "AI Gap", desc: "LLMs generate code but lack hardware context & validation" },
];

export const ProblemStatement: React.FC<ProblemStatementProps> = ({ startFrame, endFrame }) => {
  const duration = endFrame - startFrame;

  return (
    <Sequence from={startFrame} duration={duration}>
      <AbsoluteFill
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          background: "linear-gradient(135deg, #0a0a0f 0%, #1a0a1a 50%, #0a0a0f 100%)",
          padding: "80px",
        }}
      >
        <div style={{ opacity: spring({ frame: startFrame, fps: 30, startFrame, endFrame: startFrame + 40, config: { damping: 20, stiffness: 150 } }) }}>
          <span
            style={{
              fontSize: "48px",
              fontWeight: "700",
              color: "#ff6b6b",
              textAlign: "center",
              marginBottom: "16px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            The Embedded Development Problem
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
            Building firmware today means juggling fragmented tools, fighting hardware complexity,
            and hoping your code works on real silicon.
          </span>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: "24px",
            marginTop: "60px",
            maxWidth: "1100px",
            width: "100%",
          }}
        >
          {problems.map((problem, index) => (
            <div
              key={index}
              style={{
                opacity: spring({
                  frame: startFrame,
                  fps: 30,
                  startFrame: startFrame + 60 + index * 30,
                  endFrame: startFrame + 100 + index * 30,
                  config: { damping: 20, stiffness: 150 },
                }),
                transform: `translateY(${interpolate(
                  spring({
                    frame: startFrame,
                    fps: 30,
                    startFrame: startFrame + 60 + index * 30,
                    endFrame: startFrame + 100 + index * 30,
                    config: { damping: 20, stiffness: 150 },
                  }),
                  [0, 1],
                  [40, 0]
                )}px)`,
                background: "rgba(255, 255, 255, 0.03)",
                border: "1px solid rgba(255, 107, 107, 0.2)",
                borderRadius: "12px",
                padding: "24px",
                textAlign: "center",
                backdropFilter: "blur(10px)",
              }}
            >
              <span style={{ fontSize: "40px", marginBottom: "12px" }}>{problem.icon}</span>
              <span
                style={{
                  fontSize: "18px",
                  fontWeight: "600",
                  color: "#ffffff",
                  marginBottom: "8px",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                }}
              >
                {problem.title}
              </span>
              <span
                style={{
                  fontSize: "14px",
                  color: "#a0aec0",
                  lineHeight: "1.5",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                }}
              >
                {problem.desc}
              </span>
            </div>
          ))}
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};