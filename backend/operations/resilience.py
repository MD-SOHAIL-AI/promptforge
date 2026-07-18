"""Fail-safe operational controls with bounded, secret-free diagnostics."""
from __future__ import annotations
import asyncio,hashlib,json,os,platform,shutil,sqlite3,threading,time
from collections import Counter,defaultdict,deque
from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Any,Awaitable,Callable,Mapping
from backend.state.domain_persistence import CorruptDatabase,DomainDatabase,_now

_SECRET_KEYS=("api_key","apikey","authorization","credential","password","secret","token","auth_output","prompt","response","source","environment")
_SECRET_VALUES=("-----begin private key-----","authorization: bearer","api_key=","password=","secret=","token=")
MAX_DIAGNOSTIC_TEXT=256
class BudgetExceeded(RuntimeError):code="BUDGET_EXCEEDED"
class RateLimitExceeded(RuntimeError):code="RATE_LIMITED"
class CircuitOpen(RuntimeError):code="PROVIDER_CIRCUIT_OPEN"
class ProviderTimedOut(RuntimeError):code="PROVIDER_TIMEOUT"

@dataclass(frozen=True,slots=True)
class BudgetLimits:
 max_seconds:float=180.0;max_steps:int=20;max_input_tokens:int=100_000;max_output_tokens:int=20_000;max_cost_micros:int=500_000
class BudgetTracker:
 def __init__(self,limits:BudgetLimits,*,started:float|None=None):self.limits=limits;self.started=time.monotonic() if started is None else started;self.steps=self.input_tokens=self.output_tokens=self.cost_micros=0;self._lock=threading.Lock()
 def consume(self,*,steps=0,input_tokens=0,output_tokens=0,cost_micros=0,now:float|None=None):
  values=(steps,input_tokens,output_tokens,cost_micros)
  if any(not isinstance(x,int) or isinstance(x,bool) or x<0 for x in values):raise ValueError("budget usage must be non-negative integers")
  with self._lock:
   elapsed=(time.monotonic() if now is None else now)-self.started
   candidate=(self.steps+steps,self.input_tokens+input_tokens,self.output_tokens+output_tokens,self.cost_micros+cost_micros)
   if elapsed>self.limits.max_seconds or candidate[0]>self.limits.max_steps or candidate[1]>self.limits.max_input_tokens or candidate[2]>self.limits.max_output_tokens or candidate[3]>self.limits.max_cost_micros:raise BudgetExceeded("operation budget exceeded")
   self.steps,self.input_tokens,self.output_tokens,self.cost_micros=candidate
 def snapshot(self):return {"steps":self.steps,"input_tokens":self.input_tokens,"output_tokens":self.output_tokens,"cost_micros":self.cost_micros,"limits":{"max_seconds":self.limits.max_seconds,"max_steps":self.limits.max_steps,"max_input_tokens":self.limits.max_input_tokens,"max_output_tokens":self.limits.max_output_tokens,"max_cost_micros":self.limits.max_cost_micros}}

class SlidingWindowRateLimiter:
 def __init__(self,limit:int=30,window_seconds:float=60,max_scopes:int=256):
  if limit<1 or window_seconds<=0 or max_scopes<1:raise ValueError("invalid rate limit")
  self.limit,self.window_seconds,self.max_scopes=limit,window_seconds,max_scopes;self._hits:dict[str,deque[float]]={};self._lock=threading.Lock()
 def acquire(self,scope:str,*,now:float|None=None):
  stamp=time.monotonic() if now is None else now
  with self._lock:
   if scope not in self._hits and len(self._hits)>=self.max_scopes:
    oldest=min(self._hits,key=lambda key:self._hits[key][-1] if self._hits[key] else 0);self._hits.pop(oldest,None)
   hits=self._hits.setdefault(scope,deque())
   while hits and hits[0]<=stamp-self.window_seconds:hits.popleft()
   if len(hits)>=self.limit:raise RateLimitExceeded("provider rate limit exceeded")
   hits.append(stamp)

@dataclass(slots=True)
class Circuit:
 failures:int=0;state:str="closed";opened_at:float|None=None;successes:int=0
