import assert from"node:assert/strict";import test from"node:test";import{deriveSurfacePhase,workflowPresentation}from"./control-plane.ts";
test("unavailable state is explicit",()=>assert.equal(deriveSurfacePhase({hasData:false,loading:false,error:true}),"unavailable"));
test("reconnecting retains backend projection",()=>assert.equal(deriveSurfacePhase({hasData:true,loading:true,error:true}),"reconnecting"));
test("approval-waiting state is explicit",()=>assert.deepEqual(workflowPresentation("awaiting_apply_approval"),{tone:"warning",label:"Waiting for apply approval"}));
test("failed state is explicit",()=>assert.deepEqual(workflowPresentation("failed"),{tone:"error",label:"failed"}));
test("completed state is explicit",()=>assert.deepEqual(workflowPresentation("completed"),{tone:"success",label:"Completed"}));
