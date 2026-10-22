import { workspace, emit } from "./fixtures/workspace";
import { expect, test } from "@playwright/test";

test("reopening the selected project preserves an enabled prompt composer", async ({page}) => {
  await workspace(page, {status: "completed"}, undefined, true);
  const input = page.getByRole("textbox", {name: "Message", exact: true});
  await expect(input).toBeEnabled();
  await page.getByRole("button", {name: "Projects", exact: true}).click();
  await page.getByText("Temperature monitor", {exact: true}).filter({visible: true}).click();
  await expect(input).toBeEnabled();
  await input.fill("Build the selected project.");
  await expect(page.getByRole("button", {name: "Send message", exact: true})).toBeEnabled();
});

test("oversize instructions keep the draft and longer valid instructions can be sent", async ({page}) => {
  await workspace(page, {status:"completed"}, undefined, true);
  await expect.poll(() => page.evaluate(() => localStorage.getItem("forgex-agent-active-session:p"))).toBe("s");
  const input = page.getByRole("textbox", {name:"Message", exact:true});
  const send = page.getByRole("button", {name:"Send message", exact:true});
  const oversized = "x".repeat(65537);
  let submissions = 0;
  let sent = "";
  await page.route("**/api/forgex/agent-runtime/sessions/s/messages", async route => {
    submissions++;
    sent = route.request().postDataJSON().content;
    await route.fulfill({json:{session:{session_id:"s",kind:"agent",project_id:"p",messages:[],run_ids:[]}}});
  });
  await input.fill(oversized);
  await expect(input).toHaveAttribute("aria-invalid", "true");
  const validation = page.locator('.fx-conversation-composer [role="alert"]');
  await expect(validation).toContainText("65,536-character limit by 1");
  await expect(send).toBeDisabled();
  await input.press("Enter");
  expect(submissions).toBe(0);
  expect(await page.evaluate(() => localStorage.getItem("forgex-agent-draft:s") === "x".repeat(65537))).toBe(true);
  await page.reload();
  await expect.poll(async () => await input.inputValue() === oversized).toBe(true);
  const valid = "Verify firmware. " + "x".repeat(25000);
  await input.fill(valid);
  await expect(validation).toHaveCount(0);
  await expect(send).toBeEnabled();
  await send.click();
  await expect.poll(() => sent).toBe(valid);
  await expect(input).toHaveValue("");
  expect(submissions).toBe(1);
});

test("server validation errors show the rejected field and preserve the draft", async ({page}) => {
  await workspace(page, {status:"completed"}, undefined, true);
  const input = page.getByRole("textbox", {name:"Message", exact:true});
  await page.route("**/api/forgex/agent-runtime/sessions/s/messages", route => route.fulfill({status:422, json:{
    code:"VALIDATION_ERROR", message:"Request validation failed", details:{errors:[
      {location:["body","content"],message:"String should have at most 65536 characters",type:"string_too_long"}]}}}));
  await input.fill("Keep this instruction after a validation failure.");
  await page.getByRole("button", {name:"Send message",exact:true}).click();
  await expect(page.getByText("content: String should have at most 65536 characters", {exact:true})).toBeVisible();
  await expect(input).toHaveValue("Keep this instruction after a validation failure.");
});

test("saved conversation finishes restoring before the composer accepts a new draft", async ({page}) => {
  await workspace(page, {status:"completed"}, undefined, true);
  await expect.poll(() => page.evaluate(() => localStorage.getItem("forgex-agent-active-session:p"))).toBe("s");
  let release!: () => void;
  const held = new Promise<void>(resolve => {release = resolve;});
  await page.route("**/api/forgex/agent-runtime/sessions/s", async route => {
    await held;
    await route.fallback();
  });
  await page.reload();
  const composer = page.getByPlaceholder("Describe what you want to build or change…");
  await expect(composer).toBeDisabled();
  release();
  await expect(composer).toBeEnabled();
  await composer.fill("Keep the draft after restoration.");
  await expect(page.getByRole("button", {name:"Send message", exact:true})).toBeEnabled();
  await expect(composer).toHaveValue("Keep the draft after restoration.");
});

for (const knownMissing of [true, false]) {
  test(`missing folder stops refresh and dependent requests (${knownMissing ? "listed" : "404"})`, async ({page}) => {
    const requests: string[] = [];
    page.on("request", request => requests.push(new URL(request.url()).pathname));
    await workspace(page, {}, undefined, false, {project: {external: true, metadata: {missing: knownMissing}}, filesMissing: true});
    await expect(page.getByRole("button", {name: "Locate folder", exact: true})).toBeVisible();
    const before = requests.length;
    await page.evaluate(() => {
      for (let count = 0; count < 6; count++) {
        window.dispatchEvent(new Event("focus"));
        window.dispatchEvent(new Event("forgex:workspace-files-changed"));
        document.dispatchEvent(new Event("visibilitychange"));
      }
    });
    await page.waitForTimeout(600);
    const projectRequests = requests.filter(path => path.includes("/projects/p") || path.endsWith("/files"));
    expect(projectRequests).toEqual(knownMissing ? [] : ["/api/forgex/projects/p/files"]);
    expect(requests.slice(before).filter(path => path.includes("/projects/p") || path.includes("/agent-runtime/sessions"))).toEqual([]);
    let removed = false;
    await page.route("**/api/forgex/projects/p/recent", async route => {
      expect(route.request().method()).toBe("DELETE"); removed = true;
      await route.fulfill({json: {project_id: "p", deleted: true}});
    });
    await page.route("**/api/forgex/projects", route => route.fulfill({json: {projects: []}}));
    await page.getByRole("button", {name: "Remove from recents", exact: true}).click();
    await expect(page.getByRole("button", {name: "Locate folder", exact: true})).toHaveCount(0);
    expect(removed).toBe(true);
  });
}

