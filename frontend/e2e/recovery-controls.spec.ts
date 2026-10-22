import {expect, test} from "@playwright/test";
import {workspace} from "./fixtures/workspace";

test("a model connection check does not claim task qualification", async ({page}) => {
  await workspace(page);
  const provider = {provider_id:"nvidia_nim",display_name:"NVIDIA",default_model:"fixture-model",
    configured:true,enabled:true,local:false,health_status:"healthy",base_url:"https://fixture.invalid/v1"};
  await page.route("**/api/forgex/models/providers", route => route.fulfill({json:{providers:[provider]}}));
  await page.route("**/api/forgex/models/routes", route => route.fulfill({json:{routes:[{
    task_type:"code_generation",provider_id:"nvidia_nim",model_id:"fixture-model"}]}}));
  await page.route("**/api/forgex/models/providers/nvidia_nim/models", route => route.fulfill({json:{models:[{
    provider_id:"nvidia_nim",model_id:"fixture-model",display_name:"Fixture model",availability:"available",
    qualification:{status:"untested",scopes:[],message:"Task completion has not been qualified for this runtime."}}]}}));
  await page.route("**/api/forgex/models/providers/nvidia_nim/health", route => route.fulfill({json:{health:{ok:true,latency_ms:12}}}));
  await page.reload();
  await page.getByRole("button", {name:"Settings",exact:true}).click();
  await page.locator('[data-settings-category="models"]').click();
  await page.getByRole("button", {name:"Models",exact:true}).click();
  await expect(page.getByLabel("Model task qualification")).toContainText("not been qualified");
  await page.getByRole("button", {name:"Test",exact:true}).click();
  await expect(page.getByText(/connection check passed.*Task completion requires separate qualification/)).toBeVisible();
  await expect(page.getByLabel("Model task qualification")).toContainText("not been qualified");
});

test("Apply executes once and disappears across canvases despite delayed change-set refresh", async ({page}) => {
  const now = new Date().toISOString();
  let applied = false;
  let posts = 0;
  const run = () => ({run_id:"r",session_id:"s",project_id:"p",status:"completed",provider_id:"nvidia_nim",
    classification:applied ? "CHANGESET_APPLIED" : "CHANGESET_REVIEW_REQUIRED",change_set_id:"c",
    created_file_count:1,modified_file_count:0,deleted_file_count:0,active_workspace_unchanged:!applied,
    events:[],plan:[],changed_files:[]});
  await workspace(page,run(),undefined,true);
  await page.route("**/agent-runtime/runs/r",route => route.fulfill({json:{run:run()}}));
  await page.route("**/agent-runtime/runs/r/changes",async route => {
    // A stale pending response must never override the run's applied classification.
    if (applied) await new Promise(resolve => setTimeout(resolve,400));
    await route.fulfill({json:{change_set:{change_set_id:"c",provider_id:"nvidia_nim",workspace_root:"D:/test/monitor",
      status:"pending",created_at:now,summary:"Firmware",changed_files:[{path:"src/main.cpp",change_type:"created",safe:true,diff_preview:"+ void setup() {}"}]}}});
  });
  await page.route("**/agent-runtime/runs/r/apply-changes",async route => {
    posts++;
    await new Promise(resolve => setTimeout(resolve,300));
    applied=true;
    await route.fulfill({json:{run:run()}});
  });
  await page.getByRole("button",{name:"Open workspace",exact:true}).click();
  await page.getByRole("tab",{name:"Files",exact:true}).click();
  const apply=page.getByRole("button",{name:"Apply changes",exact:true});
  await expect(apply).toBeEnabled();
  await apply.evaluate(element => { (element as HTMLButtonElement).click(); (element as HTMLButtonElement).click(); });
  await expect(apply).toHaveCount(0);
  expect(posts).toBe(1);
  await page.getByRole("tab",{name:/^Changes/}).click();
  await expect(page.getByRole("button",{name:"Apply changes",exact:true})).toHaveCount(0);
  await expect(page.getByText(/could not apply/i)).toHaveCount(0);
});

