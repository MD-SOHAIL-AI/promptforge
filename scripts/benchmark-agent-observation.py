"""Compare full replay with checkpoint observation using an isolated journal."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.agent_runtime.orchestration_store import AgentOrchestrationStore
from backend.agent_runtime.orchestrator import AgentOrchestrator
from backend.agent_runtime.projection import SessionProjectionEngine


def measure(events, repeats):
    with tempfile.TemporaryDirectory(prefix="forgex-observation-") as directory:
        store = AgentOrchestrationStore(Path(directory) / "journal.sqlite3")
        for number in range(events):
            store.append_event("benchmark", {"run_id":"run", "event_type":"model.attempt.settled", "content":f"settled {number}"})
        engine = SessionProjectionEngine()
        owner = SimpleNamespace(store=store, projections=engine, _lock=threading.RLock(),
            _session_for_run={}, _transient_events={})
        def replay():
            return engine.rebuild("benchmark", tuple(store.snapshot_events("benchmark"))).to_dict()
        def incremental():
            return AgentOrchestrator.session_projection(owner, "benchmark")
        expected = replay()
        assert incremental() == expected
        incremental()  # Warm the read cache after the checkpoint write.
        results = {}
        for name, callback in (("full_replay",replay), ("checkpoint_observation",incremental)):
            samples = []
            for _ in range(repeats):
                started = time.perf_counter()
                assert callback() == expected
                samples.append(1000 * (time.perf_counter() - started))
            results[name] = {"median_ms":round(statistics.median(samples),3), "max_ms":round(max(samples),3)}
        return {"schema_version":1, "events":events, "repeats":repeats, "timings":results,
            "median_speedup":round(results["full_replay"]["median_ms"] / max(.001,results["checkpoint_observation"]["median_ms"]),2),
            "scope":"Isolated local read observation; provider, leases under load and packaged renderer are separate qualification gates."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.events <= 100000 or not 1 <= args.repeats <= 1000:
        parser.error("events/repeats outside benchmark limits")
    print(json.dumps(measure(args.events,args.repeats),indent=2))
