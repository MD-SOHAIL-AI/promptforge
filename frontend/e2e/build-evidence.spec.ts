import {expect, test} from "@playwright/test";
import {workspace} from "./fixtures/workspace";

test("failed compiler evidence remains visibly failed", async ({page}) => {
  await workspace(page, {status:"failed",build_verified:false,build_result:{success:false,message:"Compiler error: missing declaration",warnings_count:2}});
  await page.getByRole("button", {name:"Agent", exact:true}).click();
  await page.getByRole("button", {name:"Open workspace", exact:true}).click();
  await page.getByRole("tab", {name:"Build", exact:true}).click();
  await expect(page.getByText("Firmware build failed", {exact:true})).toBeVisible();
  await expect(page.getByText("FAILED", {exact:true})).toBeVisible();
  await expect(page.getByRole("button", {name:"Flash device", exact:true})).toHaveCount(0);
  await page.screenshot({path:"test-results/redesign-build-failure.png"});
});

test("stale compiler output cannot enable flashing or fabricated memory metrics", async ({page}) => {
  await workspace(page, {status:"failed",build_verified:false,build_result:{success:true,build_size_bytes:100000,message:"Earlier compiler output"}});
  await page.getByRole("button", {name:"Agent", exact:true}).click();
  await page.getByRole("button", {name:"Open workspace", exact:true}).click();
  await page.getByRole("tab", {name:"Build", exact:true}).click();
  await expect(page.getByText("Build needs verification", {exact:true})).toBeVisible();
  await expect(page.getByRole("button", {name:"Flash device", exact:true})).toHaveCount(0);
  await expect(page.getByText("Measurement unavailable", {exact:true})).toHaveCount(2);
  await expect(page.getByText("31%", {exact:true})).toHaveCount(0);
});

test("saved manual build refresh retrieves its result without another execution", async ({page}) => {
  await workspace(page, {status:"completed"});
  let starts = 0;
  let operationId = "";
  let state = "passed";
  await page.route("**/api/forgex/projects/p/files", route => route.fulfill({json:{entries:[
    {path:"platformio.ini",name:"platformio.ini",kind:"file",file_type:"ini"},
    {path:"main.cpp",name:"main.cpp",kind:"file",file_type:"cpp"}]}}));
  await page.route("**/api/forgex/build/operations", async route => {
    starts++;
    operationId = route.request().headers()["idempotency-key"];
    expect(route.request().postDataJSON()).toEqual({project_id:"p",environment:"debug"});
    await route.fulfill({status:202,json:{operation_id:operationId,project_id:"p",environment:"debug",state:"queued",source_revision:"rev",result:null}});
  });
  await page.route("**/api/forgex/build/operations/*", route => route.fulfill({json:{
    operation_id:operationId,project_id:"p",environment:"debug",source_revision:"rev",state,
    updated_at:new Date().toISOString(),result:{project_id:"p",result:{success:true,status:"success",message:"Compiled current revision",build_size_bytes:12345,metadata:{flash_used_bytes:12345,flash_total_bytes:100000,ram_used_bytes:1000,ram_total_bytes:10000}}}}}));
  await page.getByRole("button", {name:"Agent",exact:true}).click();
  await page.getByRole("button", {name:"Open workspace",exact:true}).click();
  await page.getByRole("tab", {name:"Build",exact:true}).click();
  await page.getByRole("button", {name:"Build",exact:true}).click();
  await expect(page.getByText("Firmware compiled successfully",{exact:true})).toBeVisible();
  expect(starts).toBe(1);
  await page.getByRole("button", {name:"Refresh result",exact:true}).click();
  await expect(page.getByText("PASSED",{exact:true})).toBeVisible();
  expect(starts).toBe(1);
  state = "stale";
  await page.getByRole("button", {name:"Refresh result",exact:true}).click();
  await expect(page.getByText("Build needs verification",{exact:true})).toBeVisible();
  await expect(page.getByRole("button", {name:"Flash device",exact:true})).toHaveCount(0);
  expect(starts).toBe(1);
});

test("a held response body becomes stale and a later observation recovers", async ({page}) => {
  test.setTimeout(60000);
  await page.addInitScript(() => {
    const fetchOriginal = window.fetch;
    let reads = 0;
    Object.assign(window,{holdRunBody:true});
    window.fetch = async (...args) => {
      if (String(args[0]).endsWith("/agent-runtime/runs/r") && ++reads > 1 && (window as unknown as {holdRunBody:boolean}).holdRunBody) {
        return {ok:true,status:200,json:() => new Promise(() => {})} as Response;
      }
      return fetchOriginal(...args);
    };
  });
  await workspace(page);
  await page.getByRole("button", {name:"Agent",exact:true}).click();
  await expect(page.getByText(/Status is stale. Reconnecting/)).toBeVisible({timeout:40000});
  await page.evaluate(() => Object.assign(window,{holdRunBody:false}));
  await expect(page.getByText(/Status is stale. Reconnecting/)).toHaveCount(0,{timeout:15000});
  await expect(page.getByRole("button", {name:"Stop task",exact:true})).toHaveCount(2);
});