test("evidence shows risk-review attention separately from successful source review", async ({page}) => {
  await workspace(page,{status:"completed",classification:"CHANGESET_REVIEW_REQUIRED",build_verified:true,
    requirement_report:{references_valid:true,valid:true,verification_level:"model_review",checks:[],
      risk_review:{verdict:"block",status:"needs_review",failure_code:"RISK_REVIEW_TRUNCATED"}}},undefined,true);
  await page.getByRole("button",{name:"Open workspace",exact:true}).click();
  await page.getByRole("tab",{name:"Evidence",exact:true}).click();
  const levels=page.getByLabel("Verification levels").last();
  await expect(levels.locator("div").filter({hasText:"Requirement fidelity"})).toContainText("Reviewed");
  await expect(levels.locator("div").filter({hasText:"Change risk review"})).toContainText("Needs review");
});

function savedJob() {
  return {job_id:"j", project_id:"p", current_run_id:"r", status:"paused_model_unavailable", paused_by:null,
    blocking_reason:null as string | null, recovery_policy:"safe_auto", updated_at:new Date().toISOString(), milestones:[],
    recovery:{phase:"paused_model_unavailable",attempt:0,max_attempts:3,error_code:"PROVIDER_TIMEOUT"},
    budget:{remaining:{tool_calls_used:90,requests:110,elapsed_seconds:830,output_tokens:200000,input_bytes:7000000},
      consumed:{tool_calls_used:10,requests:10,elapsed_seconds:70,output_tokens:40000,input_bytes:1000000},
      limits:{tool_calls_used:100,requests:120,elapsed_seconds:900,output_tokens:240000,input_bytes:8000000}}};
}

function recovery(verificationReason: string | null) {
  return {eligible:true, checkpoint_id:"a".repeat(64), blocking_reason:null, uncertain_operations:[],
    actions:{inspect:{allowed:true,blocking_reason:null}, continue_generation:{allowed:true,blocking_reason:null},
      verify_locally:{allowed:verificationReason === null,blocking_reason:verificationReason}}};
}

test("saved repair explains unfinished implementation without claiming completion", async ({page}) => {
  await workspace(page, {job_id:"j",job:{...savedJob(),status:"failed",recovery:undefined},status:"failed",classification:"BUILD_ERROR",
    change_set_id:"draft",build_verified:false,recovery:recovery(null),task_state:{schema_version:1,revisions:[],requirements:[],exclusions:[],decisions:[],
      milestone:null,evidence:[],blockers:["Unresolved implementations"],next_action:"Implement DisplayManager::init() and rebuild.",
      recovery_state:{stalled_turns:0,reorientation_started_at:null},execution:{version:1,phase:"repair",
        active_milestone:{text:"Complete display module"},missing_artifacts:["src/Display.cpp"],blocking_symbols:["DisplayManager::init()"],
        remaining_requirement_ids:["req-1","req-2"],stalled_turns:0,next_action:"Implement DisplayManager::init() and rebuild."}}});
  await page.getByRole("button",{name:"Agent",exact:true}).click();
  const status=page.getByLabel("Task completion status");
  await expect(status).toContainText("Repairing build");
  await expect(status).toContainText("Files still needed: src/Display.cpp");
  await expect(status).toContainText("Build needs these implementations: DisplayManager::init()");
  await expect(status).toContainText("2 requirements still need independent acceptance");
  await expect(status).not.toContainText("complete and verified");
  await expect(page.getByRole("button",{name:"Retry selected model",exact:true})).toBeEnabled();
});

test("verification reserve keeps continuation available and shows remaining budget", async ({page}) => {
  await workspace(page, {job_id:"j", job:{...savedJob(),status:"paused",paused_by:"budget",
    blocking_reason:"AGENT_VERIFICATION_RESERVE_REACHED",recovery:undefined},
    status:"failed",classification:"AGENT_VERIFICATION_RESERVE_REACHED",change_set_id:"draft",
    recovery:recovery(null)}, undefined, true);
  const facts = page.getByLabel("Saved task controls").locator(".fx-recovery-facts");
  await expect(facts).toContainText("Verification allowance preserved");
  await expect(facts).not.toContainText("Budget extension required");
  await expect(page.getByRole("button", {name:"Continue generation", exact:true})).toBeEnabled();
  await expect(page.getByRole("button", {name:"Build saved draft", exact:true})).toBeEnabled();
});

