"use client";

import { useEffect, useMemo, useState } from "react";
import { promptForgeApi } from "@/lib/api";
import type { ChangeSetResponse, ProductAgentRun } from "@/types";

export function useAgentChangeSet(run: ProductAgentRun | null) {
  const [changeSet, setChangeSet] = useState<ChangeSetResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!run?.run_id || !run.change_set_id) {
      setChangeSet(null);
      setLoading(false);
      return;
    }
    let alive = true;
    setChangeSet(null);
    setLoading(true);
    void promptForgeApi.agentRuntimeChanges(run.run_id)
      .then((result) => { if (alive) setChangeSet(result.change_set); })
      .catch(() => { if (alive) setChangeSet(null); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [run?.change_set_id, run?.run_id, run?.status, run?.classification]);

  const changedFileCount = useMemo(() => {
    if (changeSet) return changeSet.changed_files.length;
    if (!run) return 0;
    return run.created_file_count + run.modified_file_count + run.deleted_file_count;
  }, [changeSet, run]);

  return { changeSet, loading, changedFileCount };
}
