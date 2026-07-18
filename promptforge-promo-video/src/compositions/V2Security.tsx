import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface V2SecurityProps {
  startFrame: number;
  endFrame: number;
}

const actions = ["flash", "erase", "fuse", "OTA", "debug", "reset"];

const bullets = [
  "No hardware action without explicit human approval",
  "One-time approval tokens invalidated by any change",
  "Bridge reviews require approve / reject",
  "Credential boundaries — no tokens / cookies / OAuth scraping",
  "Fail-closed everywhere",
];

export const V2Security: React.FC<V2SecurityProps> = ({ startFrame, endFrame }) => {
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
            Security by Default
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
            Hardware is dangerous — every action needs a human.
          </span>
        </div>

        <div
          style={{
            display: "flex",
            gap: "12px",
            flexWrap: "wrap",
            justifyContent: "center",
            maxWidth: "900px",
            marginBottom: "32px",
          }}
        >
          {actions.map((action, index) => (
            <div
              key={index}
              style={{
                opacity: spring({
                  frame: startFrame,
                  fps: 30,
                  startFrame: startFrame + 50 + index * 10,
                  endFrame: startFrame + 90 + index * 10,
                  config: { damping: 20, stiffness: 150 },
                }),
                background: "rgba(255, 80, 80, 0.08)",
                border: "1px solid rgba(255, 80, 80, 0.3)",
                borderRadius: "10px",
                padding: "12px 20px",
                color: "#ff8a8a",
                fontSize: "15px",
                fontWeight: "600",
                fontFamily: "system-ui, -apple-system, sans-serif",
              }}
            >
              requires approval · {action}
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
                  startFrame: startFrame + 115 + index * 12,
                  endFrame: startFrame + 155 + index * 12,
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