class ProviderCircuitBreakers:
 def __init__(self,failure_threshold:int=3,recovery_seconds:float=30):
  self.failure_threshold,self.recovery_seconds=failure_threshold,recovery_seconds;self._items:dict[str,Circuit]={};self._lock=threading.Lock()
 def before(self,provider_id:str,*,now:float|None=None):
  stamp=time.monotonic() if now is None else now
  with self._lock:
   item=self._items.setdefault(provider_id,Circuit())
   if item.state=="open":
    if item.opened_at is not None and stamp-item.opened_at>=self.recovery_seconds:item.state="half_open"
    else:raise CircuitOpen("provider circuit breaker is open")
   return item.state
 def success(self,provider_id:str):
  with self._lock:self._items[provider_id]=Circuit(successes=self._items.get(provider_id,Circuit()).successes+1)
 def failure(self,provider_id:str,*,now:float|None=None):
  stamp=time.monotonic() if now is None else now
  with self._lock:
   item=self._items.setdefault(provider_id,Circuit());item.failures+=1
   if item.state=="half_open" or item.failures>=self.failure_threshold:item.state="open";item.opened_at=stamp
 def snapshot(self): 
  with self._lock:return {key:{"state":value.state,"failures":value.failures,"successes":value.successes} for key,value in sorted(self._items.items())}

class SafeOperationalMetrics:
 ALLOWED=frozenset({"provider_calls","provider_timeouts","provider_failures","circuit_open","rate_limited","budget_denied","diagnostic_exports","database_backups","database_recoveries","reconciled_runs","websocket_resyncs","cancellations","device_disconnects"})
 def __init__(self):self._counts=Counter();self._lock=threading.Lock()
 def increment(self,name:str,amount:int=1):
  if name not in self.ALLOWED or not isinstance(amount,int) or amount<0:raise ValueError("unsafe metric")
  with self._lock:self._counts[name]+=amount
 def safe_increment(self,name:str,amount:int=1):
  try:self.increment(name,amount)
  except Exception:pass
 def snapshot(self):
  with self._lock:return {name:int(self._counts.get(name,0)) for name in sorted(self.ALLOWED)}

class ProviderResilience:
 def __init__(self,*,timeout_seconds=180.0,rate_limiter=None,circuits=None,metrics=None):
  self.timeout_seconds=timeout_seconds;self.rate_limiter=rate_limiter or SlidingWindowRateLimiter();self.circuits=circuits or ProviderCircuitBreakers();self.metrics=metrics or SafeOperationalMetrics()
 async def execute(self,provider_id:str,operation:Callable[[],Awaitable[Any]],*,timeout_seconds:float|None=None,budget:BudgetTracker|None=None):
  try:self.rate_limiter.acquire(provider_id)
  except RateLimitExceeded:self._metric("rate_limited");raise
  try:self.circuits.before(provider_id)
  except CircuitOpen:self._metric("circuit_open");raise
  self._metric("provider_calls")
  try:result=await asyncio.wait_for(operation(),timeout=timeout_seconds or self.timeout_seconds)
  except asyncio.TimeoutError as exc:self.circuits.failure(provider_id);self._metric("provider_timeouts");raise ProviderTimedOut("provider timed out") from exc
  except asyncio.CancelledError:self._metric("cancellations");raise
  except Exception:self.circuits.failure(provider_id);self._metric("provider_failures");raise
  self.circuits.success(provider_id);return result
 def _metric(self,name):
  try:self.metrics.safe_increment(name)
  except Exception:pass

@dataclass(frozen=True,slots=True)
class RetentionPolicy:
 command_days:int=30;published_outbox_days:int=7;usage_days:int=365;backup_count:int=7
class DatabaseMaintenance:
 def __init__(self,database:DomainDatabase,backup_dir:Path,*,policy:RetentionPolicy=RetentionPolicy()):self.database=database;self.backup_dir=backup_dir;self.policy=policy
 def backup(self)->Path:
  self.database.initialize();self.backup_dir.mkdir(parents=True,exist_ok=True);stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ");target=self.backup_dir/f"domain-state-{stamp}.db";temporary=target.with_suffix(".tmp")
  source=self.database.connect()
  try:
   destination=sqlite3.connect(temporary)
   try:
    source.backup(destination)
    if destination.execute("PRAGMA quick_check").fetchone()[0]!="ok":raise CorruptDatabase("backup integrity check failed")
   finally:destination.close()
   os.replace(temporary,target)
  finally:
   source.close()
   if temporary.exists():temporary.unlink(missing_ok=True)
  self._trim_backups();return target
 def recover_if_corrupt(self)->bool:
  try:self.database.initialize();return False
  except CorruptDatabase:
   backups=sorted(self.backup_dir.glob("domain-state-*.db"),reverse=True)
   for candidate in backups:
    try:
     probe=sqlite3.connect(candidate.resolve().as_uri()+"?mode=ro",uri=True)
     ok=probe.execute("PRAGMA quick_check").fetchone()[0]=="ok";probe.close()
     if not ok:continue
     corrupt=self.database.path.with_name(f"{self.database.path.name}.corrupt-{int(time.time())}")
     if self.database.path.exists():os.replace(self.database.path,corrupt)
     shutil.copy2(candidate,self.database.path);self.database.initialize();return True
    except Exception:continue
   raise CorruptDatabase("database is corrupt and no verified backup is recoverable")
 def retain(self)->dict[str,int]:
  now=datetime.now(timezone.utc);cut=lambda days:(now-timedelta(days=days)).isoformat().replace("+00:00","Z");removed={}
  with self.database.transaction() as db:
   removed["commands"]=db.execute("DELETE FROM control_plane_commands WHERE created_at<?",(cut(self.policy.command_days),)).rowcount
   removed["outbox"]=db.execute("DELETE FROM workflow_event_outbox WHERE published_at IS NOT NULL AND published_at<?",(cut(self.policy.published_outbox_days),)).rowcount
   removed["usage"]=db.execute("DELETE FROM usage_records WHERE created_at<?",(cut(self.policy.usage_days),)).rowcount
  return removed
 def _trim_backups(self):
  for path in sorted(self.backup_dir.glob("domain-state-*.db"),reverse=True)[self.policy.backup_count:]:path.unlink(missing_ok=True)

