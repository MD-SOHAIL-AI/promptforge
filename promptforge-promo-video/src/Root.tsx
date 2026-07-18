import React from "react";
import { Composition } from "remotion";
import { registerRoot } from "remotion";
import { TitleScreen } from "./compositions/TitleScreen";
import { ProblemStatement } from "./compositions/ProblemStatement";
import { SolutionOverview } from "./compositions/SolutionOverview";
import { CoreWorkflow } from "./compositions/CoreWorkflow";
import { KeyFeatures } from "./compositions/KeyFeatures";
import { SupportedBoards } from "./compositions/SupportedBoards";
import { EditorShowcase } from "./compositions/EditorShowcase";
import { WorkflowInspector } from "./compositions/WorkflowInspector";
import { CallToAction } from "./compositions/CallToAction";
import { ForgeXV2Intro } from "./compositions/ForgeXV2Intro";
import { V2DesktopShell } from "./compositions/V2DesktopShell";
import { V2AgentLayer } from "./compositions/V2AgentLayer";
import { V2ModelRouter } from "./compositions/V2ModelRouter";
import { V2AgentRuntime } from "./compositions/V2AgentRuntime";
import { V2BridgeSystem } from "./compositions/V2BridgeSystem";
import { V2BoardDevice } from "./compositions/V2BoardDevice";
import { V2BuildFlashSim } from "./compositions/V2BuildFlashSim";
import { V2EditorInspector } from "./compositions/V2EditorInspector";
import { V2SettingsCenter } from "./compositions/V2SettingsCenter";
import { V2Security } from "./compositions/V2Security";
import { V2Observability } from "./compositions/V2Observability";
import { V2StorageWorkspaces } from "./compositions/V2StorageWorkspaces";
import { V2CallToAction } from "./compositions/V2CallToAction";

const PromptForgePromo: React.FC = () => {
  return (
    <>
      <TitleScreen startFrame={0} endFrame={150} />
      <ProblemStatement startFrame={150} endFrame={300} />
      <SolutionOverview startFrame={300} endFrame={450} />
      <CoreWorkflow startFrame={450} endFrame={700} />
      <KeyFeatures startFrame={700} endFrame={900} />
      <SupportedBoards startFrame={900} endFrame={1050} />
      <EditorShowcase startFrame={1050} endFrame={1150} />
      <WorkflowInspector startFrame={1150} endFrame={1250} />
      <CallToAction startFrame={1250} endFrame={1400} />
      <ForgeXV2Intro startFrame={1400} endFrame={1520} />
      <V2DesktopShell startFrame={1520} endFrame={1640} />
      <V2AgentLayer startFrame={1640} endFrame={1760} />
      <V2ModelRouter startFrame={1760} endFrame={1880} />
      <V2AgentRuntime startFrame={1880} endFrame={2000} />
      <V2BridgeSystem startFrame={2000} endFrame={2120} />
      <V2BoardDevice startFrame={2120} endFrame={2240} />
      <V2BuildFlashSim startFrame={2240} endFrame={2360} />
      <V2EditorInspector startFrame={2360} endFrame={2480} />
      <V2SettingsCenter startFrame={2480} endFrame={2600} />
      <V2Security startFrame={2600} endFrame={2720} />
      <V2Observability startFrame={2720} endFrame={2840} />
      <V2StorageWorkspaces startFrame={2840} endFrame={2960} />
      <V2CallToAction startFrame={2960} endFrame={3080} />
    </>
  );
};

const Root: React.FC = () => {
  return (
    <>
      <Composition
        id="PromptForgePromo"
        component={PromptForgePromo}
        durationInFrames={3080}
        fps={30}
        width={1920}
        height={1080}
      />
    </>
  );
};

registerRoot(Root);