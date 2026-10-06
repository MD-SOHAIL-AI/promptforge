"""Launch the actual bundled desktop in an isolated profile and verify its UI/API."""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import socket
import struct
import zlib
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from backend.runtime.windows_job import WindowsJob
from websockets.sync.client import connect


def request(url):
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.load(response)


def assert_no_error_dialog(browser_pid):
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    errors = []

    @callback_type
    def visit(window, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(window, title, len(title))
        if owner.value == browser_pid and title.value == "Error":
            errors.append(window)
        return True

    user32.EnumWindows(visit, 0)
    assert not errors, "DESKTOP_SMOKE_MAIN_PROCESS_ERROR_DIALOG"


def renderer_crash_drill(debug_port, backend_url, screenshot):
    """Crash the actual renderer four times, then recover by explicit navigation."""
    health = request(backend_url + "/health")
    def evaluate(target, expression, *, capture=False):
        with connect(target["webSocketDebuggerUrl"], open_timeout=3) as channel:
            channel.send(json.dumps({"id": 1, "method": "Page.captureScreenshot" if capture else "Runtime.evaluate",
                "params": {"format": "png"} if capture else {"expression": expression, "returnByValue": True}}))
            while True:
                # The first frame after a renderer crash may still be painting.
                reply = json.loads(channel.recv(timeout=15 if capture else 3))
                if reply.get("id") == 1:
                    return reply.get("result", {}).get("data") if capture else reply.get("result", {}).get("result", {}).get("value")
    def target():
        return next(item for item in request(f"http://127.0.0.1:{debug_port}/json/list") if item.get("type") == "page")
    def wait_for(text):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                page = target()
                expression = "document.querySelector('.fx-nav-item[aria-current=\"page\"]')?.textContent.includes('Agent') ? 'APP_READY' : document.body?.innerText"
                if text in (evaluate(page, expression) or ""):
                    return page
            except (TimeoutError, ConnectionError, urllib.error.URLError):
                pass
            time.sleep(0.1)
        raise AssertionError("DESKTOP_RENDERER_CRASH_RECOVERY_TIMEOUT")
    started = time.monotonic()
    for crash in range(4):
        page = target()
        with connect(page["webSocketDebuggerUrl"], open_timeout=3) as channel:
            channel.send(json.dumps({"id": 1, "method": "Page.crash"}))
            # Page.crash has no successful reply; the renderer connection dies.
            time.sleep(0.1)
        page = wait_for("The window stopped reloading" if crash == 3 else "APP_READY")
        assert request(backend_url + "/health")["local_auth_fingerprint"] == health["local_auth_fingerprint"]
    assert time.monotonic() - started < 60, "DESKTOP_RENDERER_DRILL_WINDOW_EXCEEDED"
    blocked = Path(screenshot).with_name(Path(screenshot).stem + "-circuit.png")
    blocked.write_bytes(base64.b64decode(evaluate(page, "", capture=True)))
    evaluate(page, "document.querySelector('a').click()")
    page = wait_for("APP_READY")
    Path(screenshot).write_bytes(base64.b64decode(evaluate(page, "", capture=True)))
    print("Renderer crash drill passed: three reloads, fourth crash stopped, manual reload restored the app; backend authentication identity unchanged")


def smoke(executable, screenshot, *, closed_output_pipes=False, isolated_ports=False, renderer_crashes=False, redesign=False):
    executable = Path(executable).resolve(strict=True)
    if sys.platform != "win32":
        raise ValueError("Desktop smoke requires Windows")
    ports = (8000, 3000, 8765)
    if isolated_ports:
        from contextlib import ExitStack
        with ExitStack() as stack:
            probes = [stack.enter_context(socket.socket()) for _ in ports]
            for probe in probes:
                probe.bind(("127.0.0.1", 0))
            ports = tuple(probe.getsockname()[1] for probe in probes)
    backend_port, frontend_port, harness_port = ports
    backend_url = f"http://127.0.0.1:{backend_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}"
    for port in ports:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise ValueError(f"DESKTOP_SMOKE_PORT_OCCUPIED: {port}")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        debug_port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="forgex-desktop-smoke-") as temporary:
        profile = Path(temporary)
        if redesign:
            asset_id = "background-11111111-1111-4111-8111-111111111111.png"
            background_dir = profile / "desktop/backgrounds"
            background_dir.mkdir(parents=True)
            width, height = 640, 360
            raw = b"".join(b"\0" + bytes(channel for x in range(width) for channel in (18 + x * 65 // width, 35 + y * 100 // height, 70 + x * 90 // width)) for y in range(height))
            def chunk(name, content):
                payload = name + content
                return struct.pack(">I", len(content)) + payload + struct.pack(">I", zlib.crc32(payload))
            png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
            (background_dir / asset_id).write_bytes(png)
            (profile / "settings.json").write_text(json.dumps({"settings": {
                "appearance.background_asset_id": asset_id, "appearance.background_asset_name": "Protocol fixture.png",
                "appearance.surface_opacity": 70, "appearance.background_dim": 35, "appearance.background_blur": 4,
                "appearance.background_fit": "fill", "appearance.activity_detail": "normal",
            }}), encoding="utf-8")
        environment = {**os.environ, "FORGEX_ELECTRON_USER_DATA_DIR": str(profile),
                       "FORGEX_SETTINGS_PATH": str(profile / "settings.json"),
                       "FORGEX_LEGACY_DATA_DIR": "", "FORGEX_FRONTEND_PORT": str(frontend_port),
                       "FORGEX_BACKEND_PORT": str(backend_port), "FORGEX_HARNESS_PORT": str(harness_port),
                       # The application must use bundled Python and Electron's
                       # Node runtime without relying on development PATH entries.
                       "PATH": str(Path(os.environ["SystemRoot"]) / "System32")}
        for key in ("ELECTRON_RUN_AS_NODE", "NODE_OPTIONS", "PYTHONPATH", "PYTHONHOME", "FORGEX_FRONTEND_URL"):
            environment.pop(key, None)
        job = WindowsJob()
        with (profile / "desktop.log").open("wb") as output:
            process = subprocess.Popen(job.wrap([str(executable), f"--remote-debugging-port={debug_port}",
                                                  "--remote-debugging-address=127.0.0.1"]),
                                       env=environment, cwd=executable.parent,
                                       stdout=subprocess.PIPE if closed_output_pipes else output,
                                       stderr=subprocess.PIPE if closed_output_pipes else subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            if closed_output_pipes:
                # Reproduce a terminal disappearing immediately after launch.
                # The GUI and its services must survive both broken log pipes.
                process.stdout.close()
                process.stderr.close()
            browser_url = None
            page = None
            try:
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        break
                    try:
                        health = request(backend_url + "/health")
                        assert health.get("local_auth_required") is True
                        browser_url = request(f"http://127.0.0.1:{debug_port}/json/version")["webSocketDebuggerUrl"]
                        targets = request(f"http://127.0.0.1:{debug_port}/json/list")
                        page = next((item for item in targets if item.get("type") == "page" and item.get("url", "").startswith(frontend_url)), None)
                        if page:
                            break
                    except (urllib.error.URLError, ConnectionError, TimeoutError, StopIteration):
                        pass
                    time.sleep(0.2)
                else:
                    raise RuntimeError("DESKTOP_SMOKE_STARTUP_TIMEOUT")
                if process.poll() is not None or not page:
                    raise RuntimeError("DESKTOP_SMOKE_STARTUP_FAILED")
                try:
                    request(backend_url + "/settings")
                    raise AssertionError("Unauthenticated settings were accepted")
                except urllib.error.HTTPError as denied:
                    assert denied.code == 401
                with connect(page["webSocketDebuggerUrl"], open_timeout=5) as channel:
                    sequence = 0
                    def command(method, params=None):
                        nonlocal sequence
                        sequence += 1
                        channel.send(json.dumps({"id": sequence, "method": method, "params": params or {}}))
                        while True:
                            reply = json.loads(channel.recv(timeout=15))
                            if reply.get("id") == sequence:
                                if "error" in reply:
                                    raise RuntimeError("DESKTOP_CDP_FAILED: " + str(reply["error"]))
                                return reply["result"]
                    # A CDP target exists as soon as navigation begins. Wait
                    # for its document before evaluating renderer API calls.
                    document_deadline = time.monotonic() + 30
                    while time.monotonic() < document_deadline:
                        ready = command("Runtime.evaluate", {
                            "expression": "document.readyState === 'complete' && Boolean(document.body) && document.body.innerText.includes('ForgeX')",
                            "returnByValue": True,
                        })
                        if ready.get("result", {}).get("value") is True:
                            break
                        time.sleep(0.1)
                    else:
                        raise RuntimeError("DESKTOP_SMOKE_DOCUMENT_TIMEOUT")
                    expression = "(async()=>JSON.stringify({text:document.body.innerText,settings:(await fetch('/api/forgex/settings')).status,projects:(await fetch('/api/forgex/projects')).status,sessions:(await fetch('/api/forgex/agent-runtime/sessions')).status}))()"
                    result = command("Runtime.evaluate", {"expression": expression, "awaitPromise": True, "returnByValue": True})
                    assert "exceptionDetails" not in result, result.get("exceptionDetails")
                    state = json.loads(result["result"]["value"])
                    assert "ForgeX" in state["text"]
                    assert "Chat history unavailable" not in state["text"]
                    assert "ForgeX runtime error" not in state["text"]
                    assert (state["settings"], state["projects"], state["sessions"]) == (200, 200, 200), state
                    # A fresh chat view misses failures in a restored agent
                    # destination and its dynamically mounted workspace panels.
                    open_workspace = """(async()=>{
                      const waitFor=async(selector)=>{
                        for(let i=0;i<100;i++){
                          const element=document.querySelector(selector);
                          if(element)return element;
                          await new Promise(resolve=>setTimeout(resolve,50));
                        }
                        throw new Error('DESKTOP_SMOKE_WORKSPACE_TIMEOUT');
                      };
                      const agent=Array.from(document.querySelectorAll('.fx-nav-item')).find(element=>element.textContent.trim()==='Agent');
                      if(!agent)throw new Error('DESKTOP_SMOKE_AGENT_NAVIGATION_MISSING');
                      agent.click();
                      // Native DOM clicks do not wait for React's navigation
                      // commit. Its view-change effect also closes old panels.
                      for(let i=0;i<100;i++){
                        if(document.querySelector('.fx-nav-item[aria-current="page"]')?.textContent.trim()==='Agent')break;
                        await new Promise(resolve=>setTimeout(resolve,50));
                      }
                      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
                      (await waitFor('button[aria-label="Open workspace"]')).click();
                      await waitFor('[role="tablist"][aria-label="Workspace tools"]');
                      return !document.body.innerText.includes('ForgeX runtime error');
                    })()"""
                    for reload_view in (False, True):
                        if reload_view:
                            command("Page.reload")
                            restored_deadline = time.monotonic() + 15
                            while time.monotonic() < restored_deadline:
                                restored = command("Runtime.evaluate", {
                                    "expression": "Boolean(document.querySelector('.fx-nav-item[aria-current=\"page\"]')?.textContent.includes('Agent'))",
                                    "returnByValue": True,
                                })
                                if restored.get("result", {}).get("value") is True:
                                    break
                                time.sleep(0.1)
                            else:
                                raise RuntimeError("DESKTOP_SMOKE_AGENT_RESTORE_FAILED")
                        workspace = command("Runtime.evaluate", {
                            "expression": open_workspace, "awaitPromise": True, "returnByValue": True,
                        })
                        assert "exceptionDetails" not in workspace, workspace.get("exceptionDetails")
                        assert workspace.get("result", {}).get("value") is True
                    if redesign:
                        verify = """(async()=>{
                          const wait=async(predicate)=>{
                            for(let i=0;i<120;i++){if(await predicate())return; await new Promise(resolve=>setTimeout(resolve,50));}
                            throw new Error('REDESIGN_SMOKE_WAIT_TIMEOUT');
                          };
                          const id='background-11111111-1111-4111-8111-111111111111.png';
                          const asset=await window.forgexDesktop.resolveBackgroundImage(id);
                          if(!asset?.url.startsWith('forgex-asset:'))throw new Error('REDESIGN_NATIVE_ASSET_PROTOCOL_MISSING');
                          const image=new Image(); image.src=asset.url; await image.decode();
                          if(image.naturalWidth!==640)throw new Error('REDESIGN_NATIVE_IMAGE_DECODE_FAILED');
                          await document.fonts.load('14px "ForgeX Sans"');
                          await document.fonts.load('14px "ForgeX Mono"');
                          if(!document.fonts.check('14px "ForgeX Sans"') || !document.fonts.check('14px "ForgeX Mono"'))throw new Error('REDESIGN_BUNDLED_FONTS_MISSING');
                          const editorAssets=await Promise.all([fetch('/vendor/monaco/version.json'),fetch('/vendor/monaco/vs/loader.js')]);
                          if(editorAssets.some(response=>!response.ok) || (await editorAssets[0].json()).version!=='0.57.0')throw new Error('REDESIGN_LOCAL_EDITOR_ASSETS_MISSING');
                          await wait(()=>document.documentElement.style.getPropertyValue('--fx-background-image').includes(id));
                          const root=document.documentElement;
                          if(root.style.getPropertyValue('--fx-surface-opacity')!=='70%' || root.style.getPropertyValue('--fx-background-fit')!=='100% 100%')throw new Error('REDESIGN_PREFERENCES_NOT_RESTORED');
                          Array.from(document.querySelectorAll('.fx-nav-item')).find(item=>item.textContent.trim()==='Settings').click();
                          await wait(()=>document.querySelector('[data-settings-category="appearance"]'));
                          document.querySelector('[data-settings-category="appearance"]').click();
                          await wait(()=>document.querySelector('.fx-appearance-preview'));
                          await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
                          const preview=document.querySelector('.fx-appearance-preview .fx-workspace-background-image');
                          if(!getComputedStyle(preview).backgroundImage.includes(id))throw new Error('REDESIGN_PREVIEW_IMAGE_MISSING');
                          return {nativeProtocol:true,decodedImage:true,bundledFonts:true,localEditorAssets:true,restoredOpacity:true,matchingPreview:true};
                        })()"""
                        result = command("Runtime.evaluate", {"expression": verify, "awaitPromise": True, "returnByValue": True})
                        assert "exceptionDetails" not in result, result.get("exceptionDetails")
                        assert result["result"]["value"]["matchingPreview"] is True
                        print("Redesign packaged checks:", json.dumps(result["result"]["value"]))
                    image = command("Page.captureScreenshot", {"format": "png"})
                    screenshot.parent.mkdir(parents=True, exist_ok=True)
                    screenshot.write_bytes(base64.b64decode(image["data"]))
                    if redesign:
                        remove = """(async()=>{
                          const button=Array.from(document.querySelectorAll('.fx-card button')).find(item=>item.textContent.trim()==='Remove');
                          if(!button || button.disabled)throw new Error('REDESIGN_REMOVE_CONTROL_UNAVAILABLE');
                          button.click();
                          for(let i=0;i<120;i++){
                            if(document.documentElement.style.getPropertyValue('--fx-background-image')==='none'){
                              const response=await fetch('/api/forgex/settings'); const body=await response.json();
                              if(body.settings['appearance.background_asset_id']===''){
                                const asset=await window.forgexDesktop.resolveBackgroundImage('background-11111111-1111-4111-8111-111111111111.png');
                                if(!asset)return true;
                              }
                            }
                            await new Promise(resolve=>setTimeout(resolve,50));
                          }
                          throw new Error('REDESIGN_NATIVE_REMOVAL_FAILED');
                        })()"""
                        result = command("Runtime.evaluate", {"expression": remove, "awaitPromise": True, "returnByValue": True})
                        assert "exceptionDetails" not in result, result.get("exceptionDetails")
                        assert result["result"]["value"] is True
                        print("Redesign native removal passed: settings cleared and copied image deleted")
                if renderer_crashes:
                    renderer_crash_drill(debug_port, backend_url, screenshot)
                # Renderer HTTP checks can pass behind Electron's native
                # uncaught-exception dialog. Check the browser process too.
                with connect(browser_url, open_timeout=5) as browser:
                    browser.send(json.dumps({"id": 1, "method": "SystemInfo.getProcessInfo"}))
                    process_info = json.loads(browser.recv(timeout=5))["result"]["processInfo"]
                    browser_pid = next(int(item["id"]) for item in process_info if item["type"] == "browser")
                    assert_no_error_dialog(browser_pid)
                assert profile.joinpath("backend/.promptforge/state").is_dir()
                manifest = json.loads(executable.parent.joinpath("resources/forgex/release-manifest.json").read_text(encoding="utf-8"))
                log_root = profile / "backend/logs"
                backend_logs = list(log_root.glob("live-runtime.backend-*.jsonl"))
                desktop_logs = list(log_root.glob("live-runtime.desktop-*.jsonl"))
                assert backend_logs and desktop_logs, "DESKTOP_SMOKE_INSTANCE_LOGS_MISSING"
                launch_ids = set()
                for log_path in (*backend_logs, *desktop_logs):
                    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                    assert records, "DESKTOP_SMOKE_INSTANCE_LOG_EMPTY"
                    for record in records:
                        assert record["app_version"] == manifest["appVersion"]
                        assert record["source_fingerprint"] == manifest["backendSourceFingerprint"]
                        launch_ids.add(record["desktop_launch_id"])
                assert len(launch_ids) == 1 and None not in launch_ids, "DESKTOP_SMOKE_LOG_LAUNCH_IDENTITY_MISMATCH"
                mode = ", parent output pipes closed" if closed_output_pipes else ""
                print("Bundled desktop passed: isolated profile, restricted PATH, authenticated renderer API, projects/sessions/settings, restored agent workspace, UI screenshot" + mode)
            except Exception:
                print((profile / "desktop.log").read_text(errors="replace")[-4000:], file=sys.stderr)
                raise
            finally:
                if browser_url:
                    try:
                        with connect(browser_url, open_timeout=2) as channel:
                            channel.send(json.dumps({"id": 1, "method": "Browser.close"}))
                        process.wait(timeout=8)
                    except Exception:
                        pass
                job.terminate()
                job.close()
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", type=Path, default=REPO / ".forgex/installers/win-unpacked/ForgeX.exe")
    parser.add_argument("--screenshot", type=Path, default=REPO / "test-results/packaged-desktop.png")
    parser.add_argument("--closed-output-pipes", action="store_true")
    parser.add_argument("--isolated-ports", action="store_true")
    parser.add_argument("--renderer-crashes", action="store_true")
    parser.add_argument("--redesign", action="store_true")
    args = parser.parse_args()
    smoke(args.executable, args.screenshot, closed_output_pipes=args.closed_output_pipes,
          isolated_ports=args.isolated_ports, renderer_crashes=args.renderer_crashes, redesign=args.redesign)
