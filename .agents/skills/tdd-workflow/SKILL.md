---
name: tdd-workflow
description: Test-Driven Development (TDD) & Self-Healing Loop. Enforces the Red-Green-Refactor cycle for all new features, bug fixes, and refactoring tasks.
user-invocable: true
---

# TDD & Self-Healing Workflow (Test-Driven Development)

This skill operationalizes the **Red-Green-Refactor-SelfHeal** discipline into the agent's subconscious development loop.

---

## 1. Core Principles

1. **Test-First (Red)**: Never write functional or fix code before an automated test exists that reproduces the requirement or failure mode.
2. **Deterministic Green**: Implement the minimal, clean code necessary to turn the red test green.
3. **Self-Healing Loop**: If a test fails after implementation:
   - Capture the exact assertion error and stack trace.
   - Consult `reflexion-memory` for similar known historical failure modes.
   - Fix the underlying root cause (never modify the test to artificially pass unless requirements changed).
   - Re-run the test suite until 100% green.
4. **Offline & Token-Efficient Sandbox Execution**:
   - All tests execute locally within the secure sandbox without network calls.
   - Run tests directly via Node.js built-ins (`assert`, `node:test`) or project runners.
   - Silent success minimizes context token consumption; only fold and analyze errors upon failure.

---

## 2. Standard 4-Step Cycle

```
[1. RED]        Write failing boundary/unit test (assert failure / bug repro)
    │
    ▼
[2. GREEN]      Write minimal, clean production code to pass the test
    │
    ▼
[3. REFACTOR]   Clean up smells, optimize complexity (keep tests passing)
    │
    ▼
[4. HEAL/AUDIT] Run entire test suite + check pre-push gates before delivery
```

### Step 1: Red Phase
Create a test file in `test/` capturing edge cases, inputs, and expected outputs.
```javascript
const assert = require('assert');
// Expect failure before implementation exists
assert.strictEqual(myModule.solve(input), expectedOutput);
```
Run the test to confirm it fails for the expected reason (`code: 1` or `AssertionError`).

### Step 2: Green Phase
Implement the code under `src/` to satisfy the specification until the test passes with exit code 0.

### Step 3: Refactor & Code Smell Check
Ensure functions stay below 80 lines and nesting depth is kept at or below 4.
Confirm tests remain green after refactoring.

### Step 4: Verification & Git Commit
Run the full test suite (`npm test`).
Once all tests pass, stage and commit locally:
```bash
git add <files>
git commit -m "feat/fix: <descriptive message>"
```
*(Remember: Never push automatically. The pre-push security gatekeeper will run when the user executes `git push`.)*
