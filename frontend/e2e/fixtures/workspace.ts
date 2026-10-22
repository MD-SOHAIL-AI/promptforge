import { expect, type Page } from "@playwright/test";

// Transport fixtures are confined to browser tests. Production uses the durable SSE endpoint.
export async function workspace(page: Page, runOverrides: Record<string, unknown> = {}, selection?: {provider_id: string; model_id: string}, agentDefault = false, options: {project?: Record<string, unknown>; filesMissing?: boolean; sessionMissing?: boolean; settings?: Record<string, unknown>; schema?: Record<string, unknown>} = {}) {
  const now = new Date().toISOString();
  const settings = options.settings ?? {};
  const project = {
    project_id: "p",
    project_name: "Temperature monitor",
    project_path: "D:/test/monitor",
    target_board: "esp32dev",
    metadata: {},
    updated_at: now,
    ...options.project,
  };
  const run = {
    run_id: "r",
    session_id: "s",
    project_id: "p",
    status: "running",
    created_at: now,
    events: [],
    plan: [],
    changed_files: [],
    ...runOverrides,
  };
  const session = {
    ...selection,
    session_id: "s",
    kind: "agent",
    project_id: "p",
    title: "Build a temperature monitor",
    active_run_id: "r",
    run_ids: ["r"],
    messages: [
      {
        message_id: "u",
        role: "user",
        content:
          "Inspect the firmware, improve the sensor reading, and verify the build.",
        created_at: now,
      },
    ],
  };
  await page.addInitScript(() => {
    const streams: Array<{
      url: string;
      closed: boolean;
      onmessage: ((event: { data: string }) => void) | null;
    }> = [];
    class Source {
      url: string;
      closed = false;
      onmessage = null;
      onerror = null;
      onopen: (() => void) | null = null;
      constructor(url: string) {
        this.url = url;
        streams.push(this);
        setTimeout(() => this.onopen?.(), 0);
      }
      close() {
        this.closed = true;
      }
    }
    Object.assign(window, { EventSource: Source, testStreams: streams });
  });
  if (!agentDefault) await page.addInitScript(() => {
    if (!localStorage.getItem("forgex-destination")) localStorage.setItem("forgex-destination", "chat");
  });
  await page.route("**/api/forgex/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/forgex", "");
    let body: unknown = {};
    if (path === "/projects") body = { projects: [project] };
    else if (path === "/projects/p/profile") body = {schema_version:1,adapter:"platformio",selected_environment:"debug",selection_required:false,
      environments:[{name:"debug",board:"esp32dev",framework:"arduino",platform:"espressif32"},{name:"release",board:"esp32dev",framework:"arduino",platform:"espressif32"}],warnings:[]};
    else if (path === "/projects/p/readiness") body = {observed_at:now, provider:{configured:true,cached_status:"unknown",checked_at:null,fresh:false},
      toolchain:{available:true},environment:{selection_required:false},permissions:{local_builds:false},capabilities:{generate:true,build:false}};
    else if (path.endsWith("/files") && options.filesMissing)
      return route.fulfill({status: 404, json: {code: "PROJECT_PATH_MISSING", message: "Missing project path"}});
    else if (path.endsWith("/files"))
      body = {
        entries: [
          {
            path: "main.cpp",
            name: "main.cpp",
            kind: "file",
            file_type: "cpp",
          },
        ],
      };
    else if (path === "/files/content")
      body = {
        project_id: "p",
        path: "main.cpp",
        content: "// Sensor firmware\nvoid setup() {}\nvoid loop() {}\n",
        file_type: "cpp",
      };
    else if (path === "/agent-runtime/providers")
      body = { enabled: true, providers: [] };
    else if (path === "/v1/chat/sessions") body = { sessions: [] };
    else if (path === "/agent-runtime/sessions")
      body = {
        sessions: url.searchParams.get("kind") === "chat" ? [] : [session],
      };
    else if (path === "/agent-runtime/sessions/s" && options.sessionMissing)
      return route.fulfill({status: 404, json: {code: "AGENT_SESSION_NOT_FOUND", message: "Session was not found"}});
    else if (path === "/agent-runtime/sessions/s") body = { session };
    else if (path === "/agent-runtime/runs/r") body = { run };
    else if (path === "/agent-runtime/runs/r/build-evidence") body = {state:runOverrides.build_result ? (runOverrides.build_result as {success?:boolean}).success ? runOverrides.build_verified ? "passed" : "stale" : "failed" : "idle",scope:"workspace"};
    else if (path.startsWith("/agent-runtime/jobs/") && runOverrides.job)
      body = {job: runOverrides.job, recovery: runOverrides.recovery};
    else if (path === "/agent-runtime/projects/p/trust") body = {trusted: false};
    else if (path.endsWith("/graph"))
      body = { graph: { nodes: [], edges: [] } };
    else if (path === "/models/providers") body = { providers: [] };
    else if (path === "/models/routes") body = { routes: [] };
    else if (path === "/devices/boards") body = { boards: [] };
    else if (path === "/settings") {
      if (route.request().method() === "PATCH") { const payload = route.request().postDataJSON(); Object.assign(settings, payload.settings ?? payload); }
      body = { settings };
    }
    else if (path === "/settings/schema") body = { schema: options.schema ?? {} };
    else if (path.includes("history")) body = { builds: [], history: [] };
    else if (path.includes("logs")) body = { entries: [], logs: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  if (agentDefault) await expect(page.getByRole("combobox", {name:"Agent mode"})).toBeVisible();
  else await expect(page.getByText("A little clarity.")).toBeVisible();
}
export async function emit(
  page: Page,
  sequence: number,
  activity: Record<string, unknown>,
) {
  await page.evaluate(
    ({ sequence, activity }) => {
      const packet = activity as Record<string, unknown>;
      const streams = (
        window as unknown as {
          testStreams: Array<{
            url: string;
            closed: boolean;
            onmessage?: (event: { data: string }) => void;
          }>;
        }
      ).testStreams;
      for (const source of streams)
        if (!source.closed && source.url.includes("/runs/r/events"))
          source.onmessage?.({
            data: JSON.stringify({
              session_id: "s",
              run_id: "r",
              event_type:
                typeof packet.eventType === "string"
                  ? packet.eventType
                  : "tool.output",
              message:
                typeof packet.message === "string"
                  ? packet.message
                  : undefined,
              sequence,
              activity_record: activity,
            }),
          });
    },
    {
      sequence,
      activity: {
        runId: "r",
        agentId: "main",
        parentId: null,
        startedAt: Date.now() - 4000,
        durationMs: null,
        details: {},
        ...activity,
      },
    },
  );
}