test("missing configuration identifies the repair and disables local verification", async ({page}) => {
  await workspace(page,{job_id:"j",job:{...savedJob(),status:"failed",recovery:undefined},status:"failed",change_set_id:"draft",
    classification:"BUILD_CONFIGURATION_MISSING",build_result:{success:false,error_code:"BUILD_CONFIGURATION_MISSING"},
    recovery:recovery("AGENT_RECOVERY_DRAFT_INCOMPLETE"),
    provider_diagnostics:{http_status:200,request_reached_provider:true}});
  await page.getByRole("button",{name:"Agent",exact:true}).click();
  await expect(page.getByRole("button",{name:"Build saved draft",exact:true})).toBeDisabled();
  await expect(page.getByText(/Create or repair platformio.ini before building/)).toBeVisible();
  await expect(page.getByText(/configuration but no firmware source/)).toHaveCount(0);
  await expect(page.getByText(/Failure origin:/)).toHaveCount(0);
  await expect(page.getByRole("button",{name:"Continue generation",exact:true})).toBeEnabled();
});

test("local verification requires permission and refreshes immediately after trust changes", async ({page}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const job = {...savedJob(),status:"blocked",blocking_reason:"AGENT_PROJECT_TRUST_REQUIRED" as string | null};
  let trusted = false;
  const currentRun = () => ({run_id:"r",session_id:"s",project_id:"p",job_id:"j",job,status:"paused_model_unavailable",
    provider_id:"nvidia_nim",model_id:"selected",change_set_id:"draft",events:[],plan:[],changed_files:[],
    recovery:recovery(trusted ? null : "AGENT_PROJECT_TRUST_REQUIRED")});
  await workspace(page, currentRun());
  await page.route("**/agent-runtime/projects/p/trust", async route => {
    if (route.request().method() === "POST") {trusted = route.request().postDataJSON().trusted; job.blocking_reason = null;}
    await route.fulfill({json:{trusted}});
  });
  await page.route("**/agent-runtime/runs/r", route => route.fulfill({json:{run:currentRun()}}));
  await page.getByRole("button",{name:"Agent",exact:true}).click();
  const verify = page.getByRole("button",{name:"Build saved draft",exact:true});
  await expect(verify).toBeDisabled();
  await expect(page.getByRole("button",{name:"Retry selected model",exact:true})).toBeEnabled();
  await expect(page.getByText(/Automatic recovery is waiting for project build permission/)).toBeVisible();
  await page.getByRole("checkbox",{name:"I trust this project for local builds and tests."}).check();
  await expect(verify).toBeEnabled();
  await page.getByRole("checkbox",{name:"I trust this project for local builds and tests."}).uncheck();
  await expect(verify).toBeDisabled();
  expect(errors).toEqual([]);
});

test("configuration-only drafts direct the user to generate source before a local build", async ({page}) => {
  await workspace(page,{job_id:"j",job:savedJob(),status:"paused_model_unavailable",change_set_id:"draft",
    recovery:recovery("AGENT_RECOVERY_DRAFT_INCOMPLETE"),
    provider_diagnostics:{timeout_stage:"first_event",http_status:200,response_headers_received:true,request_reached_provider:true}});
  await page.getByRole("button",{name:"Agent",exact:true}).click();
  await expect(page.getByRole("button",{name:"Build saved draft",exact:true})).toBeDisabled();
  await expect(page.getByRole("button",{name:"Retry selected model",exact:true})).toBeEnabled();
  await expect(page.getByText(/configuration but no firmware source/)).toBeVisible();
  await expect(page.getByText(/No meaningful model output was recorded/)).toBeVisible();
});

