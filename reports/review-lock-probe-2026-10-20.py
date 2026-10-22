"""Independent Windows cross-process lock qualification probe."""
import sys,os,time,json,multiprocessing
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ["LOCALAPPDATA"]=str(Path.cwd()/".cache/review-localappdata")
from backend.security.workspace_lock import workspace_lock,WorkspaceLockTimeout

def hold(path,acquired,release):
    with workspace_lock(path,timeout=10):
        acquired.set(); release.wait(20)

if __name__ == "__main__":
    ctx=multiprocessing.get_context("spawn"); acquired=ctx.Event();release=ctx.Event()
    child=ctx.Process(target=hold,args=(str(Path.cwd()/".cache/review-lock-target"),acquired,release))
    started=time.monotonic();child.start();exclusion=False;ready=acquired.wait(15)
    elapsed=time.monotonic()-started
    try:
        if ready:
            try:
                with workspace_lock(Path.cwd()/".cache/review-lock-target",timeout=.2):pass
            except WorkspaceLockTimeout:exclusion=True
    finally:
        release.set();child.join(15)
        if child.is_alive():child.terminate();child.join(5)
    output={"child_acquired":ready,"child_acquisition_seconds":round(elapsed,3),"competing_writer_excluded":exclusion,"child_exit_code":child.exitcode}
    print(json.dumps(output,indent=2));Path("reports/current-review-lock-probe.json").write_text(json.dumps(output,indent=2))
