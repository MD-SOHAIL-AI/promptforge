import {expect, test} from "@playwright/test";
import {workspace} from "./fixtures/workspace";

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
  const verify = page.getByRole("button",{name:"Verify locally",exact:true});
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
  await expect(page.getByRole("button",{name:"Verify locally",exact:true})).toBeDisabled();
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
  const verify = page.getByRole("button",{name:"Verify locally",exact:true});
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
