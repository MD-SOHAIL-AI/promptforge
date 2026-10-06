# ForgeX live desktop usability test

Date: 2026-10-06. Checkout: D:/projects/ForgeX-Phase6-Final.

## Method and scope

Used the running Electron desktop app and its real frontend/backend. The native Computer Use helper could not connect to its Windows pipe after retry and kernel reset. This was disclosed during testing. Fallback: the repository's existing Playwright installation, attached to the actual Electron renderer through its localhost debugging port. Actions used visible buttons, tabs, search fields and the chat composer; observations used rendered content, screenshots, response events and existing desktop logs. No mocked provider responses, injected application state or direct API operations were used to drive these checks.

This is a live UI test, not a complete repository audit or release qualification. Eighteen recorded checks: 11 passed, 6 failed and 1 partial. Failed checks include repeated reproductions and two views of the same output problem; they represent four distinct issues below.

## Verified results

| Flow | Result | Evidence |
| --- | --- | --- |
| Refresh saved draft build | BUILD PASSED; esp32dev; 265.7 KB firmware; zero warnings | [02](02-refreshed-build.png), [27](27-final-build-passed.png) |
| Review staged changes | README.md, platformio.ini and src/main.cpp are selectable; source diff is visible | [05](05-changes.png), [06](06-source-diff.png) |
| Workspace and staged files | Active explorer displays zero files while three changes remain staged; no Apply or Discard action taken | [07](07-files.png) |
| Verification evidence | Compilation Built; requirement/source review, logic tests and simulation Unverified; device behavior Unqualified | [08](08-evidence.png) |
| Settings navigation | General and Models & Agents render; credentials and preferences were not edited | [14](14-settings.png), [15](15-model-settings.png) |
| Live provider chat | Selected OpenRouter nvidia/nemotron-3-ultra-550b-a55b:free through the model picker; one synthetic prompt received exactly FORGEX_UI_TEST_OK | [21](21-live-chat-progress.png) |
| Chat reload persistence | Synthetic prompt, reply and selected model remain after reload | [23](23-live-chat-persisted.png) |
| Agent reload persistence | Saved conversations and the completed draft return after reload and Agent navigation | [24](24-agent-after-reload.png), [27](27-final-build-passed.png) |
| Activity View file | Opens staged src/main.cpp review | [25](25-journal-view-file.png) |
| Task search | Nonmatching query hides tasks; clearing it restores saved entries | observations.json |

The build refresh re-read the existing verified artifact; this turn did not run another compiler build. The successful local PlatformIO compile belongs to the preceding verification work. Compilation is not proof that all requested documentation or physical behavior is correct.

## Remaining issues

### HUMAN-01 — Raw output opens the wrong view and does not show saved compiler output

Priority: P2. Verified UI behavior; underlying code cause not investigated in this test.

Reproduction: open Agent > Build results; click Raw output. The Engineering console opens with Terminal selected and a PowerShell session starting. Select Output manually: only “ForgeX output ready.” appears, without the saved build log. The activity journal separately advertises build output, so this finding concerns the Raw output destination and console data, not absence of build evidence everywhere.

Impact: users cannot inspect the saved compiler result through the build panel's advertised control; an unnecessary terminal session is created.

Evidence: [03](03-build-output.png), [04](04-console-output.png).

Recommended follow-up: trace the Raw output handler, console tab selection and run-scoped log loading; verify this button opens the saved run's output without creating a terminal.

### HUMAN-02 — Reopening a saved project loses the usable workspace/task state

Priority: P2. Reproduced twice.

Reproduction: from the restored Agent task, open Projects and select the root project at C:/Users/diwan/OneDrive/Desktop/New folder. Its name/path appears in the header, but the Agent sidebar says “No conversations yet” and the composer says “Open a workspace to enable task submission.” Saved tasks do not return within eight seconds. No JavaScript page error or contemporaneous HTTP error was captured for this action. A full renderer reload, followed by Agent navigation after startup, restores the saved task and build.

