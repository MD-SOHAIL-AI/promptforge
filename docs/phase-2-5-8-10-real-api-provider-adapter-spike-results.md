# Phase 2.5.8.10 Real API Provider Adapter Spike Results

## Phase 2.5.8.10A operator continuation

Operator preflight confirmed that API authorization was absent without printing or persisting a value. The guarded command reported `API_PROVIDER_KEY_MISSING` with both flags and confirmation present, zero outbound requests, zero tools, zero changes, and no review. Live transport therefore remains pending.

## Result

ForgeX now has a disabled-by-default OpenAI Responses API planner adapter. Mocked structured output passes through the real adapter, the existing ForgeX tool runtime creates exactly one sandbox file, and a Bridge Review is created only after the exact diff gate passes.

No live network request was attempted. The guarded operator command was run with both feature flags enabled and no authorization value; it returned `API_PROVIDER_KEY_MISSING` with zero requests, zero tool executions, zero files, and no review.

## Phase questions

**Q1. Which real API provider adapter was added?**  
The OpenAI Responses API adapter, provider ID `openai_api`.

**Q2. Is it disabled by default?**  
Yes. Normal UI routing is unavailable, registry execution/routing are false, both feature flags default to off, and explicit command confirmation is required.

**Q3. What environment variable is used for the API key?**  
`OPENAI_API_KEY`, read from the process environment only after all real-smoke gates pass.

**Q4. What model is used by default for the smoke?**  
`gpt-4.1-mini`, with an optional guarded override through `FORGEX_OPENAI_MODEL`.

**Q5. What structured ToolPlan schema is required?**  
A root object containing only `tool_calls`; for the smoke it must contain exactly one `write_file` call with the exact relative filename and exact expected content.

**Q6. How is model output validated?**  
The API is asked for strict JSON Schema output. ForgeX then performs bounded UTF-8 JSON parsing, strict root/call/argument validation, known-tool and authority checks, exact call-count checks, and the existing path/content/tool-policy validation.

**Q7. How does ForgeX prevent active workspace mutation?**  
The provider never receives an active path. Tools are rooted in a unique managed sandbox, and active-workspace plus sandbox-marker integrity are verified before review creation.

**Q8. Can the fake provider tests still pass?**  
Yes. The existing fake smoke remains `TOOL_RUNTIME_PASS`.

**Q9. Can the real API provider create exactly one sandbox file through ForgeX tools?**  
Yes with a mocked API response. A live request was not attempted because authorization was absent.

**Q10. Was a review candidate created?**  
Yes for the mocked adapter pass; no for the guarded no-key result.

**Q11. Were patch export / verify / preflight run?**  
No. They remain optional and post-review only; the no-key run had no review.

**Q12. Were apply/build/flash avoided?**  
Yes. All remained false.

**Q13. Is this production-ready or still a spike?**  
It is still a disabled, non-production spike.

## Remaining limitation

The real transport has not been validated against an authorized account. Provider availability, billing, model access, live schema behavior, rate limits, and latency remain unproven.

## Verification

| Check | Result |
| --- | --- |
| Focused API/provider/runtime/security selector | 104 passed, 1 host-limited symlink skip, 1,604 deselected |
| Fake runtime smoke | `TOOL_RUNTIME_PASS`; one file and one review |
| Guarded real-provider no-key smoke | `API_PROVIDER_KEY_MISSING`; zero request, tools, files, or reviews |
| Safety scan | 153 passed, 0 failed |
| Backend compile | PASS |
| Unmodified full backend suite | 1,698 passed, 10 skipped, 1 unrelated local timeout-override failure |
| Process-scoped 180-second backend suite | 1,699 passed, 10 skipped |
| Frontend typecheck and production build | PASS; four static pages |
| Electron build and tests | PASS; 10 tests |

Recommended next phase: **Phase 2.5.8.10A — Operator API Key Validation and Real Smoke**. If an authorized smoke later passes, proceed to **Phase 2.5.8.11 — API Provider Streaming, Retry, and Review Hardening**.