test("legacy failed drafts expose inspection and permission-gated local verification", async ({page}) => {
  const job = {...savedJob(), status:"failed"};
  let trusted = false;
  let submittedWork = 0;
  const currentRun = () => ({run_id:"r",session_id:"s",project_id:"p",job_id:"j",job,status:"failed",
    classification:"BUILD_TOOL_UNAVAILABLE",change_set_id:"draft",events:[],plan:[],changed_files:[],
    build_result:{success:false,error_code:"AGENT_PROJECT_TRUST_REQUIRED"},
    recovery:recovery(trusted ? null : "AGENT_PROJECT_TRUST_REQUIRED")});
  await workspace(page, currentRun());
  page.on("request", request => {
    if (request.method() === "POST" && /\/agent-runtime\/(runs|jobs)/.test(request.url())) submittedWork++;
  });
  await page.route("**/agent-runtime/projects/p/trust", async route => {
    if (route.request().method() === "POST") trusted = route.request().postDataJSON().trusted;
    await route.fulfill({json:{trusted}});
  });
  await page.route("**/agent-runtime/runs/r", route => route.fulfill({json:{run:currentRun()}}));
  await page.getByRole("button",{name:"Agent",exact:true}).click();
  const verify = page.getByRole("button",{name:"Build saved draft",exact:true});
  await expect(page.getByRole("button",{name:"Inspect saved work",exact:true})).toBeEnabled();
  await expect(verify).toBeDisabled();
  await expect(page.getByLabel("Saved task controls")).toContainText("Local build permission required");
  await page.getByRole("checkbox",{name:"I trust this project for local builds and tests."}).check();
  await expect(verify).toBeEnabled();
  expect(submittedWork).toBe(0);
});

test("a saved run observes its durable job and follows automatic replacement without submitting work", async ({page}) => {
  const job = savedJob();
  await workspace(page,{job_id:"j",job,status:"paused_model_unavailable",recovery:recovery(null)});
  let polls = 0;
  let submissions = 0;
  page.on("request", request => {if (request.method() === "POST" && request.url().includes("/agent-runtime/")) submissions++;});
  await page.route("**/agent-runtime/jobs/j", async route => {
    polls++;
    await route.fulfill({json:{job:{...job,current_run_id:"r2",status:"running"},recovery:recovery(null)}});
  });
  await page.route("**/agent-runtime/runs/r2", route => route.fulfill({json:{run:{run_id:"r2",session_id:"s",project_id:"p",
    job_id:"j",job:{...job,current_run_id:"r2",status:"running"},status:"running",events:[],plan:[],changed_files:[]}}}));
  await page.getByRole("button",{name:"Agent",exact:true}).click();
  await expect.poll(() => page.evaluate(() => (window as unknown as {testStreams:Array<{url:string;closed:boolean}>})
    .testStreams.some(stream => !stream.closed && stream.url.includes("/runs/r2/events"))),{timeout:12000}).toBe(true);
  expect(polls).toBe(1);
  expect(submissions).toBe(0);
});

test("durable trust blocks appear after the old run stream has settled", async ({page}) => {
  const job = savedJob();
  await workspace(page,{job_id:"j",job,status:"paused_model_unavailable",recovery:recovery(null)});
  await page.route("**/agent-runtime/jobs/j", route => route.fulfill({json:{
    job:{...job,status:"blocked",blocking_reason:"AGENT_PROJECT_TRUST_REQUIRED"},
    recovery:recovery("AGENT_PROJECT_TRUST_REQUIRED")}}));
  await page.getByRole("button",{name:"Agent",exact:true}).click();
  await expect(page.getByText(/Automatic recovery is waiting for project build permission/)).toBeVisible({timeout:12000});
});

test("reopening a completed verification does not follow historical recovery events backwards", async ({page}) => {
  await workspace(page, {status:"completed_with_warning", classification:"DRAFT_BUILD_VERIFIED",
    build_verified:true, job:{...savedJob(),status:"completed_with_warning"}});
  let obsoleteReads = 0;
  await page.route("**/agent-runtime/runs/old-repair", async route => {
    obsoleteReads++;
    await route.fulfill({json:{run:{run_id:"old-repair",status:"failed",project_id:"p"}}});
  });
  await page.getByRole("button",{name:"Agent",exact:true}).click();
  await expect(page.getByLabel("Saved task controls")).toContainText("completed with warning");
  await page.evaluate(() => {
    const streams = (window as unknown as {testStreams:Array<{url:string;closed:boolean;onmessage?: (e:{data:string})=>void}>}).testStreams;
    const source = streams.find(stream => !stream.closed && stream.url.includes("/sessions/s/events"));
    source?.onmessage?.({data:JSON.stringify({run_id:"old-repair",source_run_id:"original",sequence:87,
      event_type:"runtime.resumed",session_id:"s"})});
  });
  await expect(page.getByLabel("Saved task controls")).toContainText("completed with warning");
  expect(obsoleteReads).toBe(0);
});