class StartupReconciler:
 def __init__(self,database,maintenance,machine,metrics):self.database=database;self.maintenance=maintenance;self.machine=machine;self.metrics=metrics;self.last_report={}
 def run(self,*,stale_after_seconds=900):
  recovered=self.maintenance.recover_if_corrupt();now=_now()
  with self.database.transaction() as db:
   expired_approvals=db.execute("UPDATE approvals SET status='expired',resolved_at=? WHERE status='pending' AND json_extract(payload_json,'$.expires_at') IS NOT NULL AND json_extract(payload_json,'$.expires_at')<=?",(now,now)).rowcount
   expired_leases=db.execute("DELETE FROM operation_leases WHERE expires_at<=?",(now,)).rowcount
  stale=self.machine.recover_stale(older_than=datetime.now(timezone.utc)-timedelta(seconds=stale_after_seconds));retention=self.maintenance.retain();backup=self.maintenance.backup();self.metrics.safe_increment("database_backups");self.metrics.safe_increment("database_recoveries",int(recovered));self.metrics.safe_increment("reconciled_runs",len(stale))
  self.last_report={"database_recovered":recovered,"expired_approvals":expired_approvals,"expired_leases":expired_leases,"stale_runs":len(stale),"retention":retention,"backup":backup.name,"completed_at":_now()};return dict(self.last_report)

class DiagnosticExporter:
 def __init__(self,*,database,metrics,circuits,runtime_logger,connection_registry,reconciler):self.database=database;self.metrics=metrics;self.circuits=circuits;self.runtime_logger=runtime_logger;self.connection_registry=connection_registry;self.reconciler=reconciler
 def export(self):
  try:logs=json.loads(self.runtime_logger.export_logs())
  except Exception:logs=[]
  log_counts=Counter((str(item.get("log_type","unknown")),str(item.get("level","unknown"))) for item in logs if isinstance(item,dict))
  with self.database.connect() as db:
   tables=("workflow_runs","workflow_events","approvals","artifacts","usage_records");counts={table:db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables};schema=db.execute("PRAGMA user_version").fetchone()[0];journal=db.execute("PRAGMA journal_mode").fetchone()[0]
  result={"schema_version":1,"generated_at":_now(),"runtime":{"python":platform.python_version(),"platform":platform.system()},"database":{"schema_version":schema,"journal_mode":journal,"counts":counts},"metrics":self.metrics.snapshot(),"circuits":self.circuits.snapshot(),"connections":self.connection_registry.safe_diagnostics(),"logs":{"counts":{f"{key[0]}.{key[1]}":value for key,value in sorted(log_counts.items())},"raw_logs_included":False},"startup_reconciliation":dict(self.reconciler.last_report),"exclusions":["credentials","raw_auth_output","secret_environment_values","unrestricted_source","raw_prompts","raw_responses","unbounded_logs"]}
  return sanitize(result)
def sanitize(value,key="",depth=0):
 if depth>8:return "[TRUNCATED]"
 folded=key.casefold()
 if any(marker in folded for marker in _SECRET_KEYS):return "[REDACTED]"
 if value is None or isinstance(value,(bool,int,float)):return value
 if isinstance(value,str):
  if any(marker in value.casefold() for marker in _SECRET_VALUES):return "[REDACTED]"
  return value[:MAX_DIAGNOSTIC_TEXT]
 if isinstance(value,Mapping):return {str(k):sanitize(v,str(k),depth+1) for k,v in value.items()}
 if isinstance(value,(list,tuple)):return [sanitize(x,"",depth+1) for x in value[:100]]
 return str(type(value).__name__)
