import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface V2BoardDeviceProps {
  startFrame: number;
  endFrame: number;
}

const boards = [
  "ESP32", "ESP32-C3", "ESP32-S3", "ESP32-C6",
  "RP2040", "STM32", "NRF52", "Teensy", "AVR", "Custom",
];

const templates = ["blink", "sensor", "WiFi", "BLE", "I2C", "SPI", "FreeRTOS", "OTA"];

const bullets = [
  "Plugin system for board packs",
  "Auto device detection via USB VID/PID + probe discovery",
  "Device Tools panel with trust status",
];

export const V2BoardDevice: React.FC<V2BoardDeviceProps> = ({ startFrame, endFrame }) => {
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
            Board & Device Registry
          </span>
          <span
            style={{
              fontSize: "20px",
              color: "#a0aec0",
              textAlign: "center",
              maxWidth: "800px",
              lineHeight: "1.6",
              marginBottom: "28px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            Plug in and go — every board is recognized automatically.
          </span>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(5, 1fr)",
            gap: "12px",
            maxWidth: "950px",
            width: "100%",
            marginBottom: "24px",
          }}
        >
          {boards.map((board, index) => (
            <div
              key={index}
              style={{
                opacity: spring({
                  frame: startFrame,
                  fps: 30,
                  startFrame: startFrame + 50 + index * 7,
                  endFrame: startFrame + 90 + index * 7,
                  config: { damping: 20, stiffness: 150 },
                }),
                background: "rgba(0, 212, 255, 0.05)",
                border: "1px solid rgba(0, 212, 255, 0.2)",
                borderRadius: "10px",
                padding: "12px",
                textAlign: "center",
                color: "#ffffff",
                fontSize: "14px",
                fontWeight: "600",
                fontFamily: "system-ui, -apple-system, sans-serif",
              }}
            >
              {board}
            </div>
          ))}
        </div>

        <div
          style={{
            display: "flex",
            gap: "10px",
            flexWrap: "wrap",
            justifyContent: "center",
            maxWidth: "900px",
            marginBottom: "24px",
          }}
        >
          {templates.map((tpl, index) => (
            <div
              key={index}
              style={{
                opacity: spring({
                  frame: startFrame,
                  fps: 30,
                  startFrame: startFrame + 120 + index * 6,
                  endFrame: startFrame + 150 + index * 6,
                  config: { damping: 20, stiffness: 150 },
                }),
                background: "rgba(0, 212, 255, 0.04)",
                border: "1px solid rgba(0, 212, 255, 0.15)",
                borderRadius: "8px",
                padding: "8px 16px",
                color: "#00d4ff",
                fontSize: "13px",
                fontFamily: "system-ui, -apple-system, sans-serif",
              }}
            >
              {tpl}
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
                  startFrame: startFrame + 175 + index * 12,
                  endFrame: startFrame + 210 + index * 12,
                  config: { damping: 20, stiffness: 150 },
                }),
                display: "flex",
                alignItems: "center",
                gap: "16px",
                background: "rgba(0, 212, 255, 0.04)",
                border: "1px solid rgba(0, 212, 255, 0.15)",
                borderRadius: "12px",
                padding: "12px 24px",
                marginBottom: "10px",
                fontFamily: "system-ui, -apple-system, sans-serif",
              }}
            >
              <span style={{ color: "#00d4ff", fontSize: "20px" }}>▸</span>
              <span style={{ fontSize: "16px", color: "#ffffff", lineHeight: "1.5" }}>{bullet}</span>
            </div>
          ))}
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};
