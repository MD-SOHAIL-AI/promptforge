"""Deterministic, token-aware summaries with retrievable evidence references."""
import json
import hashlib


def detailed_tail(observations, *, keep=24, char_budget=32000):
    used=0
    count=0
    for item in reversed(observations[-keep:]):
        size=len(json.dumps(item,default=str,ensure_ascii=False))
        if count and used+size>char_budget:
            break
        used+=size
        count+=1
    return max(1,count)


def summarize_observations(observations):
    discoveries=[]
    unsuccessful=[]
    for item in observations:
        output=item.get("output")
        ref={"call_id":item.get("call_id"),"tool":item.get("tool"),"status":item.get("status")}
        if item.get("status") in {"failed","denied"}:
            unsuccessful.append(ref | {"error":str(item.get("error") or (output or {}).get("error", "") if isinstance(output,dict) else item.get("error", ""))[:500]})
        elif isinstance(output,dict):
            facts={key:output[key] if isinstance(output[key],(bool,int,float,type(None))) else str(output[key])[:800] for key in ("path","environment","board","success","source_revision","workspace_revision","firmware_hash","missing","findings","verification_level") if key in output}
            if facts:
                discoveries.append(ref | {"facts":facts})
    return {"verified_discoveries":discoveries[-20:],"unsuccessful_approaches":unsuccessful[-10:],
            "retrieval":"session_event_search", "observation_digest":hashlib.sha256(json.dumps(observations,sort_keys=True,default=str).encode()).hexdigest()}


def bounded_observation(item, *, max_chars=16000):
    encoded=json.dumps(item,default=str,ensure_ascii=False)
    if len(encoded)<=max_chars:
        return item
    return {"call_id":item.get("call_id"),"tool":item.get("tool"),"status":item.get("status"),
            "output":{"preview":encoded[:max_chars-500],"truncated":True,
                      "retrieval":"session_event_search","original_call_id":item.get("call_id")}}
