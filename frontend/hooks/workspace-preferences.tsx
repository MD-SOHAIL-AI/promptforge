"use client";
import { createContext, useContext } from "react";
import type { ForgeXSettingValue } from "@/types";
export const WorkspacePreferences = createContext<Record<string, ForgeXSettingValue>>({});
export function useWorkspacePreferences() { return useContext(WorkspacePreferences); }
