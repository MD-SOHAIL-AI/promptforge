import React from "react";
import { AbsoluteFill, interpolate, spring, Sequence } from "remotion";

interface EditorShowcaseProps {
  startFrame: number;
  endFrame: number;
}

const codeLines = [
  '#include "driver/gpio.h"',
  "",
  'void app_main(void) {',
  '    gpio_set_direction(GPIO_NUM_2, GPIO_MODE_OUTPUT);',
  "    while (1) {",
  '        gpio_set_level(GPIO_NUM_2, 1);',
  "        vTaskDelay(1000 / portTICK_PERIOD_MS);",
  '        gpio_set_level(GPIO_NUM_2, 0);',
  "        vTaskDelay(1000 / portTICK_PERIOD_MS);",
  "    }",
  "}",
];

export const EditorShowcase: React.FC<EditorShowcaseProps> = ({ startFrame, endFrame }) => {
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
              color: "#ffffff",
              textAlign: "center",
              marginBottom: "16px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            Monaco Editor
          </span>
          <span
            style={{
              fontSize: "20px",
              color: "#a0aec0",
              textAlign: "center",
              maxWidth: "700px",
              lineHeight: "1.6",
              marginBottom: "40px",
              fontFamily: "system-ui, -apple-system, sans-serif",
            }}
          >
            Full-featured code editor with syntax highlighting, autocomplete, and multi-language support.
          </span>
        </div>

        <div
          style={{
            opacity: spring({ frame: startFrame, fps: 30, startFrame: startFrame + 40, endFrame: startFrame + 80, config: { damping: 20, stiffness: 150 } }),
            background: "#1a1b26",
            border: "1px solid rgba(0, 212, 255, 0.3)",
            borderRadius: "12px",
            padding: "24px",
            maxWidth: "700px",
            width: "100%",
            fontFamily: "'Fira Code', 'Consolas', monospace",
            fontSize: "14px",
            lineHeight: "1.8",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              display: "flex",
              gap: "8px",
              marginBottom: "16px",
              paddingBottom: "12px",
              borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
            }}
          >
            <div style={{ width: "12px", height: "12px", borderRadius: "50%", background: "#ff5f56" }} />
            <div style={{ width: "12px", height: "12px", borderRadius: "50%", background: "#ffbd2e" }} />
            <div style={{ width: "12px", height: "12px", borderRadius: "50%", background: "#27c93f" }} />
          </div>
          {codeLines.map((line, index) => (
            <div key={index} style={{ display: "flex", gap: "16px" }}>
              <span style={{ color: "#4a4a5a", userSelect: "none", minWidth: "24px", textAlign: "right" }}>{index + 1}</span>
              <span
                style={{
                  color: line.startsWith("#") ? "#6a9955" : line.includes("//") ? "#6a9955" : line.includes('"') ? "#ce9178" : line.match(/^(void|int|char|gpio)/) ? "#569cd6" : line.includes("GPIO_") ? "#dcdcaa" : line.match(/^(    )?\w+\(/) ? "#dcdcaa" : "#cccccc",
                }}
              >
                {line}
              </span>
            </div>
          ))}
        </div>
      </AbsoluteFill>
    </Sequence>
  );
};