test("stale session clears remembered selection and closes streams", async ({page}) => {
  let reads = 0;
  page.on("request", request => { if (new URL(request.url()).pathname === "/api/forgex/agent-runtime/sessions/s") reads++; });
  await page.addInitScript(() => localStorage.setItem("forgex-agent-active-session:p", "s"));
  await workspace(page, {}, undefined, false, {sessionMissing: true});
  await expect.poll(() => page.evaluate(() => localStorage.getItem("forgex-agent-active-session:p"))).toBe(null);
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await page.waitForTimeout(400);
  expect(reads).toBe(1);
  expect(await page.evaluate(() => (window as unknown as {testStreams: Array<{url: string; closed: boolean}>}).testStreams
    .filter(stream => !stream.closed && stream.url.includes("/agent-runtime/")).length)).toBe(0);
});

test("provider retries preserve the selected model and show recovery status", async ({ page }) => {
  const job = {job_id: "j", project_id: "p", current_run_id: "r", status: "running", paused_by: null,
    blocking_reason: null, recovery_policy: "safe_auto", updated_at: new Date().toISOString(), milestones: [],
    recovery: {phase: "retrying", attempt: 1, max_attempts: 4, error_code: "PROVIDER_UNAVAILABLE", http_status: 503, retry_due_at: new Date(Date.now()+5000).toISOString()},
    budget: {remaining: {tool_calls_used: 90, requests: 115, elapsed_seconds: 850, output_tokens: 220000}, consumed: {}, limits: {tool_calls_used: 100, requests: 120, elapsed_seconds: 900, output_tokens: 245760}}};
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await workspace(page, {job_id: "j", job, provider_id: "nvidia_nim", model_id: "selected-model"});
  await page.getByRole("button", {name: "Agent", exact: true}).click();
  await expect(page.getByText(/The model service is temporarily unavailable/)).toBeVisible();
  await expect(page.getByText(/Request attempt 1 of 4/)).toBeVisible();
  await page.screenshot({path: "test-results/durability-retrying.png"});
  expect(errors).toEqual([]);
});

test("local model queue pauses show their real cause and keep Stop available", async ({page}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await workspace(page, {status:"paused_model_unavailable",classification:"PROVIDER_BUSY",provider_id:"nvidia_nim",
    provider_diagnostics:{last_failure_code:"PROVIDER_BUSY",request_reached_provider:false,physical_request_count:0}});
  await page.getByRole("button", {name:"Agent",exact:true}).click();
  await expect(page.getByText("Waiting for another task", {exact:true})).toBeVisible();
  await expect(page.getByText(/This task paused locally before sending a provider request/)).toBeVisible();
  await expect(page.getByRole("button", {name:"Stop task",exact:true})).toHaveCount(2);
  await expect(page.getByText(/selected model is temporarily unavailable/)).toHaveCount(0);
  await page.screenshot({path:"test-results/local-model-queue.png"});
  expect(errors).toEqual([]);
});

for (const status of ["queued", "running", "paused_model_unavailable"] as const) {
  test(`Stop task cancels a ${status} task once and closes its run stream`, async ({page}) => {
    const created = new Date(Date.now() - 300_000).toISOString();
    const events = status === "running" ? [{run_id:"r",sequence:1,event_type:"runtime.started",activity_record:{
      id:"root",runId:"r",kind:"run",status:"running",title:"ForgeX is working",
      startedAt:Date.parse(created),durationMs:null,details:{},
    }}] : [];
    await workspace(page, {status, created_at:created, events, provider_id:"nvidia_nim"});
    await page.getByRole("button", {name:"Agent",exact:true}).click();
    const headerStop = page.locator('[aria-label="Agent activity"]').getByRole("button", {name:"Stop task",exact:true});
    const composerStop = page.locator(".fx-composer-controls").getByRole("button", {name:"Stop task",exact:true});
    await expect(headerStop).toBeVisible();
    await expect(composerStop).toBeVisible();
    if (status === "running") {
      await expect.poll(() => page.evaluate(() =>
        (window as unknown as {testStreams:Array<{url:string;closed:boolean}>}).testStreams
          .some(stream => !stream.closed && stream.url.includes("/runs/r/events")),
      )).toBe(true);
      await emit(page, 1, {id:"root",kind:"run",status:"running",title:"ForgeX is working",startedAt:Date.parse(created),eventType:"runtime.started"});
      await emit(page, 2, {id:"retry",kind:"thinking",status:"running",title:"Provider overloaded; retrying",eventType:"model.retry.scheduled"});
      await page.setViewportSize({width:794,height:720});
      await page.screenshot({path:"test-results/stop-task-provider-wait.png"});
    }
    let requests = 0;
    let release!: () => void;
    const gate = new Promise<void>(resolve => { release = resolve; });
    await page.route("**/api/forgex/agent-runtime/sessions/s/cancel", async route => {
      requests++;
      expect(route.request().method()).toBe("POST");
      await gate;
      await route.fulfill({json:{session:{session_id:"s",project_id:"p",kind:"agent",active_run_id:"r",run_ids:["r"],messages:[]},
        run:{run_id:"r",session_id:"s",project_id:"p",status:"cancelled",updated_at:new Date().toISOString(),events,plan:[],changed_files:[]},activity_events:[]}});
    });
    await headerStop.evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
    await expect.poll(() => requests).toBe(1);
    await expect(headerStop).toBeDisabled();
    await expect(composerStop).toBeDisabled();
    await expect(composerStop).toHaveText("Stopping…");
    release();
    await expect(page.getByRole("button", {name:"Stop task",exact:true})).toHaveCount(0);
    await expect.poll(() => page.evaluate(() =>
      (window as unknown as {testStreams:Array<{url:string;closed:boolean}>}).testStreams
        .filter(stream => !stream.closed && stream.url.includes("/runs/r/events")).length,
    )).toBe(0);
    expect(requests).toBe(1);
  });
}

