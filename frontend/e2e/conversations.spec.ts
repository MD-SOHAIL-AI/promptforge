import { expect, test, type Page } from "@playwright/test";
import { join } from "node:path";

// Transport fixtures are confined to browser tests. Production uses the durable SSE endpoint.
async function workspace(page: Page) {
  const now = new Date().toISOString();
  const project = {
    project_id: "p",
    project_name: "Temperature monitor",
    project_path: "D:/test/monitor",
    target_board: "esp32dev",
    metadata: {},
    updated_at: now,
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
  };
  const session = {
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
  await page.route("**/api/forgex/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace("/api/forgex", "");
    let body: unknown = {};
    if (path === "/projects") body = { projects: [project] };
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
    else if (path === "/agent-runtime/sessions/s") body = { session };
    else if (path === "/agent-runtime/runs/r") body = { run };
    else if (path.endsWith("/graph"))
      body = { graph: { nodes: [], edges: [] } };
    else if (path === "/models/providers") body = { providers: [] };
    else if (path === "/models/routes") body = { routes: [] };
    else if (path === "/devices/boards") body = { boards: [] };
    else if (path === "/settings") body = { settings: {} };
    else if (path === "/settings/schema") body = { schema: {} };
    else if (path.includes("history")) body = { builds: [], history: [] };
    else if (path.includes("logs")) body = { entries: [], logs: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await expect(page.getByText("A little clarity.")).toBeVisible();
}

async function emit(
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

test("separate destinations preserve drafts and fit narrow screens", async ({
  page,
}) => {
  await workspace(page);
  const composer = page
    .getByRole("textbox", { name: "Message", exact: true })
    .filter({ visible: true });
  await composer.fill("Explain a thermistor circuit");
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(page.getByText("Inspect the firmware, improve")).toBeVisible();
  await page.getByRole("button", { name: "Chat", exact: true }).click();
  await expect(composer).toHaveValue("Explain a thermistor circuit");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("button", { name: "Open navigation" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({ path: "test-results/chat-narrow.png" });
});

test("activity updates in place, nested specialists, plans, and terminal timers", async ({
  page,
}) => {
  await workspace(page);
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(page.getByText("Inspect the firmware, improve")).toBeVisible();
  await emit(page, 1, {
    id: "root",
    kind: "run",
    title: "Working",
    status: "running",
  });
  await emit(page, 2, {
    id: "plan",
    kind: "plan",
    title: "Plan",
    status: "running",
    details: {
      steps: [
        {
          id: "step1",
          text: "Inspect firmware",
          status: "completed",
          evidence: ["read"],
        },
        { id: "step2", text: "Verify the build", status: "in_progress" },
      ],
    },
  });
  await emit(page, 3, {
    id: "summary",
    kind: "summary",
    title: "Progress",
    status: "running",
    eventType: "assistant.summary.delta",
    message: "Checking the sensor configuration before running the compiler.",
    details: {
      summary: "Checking the sensor configuration before running the compiler.",
    },
  });
  await expect(
    page.getByLabel("ForgeX streaming response"),
  ).toContainText("Checking the sensor configuration");
  await emit(page, 4, {
    id: "command",
    kind: "command",
    title: "Command",
    status: "running",
    details: { command: "platformio run", output: "Compiling src/main.cpp" },
  });
  await expect(
    page.getByRole("button", { name: /Running command/ }),
  ).toHaveCount(1);
  await emit(page, 5, {
    id: "command",
    kind: "command",
    title: "Command",
    status: "completed",
    durationMs: 7200,
    details: { command: "platformio run", output: "SUCCESS", exit_code: 0 },
  });
  await emit(page, 5, {
    id: "command",
    kind: "command",
    title: "Command",
    status: "completed",
    durationMs: 7200,
  });
  await expect(page.getByRole("button", { name: /Command completed/ })).toHaveCount(
    1,
  );
  await expect(
    page.getByRole("button", { name: /Running command/ }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: /Command completed/ }).click();
  await expect(page.getByText("SUCCESS", { exact: true })).toBeVisible();
  await emit(page, 6, {
    id: "child",
    agentId: "child",
    parentId: "main",
    kind: "subagent",
    title: "Specialist",
    status: "running",
    details: { role: "verify", assignment: "Review build diagnostics" },
  });
  await emit(page, 7, {
    id: "read",
    agentId: "child",
    parentId: "main",
    kind: "file",
    title: "Read file",
    status: "completed",
    durationMs: 12,
    details: { path: "platformio.ini" },
  });
  await page.screenshot({ path: "test-results/agent-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({ path: "test-results/agent-narrow.png" });
});

test("chat streams Markdown without snapping readers back and closes old subscriptions", async ({
  page,
}) => {
  await workspace(page);
  const session = {
    session_id: "chat",
    kind: "chat",
    project_id: null,
    title: "Sensor explanation",
    status: "running",
    messages: [
      {
        message_id: "answer",
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
        metadata: { status: "streaming", model_id: "selected-model" },
      },
    ],
  };
  await page.route("**/v1/chat/sessions**", async (route) => {
    const url = new URL(route.request().url());
    if (
      url.pathname.endsWith("/sessions") &&
      route.request().method() === "POST"
    )
      return route.fulfill({ json: { session } });
    if (url.pathname.endsWith("/sessions"))
      return route.fulfill({ json: { sessions: [session] } });
    if (url.pathname.endsWith("/sessions/chat"))
      return route.fulfill({ json: { session } });
    return route.fallback();
  });
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await expect(page.getByRole("button", { name: /Thinking/ })).toBeVisible();
  const packet = async (sequence: number, content: string, status: string) => {
    session.messages[0].content = content;
    session.messages[0].metadata.status = status;
    await page.evaluate(
      (event) => {
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
          if (!source.closed && source.url.includes("/sessions/chat/events"))
            source.onmessage?.({ data: JSON.stringify(event) });
      },
      {
        session_id: "chat",
        sequence,
        message_id: "answer",
        content,
        message_status: status,
        event_type:
          status === "streaming" ? "message.delta" : "message.completed",
      },
    );
  };
  const content =
    "## Sensor readings\n\n" +
    "A thermistor changes resistance with temperature.\n\n".repeat(80);
  await packet(1, content, "streaming");
  await expect(
    page.getByRole("heading", { name: "Sensor readings" }),
  ).toHaveCount(1);
  const scroller = page
    .locator(".fx-conversation-scroll")
    .filter({ visible: true });
  await scroller.evaluate((el) => {
    el.scrollTop = 0;
    el.dispatchEvent(new Event("scroll"));
  });
  await packet(2, content + "**Finished.**", "completed");
  await expect(page.getByRole("button", { name: "Stop response" })).toHaveCount(
    0,
  );
  expect(await scroller.evaluate((el) => el.scrollTop)).toBe(0);
  await page.getByRole("button", { name: "Live" }).click();
  await expect(page.getByText("Finished.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  expect(
    await page.evaluate(
      () =>
        (
          window as unknown as {
            testStreams: Array<{ url: string; closed: boolean }>;
          }
        ).testStreams.filter(
          (s) => s.url.includes("/sessions/chat/events") && !s.closed,
        ).length,
    ),
  ).toBe(1);
});

test("chat shows a durable completion when the live stream misses it", async ({
  page,
}) => {
  await workspace(page);
  const session = {
    session_id: "quiet-chat",
    kind: "chat",
    project_id: null,
    title: "Recovered response",
    status: "running",
    messages: [
      {
        message_id: "quiet-answer",
        role: "assistant",
        content: "",
        created_at: new Date().toISOString(),
        metadata: { status: "streaming", model_id: "selected-model" },
      },
    ],
  };
  await page.route("**/v1/chat/sessions**", async (route) => {
    const url = new URL(route.request().url());
    if (
      url.pathname.endsWith("/sessions") &&
      route.request().method() === "POST"
    )
      return route.fulfill({ json: { session } });
    if (url.pathname.endsWith("/sessions"))
      return route.fulfill({ json: { sessions: [session] } });
    if (url.pathname.endsWith("/sessions/quiet-chat"))
      return route.fulfill({ json: { session } });
    return route.fallback();
  });

  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await expect(page.getByRole("button", { name: /Thinking/ })).toBeVisible();
  session.status = "idle";
  session.messages[0].content = "Recovered without reloading.";
  session.messages[0].metadata.status = "completed";

  await expect(page.getByText("Recovered without reloading.")).toBeVisible({
    timeout: 3000,
  });
  await expect(page.getByRole("button", { name: /Thinking/ })).toHaveCount(0);
});

test("editor fills its canvas and decorative borders stay invisible across contrast modes", async ({
  page,
}) => {
  // Serve the installed editor assets so this layout regression test needs no CDN.
  await page.route("**/min/vs/**", async (route) => {
    const relative = new URL(route.request().url()).pathname.split(
      "/min/vs/",
    )[1];
    await route.fulfill({
      path: join(__dirname, "../node_modules/monaco-editor/min/vs", relative),
    });
  });
  await workspace(page);
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  await page.getByRole("button", { name: "Files", exact: true }).click();
  await page.getByText("main.cpp", { exact: true }).first().click();
  const editor = page.locator(".monaco-editor").first();
  await expect(editor).toBeVisible({ timeout: 30000 });
  expect((await editor.boundingBox())!.height).toBeGreaterThan(400);
  await expect(
    editor.getByText("// Sensor firmware", { exact: true }),
  ).toBeVisible();
  for (const contrast of ["standard", "soft", "high"]) {
    const styles = await page.evaluate((value) => {
      document.documentElement.dataset.contrast = value;
      const tabs = document.querySelector(".fx-editor-tabs")!;
      const header = document.querySelector(".fx-product-header")!;
      return [
        getComputedStyle(tabs).borderBottomColor,
        getComputedStyle(header).borderBottomColor,
        getComputedStyle(header).color,
      ];
    }, contrast);
    expect(styles.slice(0, 2)).toEqual([
      "rgba(0, 0, 0, 0)",
      "rgba(0, 0, 0, 0)",
    ]);
    expect(styles[2]).not.toBe("rgba(0, 0, 0, 0)");
  }
  await page.screenshot({ path: "test-results/editor-borderless.png" });
});
