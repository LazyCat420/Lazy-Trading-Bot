# Frontend Real-Time Tracking & Data Persistence Fix

> **Problem**: When the user starts "Run All Bots" on the Leaderboard tab and navigates away (e.g., to Settings, Data, or another browser tab), the frontend loses track of the run. Coming back shows no progress, even though the backend is still running.

---

## Root Cause Analysis

### How the Trading Bot Frontend Works

The app is a **React SPA** using `react-router-dom`. Each page (Dashboard, Monitor, Settings, Data, etc.) is a separate component that mounts/unmounts on navigation. The sidebar (`SidebarLayout`) re-renders for each page, but each page manages its own state independently.

All monitoring state lives in the `useMonitorData` hook (`terminal_app.js:3434`):

```
useState for: runAllRunning, runAllStatus, loopRunning, loopStatus,
              pipelineEvents, scores, portfolio, leaderboard, etc.
```

### The Three Bugs

#### Bug 1: `runAllRunning` is local React state — lost on navigation

When you click "Run All Bots" (line 3534):
1. `setRunAllRunning(true)` sets a local state flag
2. A `setInterval` poll starts at 3s intervals (line 3549)
3. The poll fetches `/api/bots/run-all/status` and updates `runAllStatus`

When you navigate away from the Monitor page:
1. Component unmounts → `return () => { clearInterval(interval) }` runs (line 3734-3737)
2. All `setInterval` polls are killed
3. All `useState` values (including `runAllRunning`) are garbage collected

When you navigate back:
1. Component re-mounts → `useEffect` runs (line 3720)
2. Mount effect checks `loop-status` (line 3725) — BUT **NOT** `run-all/status`
3. `runAllRunning` defaults to `false` → no poll resumes → run appears to have stopped

#### Bug 2: No recovery of Run All state on re-mount

The mount `useEffect` (line 3720-3738) does check if the autonomous loop is running:
```javascript
fetch("/api/bot/loop-status").then(r => r.json()).then(st => {
    if (st.running) {
        setLoopRunning(true);
        setLoopStatus(st);
        startLoopPoll();
    }
});
```

But there is **no equivalent check** for Run All:
```javascript
// Missing! Should be:
fetch("/api/bots/run-all/status").then(r => r.json()).then(st => {
    if (st.running) {
        setRunAllRunning(true);
        setRunAllStatus(st);
        // restart poll
    }
});
```

#### Bug 3: Pipeline events reset on remount

`pipelineEvents` starts as `[]` on every mount. The first `fetchAll()` call (line 3721) fetches events, but there's a 30s gap where the Activity Log shows empty before the next background fetch.

### How Retina Handles This (reference)

Retina's Live Activity page (`retina/src/app/admin/live/page.js`) polls at 2s and uses **fingerprinting** to avoid unnecessary re-renders:

```javascript
const fingerprint = convs
    .map(c => `${c.id}:${c.messageCount}:${c.isGenerating}`)
    .join("|");
if (fingerprint !== lastFingerprintRef.current) {
    // Only update state when data actually changed
}
```

Key difference: Retina's data source is **Prism's persistent MongoDB** — every page can independently re-fetch the same data. The trading bot's data source is **in-memory Python state** (event logs, run status), so the frontend must poll more carefully.

---

## Proposed Fixes

> All changes are in the Lazy Trading Bot only. **No changes to Prism/Retina.**

### Fix 1: Recover Run All state on re-mount
**File**: `app/static/terminal_app.js`
**Location**: `useMonitorData` hook mount `useEffect` (line 3720-3738)

Add a parallel check for `run-all/status` alongside the existing `loop-status` check:

```javascript
// Check if run-all was already running before we mounted
fetch("/api/bots/run-all/status").then(r => r.json()).then(st => {
    if (st.running) {
        console.log("[MonitorData] Run-All already running, resuming poll...");
        setRunAllRunning(true);
        setRunAllStatus(st);
        // Restart the run-all poll
        if (runAllPollRef.current) clearInterval(runAllPollRef.current);
        runAllPollRef.current = setInterval(async () => {
            // ... same poll logic as line 3549-3578
        }, 3000);
    }
}).catch(() => {});
```

### Fix 2: Add `visibilitychange` listener to pause/resume polling
**File**: `app/static/terminal_app.js`
**Location**: `useMonitorData` hook

When the browser tab is hidden, pause all `setInterval` calls. When visible again, do an immediate fetch + resume polling:

```javascript
useEffect(() => {
    const handleVisibility = () => {
        if (document.hidden) {
            // Pause the 30s fetchAll interval (save resources)
            clearInterval(bgInterval);
        } else {
            // Tab is visible again — immediate refresh + resume
            fetchAll();
            bgInterval = setInterval(fetchAll, 30000);
            // Also check if run-all or loop started/stopped while we were away
            fetch("/api/bots/run-all/status").then(r => r.json()).then(st => {
                if (st.running && !runAllRunning) { /* resume poll */ }
                else if (!st.running && runAllRunning) { /* stop poll, refresh */ }
            });
        }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
}, []);
```

### Fix 3: Extract `runAllRunning` to a module-level ref (survives remount)
**File**: `app/static/terminal_app.js`

Move the Run All running state to a module-level variable outside the React component so it persists across mount/unmount cycles:

```javascript
// Module-level (outside useMonitorData)
let _runAllRunning = false;
let _runAllPollInterval = null;
let _runAllStatus = null;

// Inside useMonitorData:
const [runAllRunning, setRunAllRunning] = useState(_runAllRunning);
const [runAllStatus, setRunAllStatus] = useState(_runAllStatus);

// Sync module-level on change
useEffect(() => { _runAllRunning = runAllRunning; }, [runAllRunning]);
useEffect(() => { _runAllStatus = runAllStatus; }, [runAllStatus]);
```

### Fix 4: Add run-all cleanup on unmount (with poll transfer)
**File**: `app/static/terminal_app.js`

Instead of killing the run-all poll on unmount, transfer it to module scope:

```javascript
return () => {
    clearInterval(bgInterval);  // kill the 30s refresh
    // Do NOT clear runAllPollRef — transfer to module level
    if (runAllPollRef.current && _runAllRunning) {
        _runAllPollInterval = runAllPollRef.current;
        // The interval continues running even after unmount
    }
};
```

---

## Checklist

- [ ] **Fix 1**: Add `run-all/status` check to mount `useEffect` in `useMonitorData`
- [ ] **Fix 2**: Add `visibilitychange` listener for tab pause/resume
- [ ] **Fix 3**: Extract `runAllRunning` + `runAllStatus` to module-level state
- [ ] **Fix 4**: Transfer run-all poll to module scope on unmount instead of killing it
- [ ] **Test**: Start Run All → navigate to Settings → navigate back → verify poll resumes
- [ ] **Test**: Start Run All → switch to different browser tab → come back → verify data updates
- [ ] **Test**: Start Run All → close and reopen the page → verify state recovery from backend

---

## Verification Plan

### Manual Testing
1. Start "Run All Bots" from the Leaderboard tab
2. While it's running, click on **Settings** in the sidebar
3. Click back to **Monitor** → verify the Run All progress bar/status is visible
4. Switch to a different browser tab for 30 seconds
5. Switch back → verify the Activity Log and Run All status update immediately
6. Refresh the page entirely (F5) → verify the Run All status recovers from the backend

### Behavioral Checks
- The 30s background fetch should pause when the tab is hidden (check DevTools Network tab)
- When returning to the tab, there should be an immediate fetch burst
- The Activity Log should not show a gap when navigating between pages