test("a paused model run does not keep the working spinner from an old activity", async ({page}) => {
  const now = Date.now();
  await workspace(page);
  await page.getByRole("button", {name:"Agent",exact:true}).click();
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as {testStreams:Array<{url:string;closed:boolean}>}).testStreams
      .some(stream => !stream.closed && stream.url.includes("/runs/r/events")),
  )).toBe(true);
  await emit(page, 1, {id:"root",kind:"run",status:"running",title:"ForgeX is working",startedAt:now-300_000,eventType:"runtime.started"});
  await expect(page.getByText("ForgeX is working", {exact:true})).toBeVisible();
  await page.route("**/api/forgex/agent-runtime/runs/r", route => route.fulfill({json:{run:{
    run_id:"r",project_id:"p",status:"paused_model_unavailable",updated_at:new Date(now).toISOString(),events:[],plan:[],changed_files:[],
  }}}));
  // The run projection can settle before its terminal activity frame arrives.
  await emit(page, 2, {id:"root",kind:"run",status:"running",title:"ForgeX is working",startedAt:now-300_000,eventType:"model.retry.scheduled"});
  await expect(page.getByText("Task paused", {exact:true})).toBeVisible();
  await expect(page.getByText("ForgeX is working", {exact:true})).toHaveCount(0);
  await expect(page.locator('.fx-run-status [aria-label="running"]')).toHaveCount(0);
  await expect(page.getByRole("button", {name:"Stop task",exact:true})).toHaveCount(2);
});

test("a failed stop request leaves Stop available to retry", async ({page}) => {
  await workspace(page);
  await page.getByRole("button", {name:"Agent",exact:true}).click();
  let requests = 0;
  await page.route("**/api/forgex/agent-runtime/sessions/s/cancel", async route => {
    requests++;
    await route.fulfill({status:503,json:{code:"CANCEL_FAILED",message:"Task could not be stopped. Try again."}});
  });
  const stop = page.locator(".fx-composer-controls").getByRole("button", {name:"Stop task",exact:true});
  await stop.click();
  await expect(page.getByRole("alert").filter({hasText:"Task could not be stopped"})).toBeVisible();
  await expect(stop).toBeEnabled();
  await stop.click();
  await expect.poll(() => requests).toBe(2);
});

test("overlapping graph events share one in-flight refresh", async ({page}) => {
  await workspace(page);
  const firstGraph = page.waitForResponse(response => response.url().endsWith("/runs/r/graph"));
  await page.getByRole("button", {name: "Agent", exact: true}).click();
  await firstGraph;
  let count = 0;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/agent-runtime/runs/r/graph", async route => {
    count++;
    await gate;
    await route.fulfill({json: {graph: {run_id: "r", nodes: [], edges: []}}});
  });
  for (let i=1; i<=8; i++) await emit(page, i, {eventType: "node.started", node_id: "implementation"});
  await expect.poll(() => count).toBe(1);
  release();
});

test("replayed terminal and inbox events do not flood session reads", async ({page}) => {
  await workspace(page);
  await page.getByRole("button", {name: "Agent", exact: true}).click();
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as {testStreams: Array<{url: string; closed: boolean}>}).testStreams
      .some(source => !source.closed && source.url.includes("/runs/r/events")),
  )).toBe(true);
  await page.route("**/agent-runtime/runs/r", route => route.fulfill({json: {run: {
    run_id: "r", session_id: "s", project_id: "p", status: "failed", events: [], plan: [], changed_files: [],
  }}}));

  const counts = {run: 0, queue: 0, session: 0, sessions: 0};
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/runs/r")) counts.run++;
    else if (path.endsWith("/runs/r/queue")) counts.queue++;
    else if (path.endsWith("/sessions/s")) counts.session++;
    else if (path.endsWith("/sessions")) counts.sessions++;
  });
  await page.evaluate(() => {
    const streams = (window as unknown as {
      testStreams: Array<{url: string; closed: boolean; onmessage?: (message: {data: string}) => void}>
    }).testStreams;
    const source = streams.find(item => !item.closed && item.url.includes("/runs/r/events"));
    for (let sequence = 1; sequence <= 20; sequence++) {
      source?.onmessage?.({data: JSON.stringify({session_id: "s", run_id: "r", sequence,
        event_type: "agent.inbox.queued"})});
    }
    for (let sequence = 21; sequence <= 40; sequence++) {
      source?.onmessage?.({data: JSON.stringify({session_id: "s", run_id: "r", sequence,
        event_type: "runtime.failed", status: "failed"})});
    }
  });
  await expect.poll(() => counts.session).toBe(1);
  expect(counts.run).toBe(1);
  expect(counts.queue).toBeLessThanOrEqual(2);
  expect(counts.session).toBe(1);
  expect(counts.sessions).toBeLessThanOrEqual(1);
});

test("session recovery follows its replacement run without submitting another task", async ({page}) => {
  await workspace(page);
  await page.getByRole("button", {name: "Agent", exact: true}).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as {testStreams:Array<{url:string;closed:boolean}>}).testStreams.some(source => !source.closed && source.url.includes("/runs/r/events")))).toBe(true);
  let submissions = 0;
  page.on("request", request => {if(request.method() === "POST" && request.url().includes("/messages")) submissions++;});
  await page.route("**/agent-runtime/runs/r2", route => route.fulfill({json: {run: {run_id: "r2", project_id: "p", status: "running", events: [], plan: [], changed_files: []}}}));
  await page.evaluate(() => {
    const streams = (window as unknown as {testStreams: Array<{url:string;closed:boolean;onmessage?:(message:{data:string})=>void}>}).testStreams;
    for(const source of streams) if(!source.closed && source.url.includes("/sessions/s/events"))
      source.onmessage?.({data: JSON.stringify({session_id: "s", run_id: "r2", source_run_id: "r", sequence: 900, event_type: "runtime.resumed"})});
  });
  await expect.poll(() => page.evaluate(() => (window as unknown as {testStreams:Array<{url:string;closed:boolean}>}).testStreams.some(source => !source.closed && source.url.includes("/runs/r2/events")))).toBe(true);
  expect(submissions).toBe(0);
});

