import React from "react";
import { AbsoluteFill, interpolate, spring } from "remotion";
import { Sequence } from "remotion";

interface TitleScreenProps {
  startFrame: number;
  endFrame: number;
}

export const TitleScreen: React.FC<TitleScreenProps> = ({ startFrame, endFrame }) => {
  const duration = endFrame - startFrame;

  const titleOpacity = spring({
    frame: startFrame,
    fps: 30,
    config: { damping: 20, stiffness: 150 },
    startFrame,
    endFrame: startFrame + 60,
  });

  const subtitleOpacity = spring({
    frame: startFrame,
    fps: 30,
    config: { damping: 20, stiffness: 150 },
    startFrame: startFrame + 30,
    endFrame: startFrame + 90,
  });

  const taglineOpacity = spring({
    frame: startFrame,
    fps: 30,
    config: { damping: 20, stiffness: 150 },
    startFrame: startFrame + 60,
    endFrame: startFrame + 120,
  });

  return (
    <Sequence from={startFrame} duration={duration}>
      <AbsoluteFill
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          background: "linear-gradient(135deg, #0a0a0f 0%, #1a1a2e 50%, #16213e 100%)",
        }}
      >
        <div style={{ opacity: titleOpacity, transform: `translateY(${interpolate(titleOpacity, [0, 1], [50, 0])}px)` }}>
          <span
            style={{
              fontSize: "80px",
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

        <div style={{ opacity: subtitleOpacity, transform: `translateY(${interpolate(subtitleOpacity, [0, 1], [30, 0])}px)`, marginTop: "16px" }}>
          <span
            style={{
              fontSize: "28px",
              fontWeight: "400",
              color: "#00d4ff",
              letterSpacing: "4px",
              textTransform: "uppercase",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            AI-Powered Embedded Development Environment
          </span>
        </div>

        <div style={{ opacity: taglineOpacity, transform: `translateY(${interpolate(taglineOpacity, [0, 1], [20, 0])}px)`, marginTop: "24px" }}>
          <span
            style={{
              fontSize: "20px",
              fontWeight: "300",
              color: "#a0aec0",
              maxWidth: "700px",
              textAlign: "center",
              lineHeight: "1.6",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            Natural language → firmware. Plan, generate, build, flash, simulate, and debug — from a single interface.
          </span>
        </div>

        <div style={{ opacity: taglineOpacity, marginTop: "48px" }}>
          <div
            style={{
              width: "120px",
              height: "3px",
              background: "linear-gradient(90deg, transparent, #00d4ff, transparent)",
              borderRadius: "2px",
            }}
          />
        </div>

        <div style={{ opacity: taglineOpacity, marginTop: "32px" }}>
          <span
            style={{
              fontSize: "14px",
              fontWeight: "500",
              color: "#6b7280",
              letterSpacing: "2px",
              textTransform: "uppercase",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            v1.0.0-rc • 1,096 tests passing • MIT License
          </span>
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};