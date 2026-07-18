import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface CallToActionProps {
  startFrame: number;
  endFrame: number;
}

export const CallToAction: React.FC<CallToActionProps> = ({ startFrame, endFrame }) => {
  const duration = endFrame - startFrame;

  const titleOpacity = spring({
    frame: startFrame,
    fps: 30,
    config: { damping: 20, stiffness: 150 },
    startFrame,
    endFrame: startFrame + 50,
  });

  const taglineOpacity = spring({
    frame: startFrame,
    fps: 30,
    config: { damping: 20, stiffness: 150 },
    startFrame: startFrame + 30,
    endFrame: startFrame + 80,
  });

  const buttonOpacity = spring({
    frame: startFrame,
    fps: 30,
    config: { damping: 20, stiffness: 150 },
    startFrame: startFrame + 60,
    endFrame: startFrame + 110,
  });

  const footerOpacity = spring({
    frame: startFrame,
    fps: 30,
    config: { damping: 20, stiffness: 150 },
    startFrame: startFrame + 90,
    endFrame: startFrame + 140,
  });

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
        <div style={{ opacity: titleOpacity, transform: `translateY(${interpolate(titleOpacity, [0, 1], [50, 0])}px)` }}>
          <span
            style={{
              fontSize: "64px",
              fontWeight: "800",
              color: "#ffffff",
              letterSpacing: "-2px",
              textShadow: "0 0 40px rgba(0, 212, 255, 0.5), 0 0 80px rgba(0, 212, 255, 0.3)",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            ⚡ PromptForge
          </span>
        </div>

        <div style={{ opacity: taglineOpacity, transform: `translateY(${interpolate(taglineOpacity, [0, 1], [30, 0])}px)`, marginTop: "20px" }}>
          <span
            style={{
              fontSize: "24px",
              fontWeight: "400",
              color: "#00d4ff",
              letterSpacing: "3px",
              textTransform: "uppercase",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            The Future of Firmware Development
          </span>
        </div>

        <div style={{ opacity: buttonOpacity, transform: `translateY(${interpolate(buttonOpacity, [0, 1], [20, 0])}px)`, marginTop: "48px" }}>
          <div
            style={{
              background: "linear-gradient(135deg, #00d4ff, #0088cc)",
              borderRadius: "12px",
              padding: "18px 48px",
              boxShadow: "0 0 40px rgba(0, 212, 255, 0.3), 0 0 80px rgba(0, 212, 255, 0.1)",
            }}
          >
            <span
              style={{
                fontSize: "22px",
                fontWeight: "700",
                color: "#ffffff",
                letterSpacing: "2px",
                textTransform: "uppercase",
                fontFamily: "system-ui, -apple-system, sans-serif",
              }}
            >
              Get Started Today
            </span>
          </div>
        </div>

        <div style={{ opacity: footerOpacity, transform: `translateY(${interpolate(footerOpacity, [0, 1], [20, 0])}px)`, marginTop: "48px" }}>
          <span
            style={{
              fontSize: "14px",
              color: "#6b7280",
              textAlign: "center",
              maxWidth: "600px",
              lineHeight: "1.8",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            promptforge.dev • GitHub • Documentation • MIT License
          </span>
          <div
            style={{
              width: "80px",
              height: "2px",
              background: "linear-gradient(90deg, transparent, #00d4ff, transparent)",
              borderRadius: "2px",
              margin: "16px auto 0",
            }}
          />
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};