test("reopening a settled agent run hydrates once without a run stream", async ({page}) => {
  const counts = {run: 0, session: 0, sessions: 0, graph: 0, queue: 0, mutations: 0};
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/agent-runtime/runs/r") && request.method() === "GET") counts.run++;
    if (path.endsWith("/agent-runtime/sessions/s") && request.method() === "GET") counts.session++;
    if (path.endsWith("/agent-runtime/sessions") && request.method() === "GET") counts.sessions++;
    if (path.endsWith("/agent-runtime/runs/r/graph") && request.method() === "GET") counts.graph++;
    if (path.endsWith("/agent-runtime/runs/r/queue") && request.method() === "GET") counts.queue++;
    if (path.includes("/agent-runtime/") && request.method() !== "GET") counts.mutations++;
  });
  await workspace(page, {status: "failed", classification: "BUILD_REPAIR_EXHAUSTED"});
  await page.getByRole("button", {name: "Agent", exact: true}).click();
  await expect.poll(() => counts.run).toBe(1);
  await page.waitForTimeout(400);
  expect(counts.run).toBe(1);
  expect(counts.session).toBe(1);
  expect(counts.sessions).toBe(1);
  expect(counts.graph).toBeLessThanOrEqual(1);
  expect(counts.queue).toBeLessThanOrEqual(1);
  expect(counts.mutations).toBe(0);
  expect(await page.evaluate(() => (window as unknown as {testStreams: Array<{url: string; closed: boolean}>}).testStreams
    .filter(source => !source.closed && source.url.includes("/runs/r/events")).length)).toBe(0);
});


test("new workspaces open Agent and Review sends the selected build environment", async ({page}) => {
  await workspace(page, {status:"completed"}, undefined, true);
  await expect(page.getByRole("combobox", {name:"Build environment"})).toBeVisible();
  const mode = page.getByRole("combobox", {name:"Agent mode"});
  await expect(mode).toHaveValue("auto");
  await mode.selectOption("review_changes");
  await page.getByRole("combobox", {name:"Build environment"}).selectOption("release");
  let submission: Record<string, unknown> | null = null;
  await page.route("**/api/forgex/agent-runtime/sessions/s/messages", async route => {
    submission = route.request().postDataJSON();
    await route.fulfill({json:{session:{session_id:"s",kind:"agent",project_id:"p",messages:[],run_ids:[]}}});
  });
  await page.getByRole("textbox", {name:"Message",exact:true}).fill("Move the LED to GPIO4 and verify compilation.");
  await page.getByRole("button", {name:"Send message",exact:true}).click();
  await expect.poll(() => submission).toMatchObject({autonomy:"review_changes",environment:"release"});
});

test("Evidence separates source references, compilation and unqualified device behavior", async ({page}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await workspace(page, {status:"completed",build_verified:true,requirement_report:{schema_version:2,
    references_valid:true,valid:true,verification_level:"source_reference",checks:[]}}, undefined, true);
  await page.getByRole("button", {name:"Open workspace",exact:true}).click();
  await page.getByRole("tab", {name:"Evidence",exact:true}).click();
  const levels = page.getByLabel("Verification levels").last();
  await expect(levels.locator("div").filter({hasText:"Requirement references"})).toContainText("Referenced");
  await expect(levels.locator("div").filter({hasText:"Requirement fidelity"})).toContainText("Not started");
  await expect(levels.locator("div").filter({hasText:"Firmware compilation"})).toContainText("Built");
  await expect(levels.locator("div").filter({hasText:"Device behavior"})).toContainText("Not tested");
  await page.screenshot({path:"test-results/verification-evidence.png"});
  expect(errors).toEqual([]);
});


test("saved work requires an explicit resume and sends its checkpoint identity", async ({ page }) => {
  const checkpoint = "a".repeat(64);
  await workspace(page, {
    status: "failed", classification: "AGENT_RUNTIME_INTERRUPTED", recoverable: true,
    recovery_actions: ["retry"],
    recovery: { eligible: true, checkpoint_id: checkpoint, blocking_reason: null, uncertain_operations: [] },
  });
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(page.getByText("Saved work is ready to resume")).toBeVisible();
  const button = page.getByRole("button", { name: "Resume saved work", exact: true });
  await expect(button).toBeEnabled();
  let received: Record<string, unknown> | null = null;
  await page.route("**/api/forgex/agent-runtime/runs/r/resume", async route => {
    received = route.request().postDataJSON();
    await route.fulfill({ json: { run: { run_id: "continuation", session_id: "s", project_id: "p", status: "running", retry_of_run_id: "r", events: [], plan: [], changed_files: [] } } });
  });
  await button.click();
  await expect.poll(() => received).toEqual({ checkpoint_id: checkpoint, idempotency_key: `resume:r:${checkpoint.slice(0, 24)}` });
});

test("retry same model resumes a paused run and double clicks submit once", async ({page}) => {
  const checkpoint = "b".repeat(64);
  await workspace(page, {status:"paused_model_unavailable", provider_id:"nvidia_nim", model_id:"550b",
    recoverable:true, recovery_actions:["resume"], recovery:{eligible:true,checkpoint_id:checkpoint,blocking_reason:null,uncertain_operations:[]}});
  let requests = 0;
  let body: Record<string, unknown> | null = null;
  await page.route("**/api/forgex/agent-runtime/runs/r/resume", async route => {
    requests++;
    body = route.request().postDataJSON();
    await new Promise(resolve => setTimeout(resolve, 200));
    await route.fulfill({json:{run:{run_id:"continued",session_id:"s",project_id:"p",status:"running",events:[],plan:[],changed_files:[]}}});
  });
  await page.getByRole("button", {name:"Agent",exact:true}).click();
  const retry = page.getByRole("button", {name:"Retry same model",exact:true});
  await expect(retry).toBeEnabled();
  await retry.evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
  await expect.poll(() => requests).toBe(1);
  await expect.poll(() => body).toEqual({checkpoint_id:checkpoint,idempotency_key:`retry:r:${checkpoint.slice(0,24)}`});
});

