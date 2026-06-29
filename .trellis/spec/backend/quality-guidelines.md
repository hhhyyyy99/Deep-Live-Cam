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

---

## Testing Requirements

<!-- What level of testing is expected -->

- Model selector tests must cover both discovery filtering and persisted
  unsupported-model fallback behavior.

---

## Code Review Checklist

<!-- What reviewers should check -->

(To be filled by the team)
