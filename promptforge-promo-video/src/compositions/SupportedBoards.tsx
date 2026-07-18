import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface SupportedBoardsProps {
  startFrame: number;
  endFrame: number;
}

const boards = [
  { name: "ESP32", chip: "Xtensa LX6", icon: "🔵" },
  { name: "ESP32-S3", chip: "Xtensa LX7", icon: "🔷" },
  { name: "ESP32-C3", chip: "RISC-V", icon: "🟢" },
  { name: "STM32 Nucleo", chip: "ARM Cortex-M", icon: "🟠" },
  { name: "STM32 BluePill", chip: "ARM Cortex-M3", icon: "🔶" },
  { name: "Arduino Uno", chip: "ATmega328P", icon: "🟣" },
];

export const SupportedBoards: React.FC<SupportedBoardsProps> = ({ startFrame, endFrame }) => {
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
              color: "#ffffff",
              textAlign: "center",
              marginBottom: "16px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            Supported Boards
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
            Targeting the most popular microcontroller platforms out of the box.
          </span>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: "24px",
            maxWidth: "900px",
            width: "100%",
          }}
        >
          {boards.map((board, index) => (
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
                padding: "32px 24px",
                textAlign: "center",
                backdropFilter: "blur(10px)",
              }}
            >
              <span style={{ fontSize: "48px", marginBottom: "12px" }}>{board.icon}</span>
              <span
                style={{
                  fontSize: "20px",
                  fontWeight: "600",
                  color: "#ffffff",
                  marginBottom: "6px",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                }}
              >
                {board.name}
              </span>
              <span
                style={{
                  fontSize: "14px",
                  color: "#00d4ff",
                  fontFamily: "system-ui, -apple-system, sans-serif",
                }}
              >
                {board.chip}
              </span>
            </div>
          ))}
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};