test("cancelled files open their saved diff and discard requires its own action", async ({page}) => {
  await workspace(page, {status:"cancelled", classification:"AGENT_RUNTIME_CANCELLED", change_set_id:"draft",
    created_file_count:1, created_files:["partitions.csv"], recoverable:true, recovery_actions:["resume"],
    recovery:{eligible:true,checkpoint_id:"f".repeat(64),blocking_reason:null,uncertain_operations:[]}});
  const draft = {change_set_id:"draft",status:"draft",changed_files:[{path:"partitions.csv",change_type:"created",safe:true,
    diff_preview:"+ nvs,data,nvs,,0x5000"}]};
  await page.route("**/api/forgex/agent-runtime/runs/r/changes", route => route.fulfill({json:{change_set:draft}}));
  let discarded = 0;
  let activeFileReads = 0;
  page.on("request", request => {if (request.url().includes("/files/content")) activeFileReads++;});
  await page.route("**/api/forgex/agent-runtime/runs/r/discard-draft", async route => {
    discarded++;
    await new Promise(resolve => setTimeout(resolve, 200));
    await route.fulfill({json:{run:{run_id:"r",status:"cancelled",change_set_id:"draft",recoverable:false}}});
  });
  await page.route("**/api/forgex/agent-runtime/runs/r", route => route.fulfill({json:{run:{run_id:"r",session_id:"s",
    project_id:"p",status:"cancelled",change_set_id:"draft",recoverable:discarded===0,created_file_count:1,
    created_at:new Date().toISOString(),updated_at:new Date().toISOString(),
    recovery:{eligible:discarded===0,checkpoint_id:"f".repeat(64),blocking_reason:null,uncertain_operations:[]},
    created_files:["partitions.csv"],events:[],plan:[],changed_files:[]}}}));
  await page.getByRole("button", {name:"Agent",exact:true}).click();
  await page.getByRole("button", {name:"Review saved work",exact:true}).click();
  await expect(page.getByText("+ nvs,data,nvs,,0x5000", {exact:true})).toBeVisible();
  await expect(page.getByText("Recovery draft. Resume and build these files before approval.")).toBeVisible();
  expect(discarded).toBe(0);
  expect(activeFileReads).toBe(0);
  await page.getByRole("button", {name:"Discard draft",exact:true}).evaluate(button => {
    (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click();
  });
  await expect.poll(() => discarded).toBe(1);
  await expect(page.getByRole("button", {name:"Discard draft",exact:true})).toHaveCount(0);
});

test("selected model continuation sends the explicit choice with its checkpoint", async ({page}) => {
  const checkpoint = "c".repeat(64);
  await workspace(page, {status:"paused_model_unavailable",provider_id:"nvidia_nim",model_id:"550b",recovery_actions:["resume"],
    recovery:{eligible:true,checkpoint_id:checkpoint,blocking_reason:null,uncertain_operations:[]}}, {provider_id:"ollama",model_id:"local-model"});
  let body: Record<string, unknown> | null = null;
  await page.route("**/api/forgex/agent-runtime/runs/r/resume", async route => {
    body = route.request().postDataJSON();
    await route.fulfill({json:{run:{run_id:"continued",session_id:"s",project_id:"p",status:"running",events:[],plan:[],changed_files:[]}}});
  });
  await page.getByRole("button", {name:"Agent",exact:true}).click();
  await page.getByRole("button", {name:"Continue with selected model",exact:true}).click();
  await expect.poll(() => body).toEqual({checkpoint_id:checkpoint,idempotency_key:expect.stringMatching(/^resume:r:c{24}:[a-f0-9]{16}$/),provider_id:"ollama",model_id:"local-model"});
});

test("saved job model switch preserves its checkpoint and budget", async ({page}) => {
  const checkpoint = "9".repeat(64);
  const job = {job_id:"j",project_id:"p",current_run_id:"r",status:"paused_model_unavailable",paused_by:null,
    blocking_reason:"AGENT_OUTAGE_WINDOW_EXHAUSTED",recovery_policy:"safe_auto",updated_at:new Date().toISOString(),milestones:[],
    durable_wait_seconds:180,recovery:{phase:"paused_model_unavailable",attempt:2,max_attempts:3,http_status:503,
      outage_deadline_at:new Date().toISOString()},
    budget:{remaining:{tool_calls_used:90,requests:113,elapsed_seconds:830},consumed:{tool_calls_used:10,requests:7,elapsed_seconds:70},
      limits:{tool_calls_used:100,requests:120,elapsed_seconds:900}}};
  await workspace(page,{job_id:"j",job,status:"paused_model_unavailable",provider_id:"nvidia_nim",model_id:"550b",
    recovery:{eligible:true,checkpoint_id:checkpoint,blocking_reason:null,uncertain_operations:[]}},
    {provider_id:"ollama",model_id:"local-model"});
  let body: Record<string,unknown> | null = null;
  let requests = 0;
  const keys: unknown[] = [];
  await page.route("**/api/forgex/agent-runtime/jobs/j/resume",async route => {
    requests++;
    body = route.request().postDataJSON();
    keys.push(body?.idempotency_key);
    if (requests === 1) {
      await route.fulfill({status:503,json:{code:"RECOVERY_TEMPORARILY_BLOCKED",message:"Recovery temporarily blocked."}});
      return;
    }
    await route.fulfill({json:{run:{run_id:"continued",job_id:"j",job:{...job,current_run_id:"continued",status:"running"},
      session_id:"s",project_id:"p",status:"running",events:[],plan:[],changed_files:[]}}});
  });
  await page.getByRole("button",{name:"Agent",exact:true}).click();
  await expect(page.getByText(/Automatic recovery cycles used: 2 of 3/)).toBeVisible();
  await expect(page.getByText(/180 seconds spent in durable outage cooldowns/)).toBeVisible();
  await expect(page.getByRole("button",{name:"Retry selected model",exact:true})).toHaveCount(1);
  await page.getByRole("button",{name:"Retry selected model",exact:true}).click();
  await expect.poll(() => requests).toBe(1);
  await expect(page.getByRole("button",{name:"Continue with ollama / local-model",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"Continue with ollama / local-model",exact:true}).click();
  await expect.poll(() => body).toEqual({checkpoint_id:checkpoint,idempotency_key:expect.stringMatching(/^resume:r:9{24}:[a-f0-9]{16}$/),
    provider_id:"ollama",model_id:"local-model"});
  expect(requests).toBe(2);
  expect(keys[0]).not.toBe(keys[1]);
  await expect(page.getByText(/90 tool calls and 113 model API requests remaining/)).toBeVisible();
});

test("saved draft can be verified locally while its model is unavailable", async ({page}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await workspace(page, {status:"paused_model_unavailable",provider_id:"nvidia_nim",model_id:"550b",change_set_id:"draft",
    recovery_actions:["resume"],recovery:{eligible:true,checkpoint_id:"d".repeat(64),blocking_reason:null,uncertain_operations:[]}});
  let verified = 0;
  await page.route("**/api/forgex/agent-runtime/runs/r/verify-draft", async route => {
    verified++;
    await route.fulfill({json:{run:{run_id:"verified",session_id:"s",project_id:"p",status:"completed_with_warning",build_verified:true,
      change_set_id:"draft",recovery_actions:["retry"],events:[],plan:[],changed_files:[]}}});
  });
  await page.route("**/api/forgex/agent-runtime/runs/verified", route => route.fulfill({json:{run:{run_id:"verified",session_id:"s",project_id:"p",
    status:"completed_with_warning",build_verified:true,change_set_id:"draft",recovery_actions:["retry"],events:[],plan:[],changed_files:[]}}}));
  await page.getByRole("button", {name:"Agent",exact:true}).click();
  await page.getByRole("button", {name:"Build saved draft",exact:true}).click();
  await expect.poll(() => verified).toBe(1);
  await expect(page.getByText("Build verified; task completion needs attention")).toBeVisible();
  await expect(page.getByText(/Task requirements and device behavior still need verification/)).toBeVisible();
  await expect(page.locator("[data-nextjs-dialog]")).toHaveCount(0);
  await page.screenshot({path:"test-results/reliability-draft-verified.png"});
  expect(errors).toEqual([]);
});

test("model outage preserves the saved draft build evidence", async ({page}) => {
  await workspace(page, {status:"paused_model_unavailable",provider_id:"nvidia_nim",model_id:"550b",change_set_id:"draft",
    build_verified:true,build_result:{success:true},recovery_actions:["resume"],
    recovery:{eligible:true,checkpoint_id:"e".repeat(64),blocking_reason:null,uncertain_operations:[]}});
  await page.getByRole("button", {name:"Agent",exact:true}).click();
  await expect(page.getByText("Model temporarily unavailable", {exact:true})).toBeVisible();
  await expect(page.getByText("Build verified; task completion needs attention")).toBeVisible();
  await expect(page.getByText(/The saved draft passed a local build/)).toBeVisible();
});

test("saved task exposes its budget, trust, recovery policy and acceptance evidence", async ({page}) => {
  const job={job_id:"job-r",project_id:"p",current_run_id:"r",status:"paused",paused_by:"budget",blocking_reason:"AGENT_JOB_BUDGET_EXHAUSTED",recovery_policy:"manual",updated_at:"2026-09-27T00:00:00Z",milestones:[],budget:{remaining:{tool_calls_used:0,requests:0,elapsed_seconds:0},consumed:{tool_calls_used:100,requests:120,elapsed_seconds:900},limits:{tool_calls_used:100,requests:120,elapsed_seconds:900}}};
  await workspace(page,{status:"failed",job_id:job.job_id,job,requirement_report:{valid:true,acceptance:{passed:false,checks:[{id:"source",kind:"source_review",criterion:"Current source inspected",passed:true},{id:"unit",kind:"unit_tests",criterion:"Logic tests pass",passed:false}]}}});
  await page.route("**/api/forgex/agent-runtime/projects/p/trust",async route=>route.fulfill({json:{trusted:route.request().method()==="POST" ? route.request().postDataJSON().trusted : false}}));
  let body: Record<string,unknown> | null=null;
  await page.route("**/api/forgex/agent-runtime/jobs/job-r/budget",async route=>{
    body=route.request().postDataJSON();
    await route.fulfill({json:{job:{...job,updated_at:"2026-09-27T00:01:00Z",paused_by:null,budget:{...job.budget,remaining:{tool_calls_used:100,requests:120,elapsed_seconds:900}}}}});
  });
  await page.getByRole("button",{name:"Agent",exact:true}).click();
  await expect(page.getByText("Task saved · Standard",{exact:true})).toBeVisible();
  await page.getByText("Verification evidence",{exact:true}).click();
  await page.getByText("Acceptance checks",{exact:true}).click();
  await expect(page.getByText("source review: Observed. Current source inspected")).toBeVisible();
  await expect(page.getByText("unit tests: Unverified. Logic tests pass")).toBeVisible();
  const trust=page.getByRole("checkbox",{name:"I trust this project for local builds and tests."});
  await trust.check();
  await expect(trust).toBeChecked();
  await page.getByRole("button",{name:"Add task budget",exact:true}).click();
  await expect.poll(()=>body).toEqual({idempotency_key:`budget:job-r:${job.updated_at}`});
  await expect(page.getByText("100 tool calls and 120 model API requests remaining.",{exact:false})).toBeVisible();
});

test("output allowance exhaustion prevents resume even when requests remain", async ({ page }) => {
  const job = {job_id:"budget-r", project_id:"p", current_run_id:"r", status:"paused", paused_by:"budget",
    blocking_reason:"AGENT_JOB_BUDGET_EXHAUSTED", recovery_policy:"manual", updated_at:"2026-09-27T00:00:00Z", milestones:[],
    budget:{remaining:{tool_calls_used:53,requests:60,elapsed_seconds:345,output_tokens:0,input_bytes:5_778_644}, consumed:{}, limits:{}}};
  await workspace(page, {status:"failed", classification:"AGENT_PROVIDER_BUDGET_EXCEEDED", job_id:job.job_id, job,
    recovery:{eligible:true, checkpoint_id:"a".repeat(64), blocking_reason:null, uncertain_operations:[]}});
  await page.getByRole("button", {name:"Agent", exact:true}).click();
  await expect(page.getByText("0 output tokens available for model responses.", {exact:false})).toBeVisible();
  await expect(page.getByText("Task paused: model response allowance exhausted.", {exact:false})).toBeVisible();
  await expect(page.getByRole("button", {name:"Retry selected model", exact:true})).toBeDisabled();
  await expect(page.getByRole("button", {name:"Resume saved work", exact:true})).toHaveCount(0);
  await expect(page.getByRole("button", {name:"Add task budget", exact:true})).toBeEnabled();
  await page.screenshot({path:"test-results/shared-budget-controls.png"});
});

test("a missing saved workspace blocks continuation and preserves its checkpoint", async ({page}) => {
  const job = {job_id:"missing-folder-job",project_id:"p",current_run_id:"r",status:"blocked",paused_by:null,
    blocking_reason:"AGENT_RESUME_WORKSPACE_UNAVAILABLE",recovery_policy:"safe_auto",updated_at:new Date().toISOString(),
    retry_due_at:null,milestones:[],
    budget:{remaining:{tool_calls_used:98,requests:113,elapsed_seconds:858},consumed:{tool_calls_used:2,requests:7,elapsed_seconds:42},
      limits:{tool_calls_used:100,requests:120,elapsed_seconds:900}}};
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await workspace(page,{status:"failed",classification:"AGENT_RUNTIME_INTERRUPTED",job_id:job.job_id,job,recoverable:true,
    recovery:{eligible:false,checkpoint_id:"a".repeat(64),blocking_reason:"AGENT_RESUME_WORKSPACE_UNAVAILABLE",uncertain_operations:[]}});
  await page.getByRole("button",{name:"Agent",exact:true}).click();
  await expect(page.getByText(/The original project folder is missing or cannot be opened/)).toBeVisible();
  await expect(page.getByText(/Your task history and sealed checkpoint are preserved/)).toBeVisible();
  await expect(page.getByRole("button",{name:"Retry selected model",exact:true})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Inspect saved work",exact:true})).toBeEnabled();
  await expect(page.getByText(/98 tool calls and 113 model API requests remaining/)).toBeVisible();
  await expect(page.getByText(/The model service is temporarily unavailable/)).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("an uncertain hardware outcome offers review and prevents resume", async ({ page }) => {
  await workspace(page, {
    status: "blocked", classification: "AGENT_RECOVERY_OUTCOME_UNKNOWN", recoverable: true,
    recovery_actions: [],
    recovery: { eligible: false, checkpoint_id: "a".repeat(64), blocking_reason: "AGENT_RECOVERY_OUTCOME_UNKNOWN",
      uncertain_operations: [{ operation_id: "flash-1", kind: "hardware.flash", status: "unknown" }] },
  });
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  await expect(page.getByText("Recovery needs attention")).toBeVisible();
  await expect(page.getByRole("button", { name: "Review saved work" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Resume saved work" })).toHaveCount(0);
});

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
    page.getByRole("button", { name: /Running platformio run/ }),
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
  await expect(page.getByRole("button", { name: /Running platformio run/ })).toHaveCount(
    1,
  );
  await expect(
    page.getByRole("button", { name: /Running command/ }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: /Running platformio run/ }).click();
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

test("failed reasoning stops timing when a saved run is reopened", async ({ page }) => {
  const startedAt = Date.now() - 343_000;
  const base = { runId: "r", agentId: "main", parentId: null, details: {} };
  const events = [
    { run_id: "r", sequence: 1, event_type: "thinking.started", activity_record: {
      ...base, id: "thinking", kind: "thinking", title: "Thinking", status: "failed",
      startedAt: startedAt + 1000, durationMs: null,
    } },
    { run_id: "r", sequence: 2, event_type: "runtime.failed", activity_record: {
      ...base, id: "root", kind: "run", title: "Task", status: "failed",
      startedAt, durationMs: 3709,
    } },
  ];
  await workspace(page, { status: "failed", updated_at: new Date(startedAt + 3709).toISOString(), events });
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  // Reopened runs receive persisted events from the session SSE subscription.
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { testStreams: Array<{ url: string; closed: boolean }> })
      .testStreams.some(source => !source.closed && source.url.includes("/sessions/s/events")),
  )).toBe(true);
  await page.evaluate((events) => {
    const streams = (window as unknown as { testStreams: Array<{
      url: string; closed: boolean; onmessage?: (event: { data: string }) => void;
    }> }).testStreams;
    for (const source of streams) {
      if (!source.closed && source.url.includes("/sessions/s/events")) {
        for (const event of events) source.onmessage?.({ data: JSON.stringify({ session_id: "s", ...event }) });
      }
    }
  }, events);
  const row = page.getByRole("button", { name: /Reasoning summary/ });
  await expect(row).toContainText("2.7s");
  // Move the browser clock forward to catch a terminal timer still ticking.
  await page.clock.install();
  await page.clock.fastForward(60_000);
  await expect(row).toContainText("2.7s");
  await page.screenshot({ path: "test-results/provider-failed-timer.png" });
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
  await page.getByRole("button", { name: "Chat", exact: true }).click();
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

test("replayed chat lifecycle events reconcile once without reopening the session", async ({ page }) => {
  await workspace(page);
  const session = {session_id: "replay-chat", kind: "chat", project_id: null,
    title: "Replay session", status: "idle", messages: [{message_id: "answer", role: "assistant",
      content: "Saved answer", created_at: new Date().toISOString(), metadata: {status: "completed"}}]};
  await page.route("**/v1/chat/sessions**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/sessions") && route.request().method() === "POST")
      return route.fulfill({json: {session}});
    if (path.endsWith("/sessions")) return route.fulfill({json: {sessions: [session]}});
    if (path.endsWith("/sessions/replay-chat")) return route.fulfill({json: {session}});
    return route.fallback();
  });
  await page.getByRole("button", {name: "New chat", exact: true}).click();
  await expect(page.getByText("Saved answer", {exact: true})).toBeVisible();
  const counts = {detail: 0, list: 0, mutation: 0};
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (path.endsWith("/v1/chat/sessions/replay-chat") && request.method() === "GET") counts.detail++;
    if (path.endsWith("/v1/chat/sessions") && request.method() === "GET") counts.list++;
    if (path.includes("/v1/chat/sessions") && request.method() !== "GET") counts.mutation++;
  });
  await page.evaluate(() => {
    const streams = (window as unknown as {testStreams: Array<{url: string; closed: boolean;
      onmessage?: (event: {data: string}) => void}>}).testStreams;
    const source = streams.find(item => !item.closed && item.url.includes("/sessions/replay-chat/events"));
    for (let sequence = 1; sequence <= 30; sequence++)
      source?.onmessage?.({data: JSON.stringify({session_id: "replay-chat", sequence,
        message_id: "answer", event_type: "message.completed", message_status: "completed",
        content: "Saved answer"})});
  });
  await expect.poll(() => counts.detail).toBe(1);
  await page.waitForTimeout(350);
  expect(counts.detail).toBe(1);
  expect(counts.list).toBe(1);
  expect(counts.mutation).toBe(0);
});

test("late chat detail response cannot replace a newer selection", async ({page}) => {
  await workspace(page);
  const now = new Date().toISOString();
  const a = {session_id: "chat-a", kind: "chat", project_id: null, title: "Chat A", status: "idle",
    messages: [{message_id: "a", role: "assistant", content: "Answer A", created_at: now, metadata: {status: "completed"}}]};
  const b = {session_id: "chat-b", kind: "chat", project_id: null, title: "Chat B", status: "idle",
    messages: [{message_id: "b", role: "assistant", content: "Answer B", created_at: now, metadata: {status: "completed"}}]};
  let delayA = false;
  let releaseA = () => {};
  let pendingA = false;
  await page.route("**/v1/chat/sessions**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/sessions")) return route.fulfill({json: {sessions: [a, b]}});
    if (path.endsWith("/sessions/chat-a")) {
      if (delayA) await new Promise<void>(resolve => {releaseA = resolve; pendingA = true;});
      return route.fulfill({json: {session: a}});
    }
    if (path.endsWith("/sessions/chat-b")) return route.fulfill({json: {session: b}});
    return route.fallback();
  });
  await page.evaluate(() => localStorage.setItem("forgex-chat-session", "chat-a"));
  await page.reload();
  await expect(page.getByText("Answer A", {exact: true})).toBeVisible();
  await page.getByRole("button", {name: "Chat B", exact: true}).click();
  await expect(page.getByText("Answer B", {exact: true})).toBeVisible();
  delayA = true;
  await page.getByRole("button", {name: "Chat A", exact: true}).click();
  await expect.poll(() => pendingA).toBe(true);
  await page.getByRole("button", {name: "Chat B", exact: true}).click();
  await expect(page.getByText("Answer B", {exact: true})).toBeVisible();
  releaseA();
  await page.waitForTimeout(200);
  await expect(page.getByText("Answer B", {exact: true})).toBeVisible();
  expect(await page.getByText("Answer A", {exact: true}).count()).toBe(0);
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

  await page.getByRole("button", { name: "Chat", exact: true }).click();
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

test("restored agent view can reopen and resize the workspace after a reload", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await workspace(page);
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  await page.getByRole("button", { name: "Open workspace", exact: true }).click();
  await expect(page.getByRole("tablist", { name: "Workspace tools" })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Open workspace", exact: true }).click();
  await expect(page.getByRole("tablist", { name: "Workspace tools" })).toBeVisible();
  const handle = page.locator(".fx-detail-handle");
  await handle.focus();
  await handle.press("ArrowLeft");
  await expect(page.getByRole("tab", { name: "Files", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close workspace", exact: true }).first().click();
  await page.getByRole("button", { name: "Chat", exact: true }).click();
  await expect(page.getByText("A little clarity.")).toBeVisible();
  expect(errors).toEqual([]);
});

test("editor fills its canvas and panel borders respect the contrast preference", async ({
  page,
}) => {
  // The app serves its own editor assets; block external scripts and workers.
  await page.route(/^https?:\/\/(?!127\.0\.0\.1|localhost)/, route => route.abort());
  const editorRequests: string[] = [];
  page.on("request", request => { if (request.url().includes("/vendor/monaco/")) editorRequests.push(request.url()); });
  await workspace(page);
  await page.getByRole("button", { name: "Agent", exact: true }).click();
  await page.getByRole("button", { name: "Open workspace" }).click();
  await page.getByRole("tab", { name: "Files", exact: true }).click();
  await page.getByText("main.cpp", { exact: true }).first().click();
  const editor = page.locator(".monaco-editor").first();
  await expect(editor).toBeVisible({ timeout: 30000 });
  expect((await editor.boundingBox())!.height).toBeGreaterThan(400);
  expect(editorRequests.some(url => url.endsWith("/vs/loader.js"))).toBe(true);
  expect(editorRequests.every(url => new URL(url).hostname === "127.0.0.1" || new URL(url).hostname === "localhost")).toBe(true);
  await expect(
    editor.getByText("// Sensor firmware", { exact: true }),
  ).toBeVisible();
  for (const contrast of ["standard", "soft", "high"]) {
    const styles = await page.evaluate((value) => {
      document.documentElement.dataset.panelBorders = "false";
      document.documentElement.dataset.contrast = value;
      const tabs = document.querySelector(".fx-editor-tabs")!;
      const header = document.querySelector(".fx-product-header")!;
      return [
        getComputedStyle(tabs).borderBottomColor,
        getComputedStyle(header).borderBottomColor,
        getComputedStyle(header).color,
      ];
    }, contrast);
    if (contrast === "high") expect(styles[1]).not.toBe("rgba(0, 0, 0, 0)");
    else expect(styles.slice(0, 2)).toEqual(["rgba(0, 0, 0, 0)", "rgba(0, 0, 0, 0)"]);
    expect(styles[2]).not.toBe("rgba(0, 0, 0, 0)");
  }
  await page.screenshot({ path: "test-results/redesign-editor.png" });
});
