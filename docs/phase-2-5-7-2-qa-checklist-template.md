# Phase 2.5.7.2 QA Checklist Template

Use only a throwaway workspace. Record each result as `PASS`, `FAIL`, `BLOCKED`, or `NOT TESTED`.

## Session

- Date:
- Operator:
- ForgeX command:
- Workspace:
- Backend URL:
- Frontend URL:
- Electron status:
- Browser fallback used:

## Checklist

### 1. Apply Disabled By Default

Result:
Notes:
Screenshot:

### 2. Apply Requires Both Flags

Result:
Notes:
Screenshot:

### 3. Modify Apply + Rollback

Result:
Notes:
Screenshot:

### 4. Create Apply + Rollback

Result:
Notes:
Screenshot:

### 5. Delete Apply + Rollback

Result:
Notes:
Screenshot:

### 6. Drift Blocked

Result:
Notes:
Screenshot:

### 7. Corrupt Patch Blocked

Result:
Notes:
Screenshot:

### 8. Audit Safety

Result:
Notes:
Screenshot:

Confirm audit logs do not include raw workspace absolute paths, patch content, file content, tokens, cookies, session data, or Google credentials.

### 9. Apply History Readability

Result:
Notes:
Screenshot:

### 10. Apply Detail Readability

Result:
Notes:
Screenshot:

## Final Safety Statement

Apply remains feature-flagged.
Restore remains feature-flagged.
No bridge routing was added.
No Codex/Claude execution was added.
No auto-build/auto-flash was added.
