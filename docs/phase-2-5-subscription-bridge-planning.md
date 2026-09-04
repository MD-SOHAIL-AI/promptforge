# ForgeX V2 Phase 2.5 - Subscription Bridge Planning

## Goal

Phase 2.5 defines a safe architecture for future local tool bridges without implementing bridge execution. The design lets ForgeX eventually use official user-installed tools such as Codex, Claude Code, and Antigravity / AGY CLI while preserving the existing API-key and local model provider flow.

## Current Boundary

This phase is planning only.

ForgeX must not:

- call Codex CLI
- call Claude Code
- call Antigravity / AGY CLI
- store ChatGPT, Claude, or Gemini session tokens
- scrape browser cookies
- reverse-engineer OAuth flows
- proxy subscription inference through ForgeX servers
- add cloud sync, agents, marketplace, or hidden billing behavior

## Access Modes

ForgeX should support two model access styles:

1. API/local provider mode
   - OpenRouter
   - OpenAI API
   - Anthropic API
   - Gemini API
   - Ollama
   - LM Studio

2. Local tool bridge mode
   - Codex Bridge
   - Claude Code Bridge
   - Antigravity / AGY CLI Bridge

Tool bridges should look like model providers to the router, but internally they invoke official local tools installed and authenticated by the user.

## Bridge Principles

- User-owned account
- User-owned local installation
- User-owned billing or subscription
- ForgeX-owned orchestration
- No token theft
- No browser-cookie scraping
- No hidden cloud proxy
- No storing subscription credentials
- Clear user consent before bridge command execution
- Workspace-contained execution only
- Reviewable file changes before applying bridge edits

## Target Architecture

```text
ModelRouterService
├── API Providers
│   ├── OpenRouter
│   ├── OpenAI API
│   ├── Anthropic API
│   ├── Gemini API
│   ├── Ollama
│   └── LM Studio
│
└── Tool Bridges
    ├── Codex Bridge
    ├── Claude Code Bridge
    └── Antigravity / AGY CLI Bridge
```

Bridge adapters should eventually implement a provider-like contract and report detection, auth, execution, streaming, cancellation, changed files, and diagnostics.

## Routing Plan

Bridge providers can participate in task routes once they expose provider-compatible metadata:

```text
code_generation:
  primary: OpenRouter
  fallback: Codex Bridge

debugging:
  primary: Codex Bridge
  fallback: OpenRouter

documentation:
  primary: OpenRouter
  fallback: local model
```

Bridge routing must respect:

- user preference
- installed status
- auth status
- allowed workspace
- offline availability
- timeout
- cancellation support
- cost and billing explanation

## UI Plan

Settings -> Models should eventually add provider type selection:

- API Provider
- Local Model
- Tool Bridge

Each bridge card should show:

- installed: yes/no
- authenticated: yes/no/unknown
- version
- workspace permissions
- test bridge
- open setup guide

Forge panel should eventually show:

- using bridge
- mode: plan/edit
- awaiting approval
- files changed
- review diff
- apply/reject

## Implementation Sequence

Future phases:

1. Phase 2.5.1 - Bridge Detection Foundation
2. Phase 2.5.2 - Codex Bridge Prototype
3. Phase 2.5.3 - Bridge Diff Review + Approval
4. Phase 2.5.4 - Claude/Gemini Bridge Planning Review
5. Phase 2.5.5 - Bridge Routing + Diagnostics

## Verification Scope

This phase creates documentation only. Existing code paths and tests should continue to pass unchanged.