Impact: routine project navigation makes task submission and saved conversation selection unavailable until reload. Data loss was not observed.

Evidence: [13](13-project-restore-failure.png), [26](26-project-reopen-repeat.png); recovery in [24](24-agent-after-reload.png) and [27](27-final-build-passed.png).

Recommended follow-up: trace project-card activation, workspace identity and session hydration; add a real project-switch regression check that validates both composer readiness and saved conversation restoration.

### HUMAN-03 — Backend became unavailable and Electron restarted it during normal navigation

Priority: P2 pending root-cause investigation. Observed once; not claimed reproducible.

While selecting the chat model, before submitting the synthetic prompt, the app went Offline and local health/device/chat requests returned HTTP 500. Frontend proxy logs record ECONNREFUSED at 127.0.0.1:8000. Desktop logs record “Scheduled owned backend restart in 2000ms” followed by a new server process and successful startup. The UI eventually recovered to Connected. Model discovery immediately preceding this returned HTTP 200 after approximately 13 seconds.

Impact: a temporary local service outage interrupts normal app use. Exit cause, exit code and the relationship to model discovery are not established. These local proxy errors are not evidence that OpenRouter returned a provider error. The subsequent live Nemotron request succeeded.

Evidence: [17](17-chat-connection.png), [18](18-runtime-status.png), [backend log excerpt](backend-restart-excerpt.txt). Original logs: forgex-completed-desktop.stderr.log (restart at line 52) and forgex-controlled-frontend.stderr.log (refused local connections from line 147 onward). The proxy file also contains earlier errors; only the errors observed during this UI session were counted in observations.json.

Recommended follow-up: retain child exit code/signal and crash diagnostics, correlate the desktop restart with Windows process events, and repeat model discovery without inferring a crash cause from timing alone.

### HUMAN-04 — Chat's interrupted-connection label remains after recovery and a successful response

Priority: P3. Verified UI inconsistency.

After the backend recovered, the global indicator said Connected and Nemotron returned FORGEX_UI_TEST_OK. Chat still displayed “Connection interrupted. Reconnecting…”. Reloading and reopening Chat preserved the message and removed the stale label.

Impact: a successful request looks as though it is still interrupted.

Evidence: [21](21-live-chat-progress.png), [23](23-live-chat-persisted.png).

Recommended follow-up: trace chat stream reconnection and status transitions; verify a successful reconnection clears the warning while preserving messages.

## Hardware and other limits

Device Detect listed five UNKNOWN ports: COM3, COM4, COM5, COM8 and COM9, described as Standard Serial over Bluetooth link/Microsoft. No ESP32 was positively identified. The UI displayed UNKNOWN/Live on COM3 and enabled Flash and Monitor controls. This does not prove a working board connection or an authorization bypass. No flash, reset, physical serial monitor connection or hardware verification was attempted. Evidence: [09](09-device.png), [10](10-device-detect.png).

No existing project files were attached to the synthetic provider prompt. Prompt: “Reply with exactly FORGEX_UI_TEST_OK. This is a synthetic interface test; do not use tools.” It used the previously authorized configured account. A local chat record and its selected model were created. No credentials were exposed or edited. No terminal commands were entered. No source changes were made in this testing turn, and staged files were not applied or discarded.

Zero JavaScript pageerror events were captured during observation; this does not establish absence of all frontend errors. Aborted requests around navigation/reload are retained as raw observations rather than classified as product failures.

## Final app state

ForgeX remains open on the original Agent task's Build results: BUILD PASSED for the saved esp32dev draft. The task is completed with warning because requirement review and hardware verification remain separate. Three files remain staged. Final screenshot: [27](27-final-build-passed.png).

Raw check outcomes and HTTP failure observations: [observations.json](observations.json). Newly found issues remain unfixed; the request for this turn was to test the application.
