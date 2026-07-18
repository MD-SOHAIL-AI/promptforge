import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("png");
Config.setChromiumOpenGlRenderer("swiftshader");
Config.setOverwriteOutput(true);
Config.setEntryPoint("src/Root.tsx");

export const remotionConfig = Config;