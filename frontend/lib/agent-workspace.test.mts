import assert from"node:assert/strict";import test from"node:test";import{mergeWorkspaceProjection}from"./agent-workspace-state.ts";
const projection=(sequence:number,status="routing")=>({run:{run_id:"run",status,version:sequence},events:[],messages:[],last_sequence:sequence});
test("reordered replay cannot replace a newer projection",()=>assert.equal(mergeWorkspaceProjection(projection(8),projection(7)).last_sequence,8));
test("reconnect replay restores the authoritative newer state",()=>assert.equal(mergeWorkspaceProjection(projection(8),projection(9,"generating")).run.status,"generating"));
test("a different selected run replaces the projection",()=>{const next={...projection(1),run:{run_id:"other",status:"draft",version:0}};assert.equal(mergeWorkspaceProjection(projection(8),next).run.run_id,"other")});
