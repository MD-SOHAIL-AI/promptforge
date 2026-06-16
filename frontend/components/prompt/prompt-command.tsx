"use client";

import { useEffect, useRef, useState } from "react";
import { Command, CornerDownLeft, History, LoaderCircle, Play, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface PromptCommandProps {
  isExecuting: boolean;
  onExecute: (prompt: string) => Promise<void>;
}

const DEFAULT_PROMPT = "Build an ESP32 weather station using WiFi and DHT22";

export function PromptCommand({ isExecuting, onExecute }: PromptCommandProps) {
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, []);

  const submit = () => {
    if (!prompt.trim() || isExecuting) return;
    void onExecute(prompt);
  };

  return (
    <section className="border-b border-border bg-[#1b1f24] p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Command className="h-3.5 w-3.5 text-[#8b949e]" />
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[#a5adb7]">Engineering Command</span>
        </div>
        <div className="flex items-center gap-2 font-mono text-[10px] text-[#6e7681]">
          <Sparkles className="h-3 w-3" />
          Structured execution
        </div>
      </div>

      <div className="rounded-[6px] border border-[#3b424c] bg-[#15181d] shadow-[0_1px_0_rgba(255,255,255,0.025)] focus-within:border-[#59636f] focus-within:ring-1 focus-within:ring-[#59636f]/30">
        <textarea
          ref={inputRef}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              submit();
            }
          }}
          className="min-h-[74px] w-full resize-none bg-transparent px-3.5 pt-3 text-[13px] leading-5 text-[#e6edf3] outline-none placeholder:text-[#59616b]"
          placeholder="Describe the firmware, target board, peripherals, and expected behavior..."
          spellCheck={false}
          aria-label="Engineering command"
        />
        <div className="flex items-center justify-between border-t border-[#2d333b] px-2 py-1.5">
          <div className="flex items-center gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="sm" className="h-7 gap-1.5 text-[11px] text-muted-foreground">
                  <History className="h-3.5 w-3.5" />
                  History
                </Button>
              </TooltipTrigger>
              <TooltipContent>Prompt history is reserved for a future release.</TooltipContent>
            </Tooltip>
            <span className="hidden font-mono text-[10px] text-[#59616b] sm:inline">Target and framework are resolved by the planner</span>
          </div>
          <Button onClick={submit} disabled={isExecuting || !prompt.trim()} size="sm" className="h-8 gap-2 bg-[#e86f3d] px-3 text-white hover:bg-[#f07846]">
            {isExecuting ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5 fill-current" />}
            {isExecuting ? "Executing" : "Execute"}
            <span className="ml-1 flex items-center gap-0.5 rounded-[3px] border border-white/15 bg-black/10 px-1 py-0.5 font-mono text-[9px] font-normal text-white/75">
              Ctrl <CornerDownLeft className="h-2.5 w-2.5" />
            </span>
          </Button>
        </div>
      </div>
    </section>
  );
}
