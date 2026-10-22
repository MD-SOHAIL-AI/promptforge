import {expect, test} from "@playwright/test";
import {workspace} from "./fixtures/workspace";

test("packaged standalone renderer enforces CSP and loads the offline editor", async ({page}) => {
  test.skip(process.env.FORGEX_BROWSER_PRODUCTION_CSP !== "1", "Requires the packaged production server.");
  await page.addInitScript(() => {
    const violations: string[] = [];
    document.addEventListener("securitypolicyviolation", event => violations.push(event.violatedDirective));
    Object.assign(window,{testCspViolations:violations});
  });
  const response=await page.request.get("/");
  const policy=response.headers()["content-security-policy"];
  expect(policy).toContain("object-src 'none'");
  expect(policy).toContain("frame-ancestors 'none'");
  expect(policy).not.toContain("unsafe-eval");
  await workspace(page,{status:"completed"},undefined,true);
  await page.getByRole("button",{name:"Open workspace",exact:true}).click();
  await page.getByRole("tab",{name:"Files",exact:true}).click();
  await page.getByRole("treeitem",{name:"main.cpp",exact:true}).click();
  await expect(page.locator(".monaco-editor").first()).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as {testCspViolations:string[]}).testCspViolations)).toEqual([]);
});
