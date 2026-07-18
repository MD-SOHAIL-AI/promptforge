"""Durable workflow projection, outbox polling, and bounded live fan-out."""
from __future__ import annotations
import asyncio
from collections import defaultdict
from typing import Any
from .domain_persistence import CorruptDatabase,DomainDatabase,_decode,_now

RESYNC={"control":"resync_required"}
class WorkflowProjectionRepository:
 def __init__(self,database:DomainDatabase): self.database=database;database.initialize()
 def projection(self,run_id:str,*,after_sequence:int=0)->dict[str,Any]:
  if after_sequence<0: raise ValueError("after_sequence must be non-negative")
  with self.database.connect() as db:
   run=db.execute("SELECT status,version,safe_summary,failure_code,created_at,updated_at FROM workflow_runs WHERE run_id=?",(run_id,)).fetchone()
   if not run: raise KeyError(run_id)
   events=[_decode(r[0]) for r in db.execute("SELECT payload_json FROM workflow_events WHERE run_id=? AND sequence>? ORDER BY sequence",(run_id,after_sequence))]
   messages=[dict(r) for r in db.execute("SELECT message_id,event_sequence,role,content,created_at FROM conversation_messages WHERE run_id=? ORDER BY event_sequence",(run_id,))]
  return {"schema_version":1,"run":{"run_id":run_id,"status":run[0],"version":run[1],"safe_summary":run[2],"failure_code":run[3],"created_at":run[4],"updated_at":run[5]},"events":events,"messages":messages,"last_sequence":events[-1]["sequence"] if events else after_sequence}

class DurableWorkflowEventHub:
 def __init__(self,database:DomainDatabase,*,queue_size:int=64,poll_interval:float=.1,metrics:Any|None=None):
  self.database=database; self.projections=WorkflowProjectionRepository(database); self.queue_size=queue_size; self.poll_interval=poll_interval; self.metrics=metrics
  self._subscribers:dict[str,set[asyncio.Queue[dict[str,Any]]]]=defaultdict(set); self._task:asyncio.Task|None=None; self._closed=False; self._wake=asyncio.Event()
 async def start(self)->None:
  if not self._task or self._task.done(): self._closed=False;self._task=asyncio.create_task(self._pump())
 async def close(self)->None:
  self._closed=True;self._wake.set()
  if self._task: self._task.cancel();await asyncio.gather(self._task,return_exceptions=True);self._task=None
 async def subscribe(self,run_id:str,*,after_sequence:int=0)->tuple[asyncio.Queue[dict[str,Any]],list[dict[str,Any]]]:
  await self.start(); queue:asyncio.Queue[dict[str,Any]]=asyncio.Queue(maxsize=self.queue_size);self._subscribers[run_id].add(queue)
  replay=self.projections.projection(run_id,after_sequence=after_sequence)["events"]
  return queue,replay
 async def unsubscribe(self,run_id:str,queue:asyncio.Queue)->None:
  self._subscribers[run_id].discard(queue)
  if not self._subscribers[run_id]:self._subscribers.pop(run_id,None)
 def notify(self)->None:self._wake.set()
 async def publish_pending(self,*,limit:int=100)->int:
  with self.database.transaction() as db:
   rows=db.execute("SELECT outbox_id,run_id,payload_json FROM workflow_event_outbox WHERE published_at IS NULL ORDER BY outbox_id LIMIT ?",(limit,)).fetchall()
   for row in rows:
    event=_decode(row[2])
    for queue in tuple(self._subscribers.get(row[1],())):
     try:queue.put_nowait(event)
     except asyncio.QueueFull:
      if self.metrics is not None: self.metrics.safe_increment("websocket_resyncs")
      self._subscribers[row[1]].discard(queue)
      try:queue.get_nowait()
      except asyncio.QueueEmpty:pass
      try:queue.put_nowait(RESYNC)
      except asyncio.QueueFull:pass
    db.execute("UPDATE workflow_event_outbox SET published_at=?,publish_attempts=publish_attempts+1 WHERE outbox_id=?",(_now(),row[0]))
  return len(rows)
 async def _pump(self)->None:
  while not self._closed:
   try:
    count=await self.publish_pending()
    if count:continue
    self._wake.clear()
    try:await asyncio.wait_for(self._wake.wait(),timeout=self.poll_interval)
    except asyncio.TimeoutError:pass
   except asyncio.CancelledError:raise
   except Exception:
    await asyncio.sleep(min(1.0,self.poll_interval*5))
