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

- Face swapper model families must declare their full runtime contract, not just
  their ONNX loading path. The contract includes source input preparation,
  target crop template and size, output normalization, and paste-back/masking
  behavior.
- Do not route a new face swapper family through INSwapper-specific assumptions
  unless the model is actually compatible with them. In particular, INSwapper's
  central elliptical paste-back mask is not a safe default for wider crop-based
  swappers such as Hyperswap; those adapters should opt into an explicit crop
  paste-back path.
- When adding a model adapter that cannot be visually validated in the current
  environment, add low-frequency runtime diagnostics that distinguish "model
  output did not change" from "model output changed but compositing hid it".

---

## Testing Requirements

<!-- What level of testing is expected -->

- Add at least a focused regression test for model family routing and adapter
  flags whenever a new swapper family is introduced.
- Run the face swapper unit tests plus `py_compile` on changed processor modules
  before reporting a model-adapter change as ready.

---

## Code Review Checklist

<!-- What reviewers should check -->

(To be filled by the team)
