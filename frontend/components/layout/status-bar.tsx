import { Braces, Radio, Unplug, Wifi } from "lucide-react";

interface StatusBarProps {
  socketState: string;
  error: string | null;
}

export function StatusBar({ socketState, error }: StatusBarProps) {
  const connected = socketState === "open";
  return (
    <footer className="flex h-[22px] shrink-0 items-center justify-between border-t border-[#31363e] bg-[#20242a] px-2.5 font-mono text-[10px] text-[#8b949e]">
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1">
          {connected ? <Wifi className="h-3 w-3" /> : <Unplug className="h-3 w-3" />}
          progress:{socketState}
        </span>
        <span className={error ? "text-[#ff7b72]" : ""}>{error ?? "No problems detected"}</span>
      </div>
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1"><Radio className="h-3 w-3" /> serial:disconnected</span>
        <span className="flex items-center gap-1"><Braces className="h-3 w-3" /> TypeScript</span>
        <span>UTF-8</span>
      </div>
    </footer>
  );
}
