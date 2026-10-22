import {expect, test} from "@playwright/test";
import {workspace} from "./fixtures/workspace";

test("authentication loss stops device polling and reconnect restores the composer", async ({page}) => {
  await workspace(page, {status:"completed"}, undefined, true);
  const composer = page.getByPlaceholder("Describe what you want to build or change…");
  await expect(composer).toBeEnabled();
  await composer.fill("Keep this draft while reconnecting.");
  let rejected = 0;
  let authenticated = false;
  await page.route("**/api/forgex/devices/boards", route => {
    if (authenticated) return route.fulfill({json:{boards:[]}});
    rejected++;
    return route.fulfill({status:401, json:{code:"LOCAL_AUTH_REQUIRED", message:"Local authentication required"}});
  });
  await expect(page.getByRole("button", {name:"Reconnect", exact:true})).toBeVisible({timeout:12000});
  expect(rejected).toBe(1);
  await expect(composer).toHaveValue("Keep this draft while reconnecting.");
  await page.waitForTimeout(5500);
  expect(rejected).toBe(1);
  await page.route("**/api/forgex/diagnostics/runtime", route => {
    authenticated = true;
    return route.fulfill({json:{backend_instance_id:"fixture", runtime_contract:"forgex-agent-runtime-v3"}});
  });
  await Promise.all([
    page.waitForEvent("load"),
    page.getByRole("button", {name:"Reconnect", exact:true}).click(),
  ]);
  await expect(page.getByRole("button", {name:"Reconnect", exact:true})).toHaveCount(0);
  await expect(composer).toBeEnabled();
  await expect(composer).toHaveValue("Keep this draft while reconnecting.");
});

test("authentication loss closes active task streams and suspends all observations", async ({page}) => {
  await workspace(page, {status:"running"}, undefined, true);
  await expect.poll(() => page.evaluate(() => (
    window as unknown as {testStreams:Array<{url:string;closed:boolean}>}
  ).testStreams.some(stream => !stream.closed && stream.url.includes("/runs/r/events")))).toBe(true);
  await page.route("**/api/forgex/devices/boards", route => route.fulfill({status:401,
    json:{code:"LOCAL_AUTH_REQUIRED", message:"Local authentication required"}}));
  await expect(page.getByRole("button", {name:"Reconnect", exact:true})).toBeVisible({timeout:12000});
  const streamsClosed = () => page.evaluate(() => {
    const streams = (window as unknown as {testStreams:Array<{closed:boolean}>}).testStreams;
    return streams.length > 0 && streams.every(stream => stream.closed);
  });
  await expect.poll(streamsClosed).toBe(true);
  let observations = 0;
  page.on("request", request => {if (new URL(request.url()).pathname.startsWith("/api/forgex/")) observations++;});
  await page.waitForTimeout(6500);
  expect(observations).toBe(0);
  expect(await streamsClosed()).toBe(true);
});
