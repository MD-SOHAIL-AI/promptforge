import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface V2CallToActionProps {
  startFrame: number;
  endFrame: number;
}

const bullets = [
  "Download the packaged desktop app",
  "Bring your own models + subscriptions — ForgeX never sees your keys",
  "Plan / generate / build / flash / monitor / rollback with confidence",
];

export const V2CallToAction: React.FC<V2CallToActionProps> = ({ startFrame, endFrame }) => {
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
              fontSize: "52px",
              fontWeight: "700",
              color: "#00d4ff",
              textAlign: "center",
              marginBottom: "16px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            ForgeX v2
          </span>
          <span
            style={{
              fontSize: "24px",
              color: "#ffffff",
              textAlign: "center",
              maxWidth: "900px",
              lineHeight: "1.6",
              marginBottom: "48px",
              fontWeight: "600",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            The AI-native Embedded Development Environment.
          </span>
        </div>

        <div style={{ maxWidth: "900px", width: "100%" }}>
          {bullets.map((bullet, index) => (
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
                display: "flex",
                alignItems: "center",
                gap: "16px",
                background: "rgba(0, 212, 255, 0.06)",
                border: "1px solid rgba(0, 212, 255, 0.25)",
                borderRadius: "12px",
                padding: "18px 24px",
                marginBottom: "14px",
                fontFamily: "system-ui, -apple-system, sans-serif",
              }}
            >
              <span style={{ color: "#00d4ff", fontSize: "22px" }}>✓</span>
              <span style={{ fontSize: "19px", color: "#ffffff", lineHeight: "1.5" }}>{bullet}</span>
            </div>
          ))}
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};
