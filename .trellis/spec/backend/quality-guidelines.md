# Quality Guidelines

> Code quality standards for backend development.

---

## Overview

<!--
Document your project's quality standards here.

Questions to answer:
- What patterns are forbidden?
- What linting rules do you enforce?
- What are your testing requirements?
- What code review standards apply?
-->

(To be filled by the team)

---

## Forbidden Patterns

<!-- Patterns that should never be used and why -->

(To be filled by the team)

---

## Required Patterns

<!-- Patterns that must always be used -->

- Face swapper model discovery must list only model families supported by the
  current loading path. At the moment the primary swapper path uses
  `insightface.model_zoo.get_model()`, so selectable primary models must match
  `inswapper*.onnx`.
- Persisted model selections from unsupported ONNX families must be normalized
  back to Auto before loading. Do not repeatedly attempt to load known
  incompatible files such as `hyperswap_*.onnx` through the InsightFace loader.
- Live capture classes that implement the `VideoCapturer`-compatible interface
  must preserve `start()` / `read()` / `release()` semantics. For event-driven
  sources such as Windows Graphics Capture, `read()` must only return success
  for a new source frame. A temporary "no new frame yet" state should be
  distinguishable from a capture failure so UI worker loops do not either
  process duplicate frames or stop a healthy static capture session.
- Live capture diagnostics must distinguish source/capture FPS from processed
  preview FPS. The processing overlay must not imply that processed FPS is the
  raw capture rate.

---

## Testing Requirements

<!-- What level of testing is expected -->

- Model selector tests must cover both discovery filtering and persisted
  unsupported-model fallback behavior.
- Event-driven live capture changes must include a regression test proving that
  one source frame is not returned as multiple successful `read()` results and
  that capture FPS updates are observable independently from processed FPS.

---

## Code Review Checklist

<!-- What reviewers should check -->

(To be filled by the team)
