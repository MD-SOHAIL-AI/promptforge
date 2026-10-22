import {createHash} from "node:crypto";
import {expect, test} from "@playwright/test";
import {workspace} from "./fixtures/workspace";

const revision = (content: string) => createHash("sha256").update(content).digest("hex");

test("typing and another save during an in-flight save retain text and advance the disk baseline", async ({page}) => {
  await workspace(page, {status:"completed"}, undefined, true, {settings:{"editor.auto_save":false}});
  let content = "// Original firmware\nvoid setup() {}\nvoid loop() {}\n";
  let diskRevision = revision(content);
  const initialRevision = diskRevision;
  const submissions: Array<{content:string; expected_revision?:string}> = [];
  const conflicts: number[] = [];
  let release!: () => void;
  const firstSaveHeld = new Promise<void>(resolve => {release = resolve;});
  await page.route("**/api/forgex/files/content?**", route => route.fulfill({json:{
    project_id:"p",path:"main.cpp",file_type:"cpp",content,revision:diskRevision}}));
  await page.route("**/api/forgex/files", async route => {
    if (route.request().method() !== "PUT") return route.fallback();
    const body = route.request().postDataJSON();
    submissions.push(body);
    if (submissions.length === 1) await firstSaveHeld;
    if (body.expected_revision !== diskRevision) {
      conflicts.push(409);
      return route.fulfill({status:409,json:{code:"FILE_REVISION_CONFLICT",message:"Stale save"}});
    }
    content = body.content;
    diskRevision = revision(content);
    await route.fulfill({json:{project_id:"p",path:"main.cpp",file_type:"cpp",content,revision:diskRevision}});
  });
  await page.getByRole("button", {name:"Open workspace",exact:true}).click();
  await page.getByRole("tab", {name:"Files",exact:true}).click();
  await page.getByText("main.cpp", {exact:true}).first().click();
  const editor = page.locator(".monaco-editor").first();
  await expect(editor).toBeVisible();
  await expect(editor).toContainText("Original firmware");
  const replaceText = async (text: string) => {
    await editor.click();
    await page.keyboard.press("ControlOrMeta+A");
    await page.keyboard.insertText(text);
  };
  const first = "// First save\nvoid setup() {}\nvoid loop() {}\n";
  const second = "// Typed during save\nvoid setup() {}\nvoid loop() {}\n";
  const third = "// Still typing\nvoid setup() {}\nvoid loop() {}\n";
  try {
    await replaceText(first);
    await page.getByRole("button", {name:"Save",exact:true}).click();
    await expect.poll(() => submissions.length).toBe(1);
    await replaceText(second);
    await page.getByRole("button", {name:"Save",exact:true}).click();
    // Let the browser process the click without releasing the first response.
    await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 50)));
    expect(submissions).toHaveLength(1);
    await replaceText(third);
    release();
    await expect.poll(() => submissions.length).toBe(2);
    await expect.poll(() => content).toBe(second);
    await expect(editor).toContainText("Still typing");
    await expect(page.locator(".fx-editor-context")).toContainText("1 unsaved");
    await page.getByRole("button", {name:"Save",exact:true}).click();
    await expect.poll(() => content).toBe(third);
    await expect(page.locator(".fx-editor-context")).toContainText("Saved");
    expect(submissions.map(item => item.expected_revision)).toEqual([
      initialRevision, revision(first), revision(second)]);
    expect(conflicts).toEqual([]);
  } finally {release();}
});
