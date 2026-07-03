# Implementation Plan: Window Capture FPS Improvements

## Checklist

1. Load backend coding specs before editing.
2. Inspect current live preview and window-capture code paths.
3. Add WGC sequence tracking:
   - frame sequence counter incremented in the callback
   - last-read sequence tracking in `read()`
   - bounded wait for a new frame
   - no duplicate-frame success return unless falling back to last frame on
     error/timeout
4. Remove avoidable WGC copy in `read()` while preserving owned callback frame
   memory.
5. Add raw capture FPS measurement and backend/dimension logging.
6. Improve pywin32 fallback:
   - identify active fallback in logs/status
   - reuse or centralize resource lifecycle if low-risk
   - preserve resize handling and cleanup
7. Add or update tests where practical for non-Windows-safe logic.
8. Run validation:
   - `python3 -m compileall modules tests`
   - `python3 -m pytest`
   - targeted manual check on Windows if available

## Risky Areas

- Returning ndarray references from WGC must remain safe: callback frames are
  replaced, not mutated.
- Win32 DC/bitmap reuse can leak handles if cleanup is wrong.
- Overly long WGC waits can stall preview shutdown; waits should be bounded and
  respect the capture worker stop loop.
- Changing FPS overlay semantics can confuse users if processed FPS and capture
  FPS are not clearly separated.

## Review Gates Before `task.py start`

- Runtime scope confirmed: Windows WGC/pywin32 path.
- User approves proceeding from planning to implementation.
