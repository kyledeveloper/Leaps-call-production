---
name: code-graph
description: REQUIRED before editing src/, refactoring, renaming, or changing exported signatures. Computes blast-radius. Hosts should call `node src/agent/index.js plan "<intent>" --target <file>` (auto --diff --semantic when the file is dirty). Use on any change/fix/refactor of project code.
user-invocable: true
---

# Code Symbol Graph & Blast-Radius Analysis

This skill equips the AI Agent with deep architectural awareness of symbol dependencies and ripple effects before executing code changes.

---

## 1. When to Analyze Blast Radius

Run blast-radius analysis **prior to writing code** whenever:
1. Refactoring an existing function, class, or module under `src/`;
2. Renaming, removing, or changing the parameter signature of an exported API;
3. Investigating which test suites must be executed after modifying a dependency;
4. Assessing technical debt or coupling between project components.

---

## 2. CLI Tool Usage

**Host default (preferred):** one command, flags chosen automatically.

```bash
node src/agent/index.js plan "refactor MemoryDatabase" --target src/memory/db.js
```

If the target file already has uncommitted changes, that plan run enables `--diff --semantic`. On a clean tree it reports full downstream topology (do not pass `--diff` before the first edit — an empty diff looks CLEAN and under-reports).

Execute via the unified project runner when you need the raw tool:

### File-Level Analysis
```bash
# After edits exist: diff-aware + AST contract
node .agents/scripts/runner.js blast-radius --target src/memory/db.js --diff --semantic

# Clean tree / pre-edit topology
node .agents/scripts/runner.js blast-radius --target src/memory/db.js --tree
```

### Symbol-Level Analysis
```bash
# Analyze impact of modifying a specific function or class
node .agents/scripts/runner.js blast-radius --target MemoryDatabase --tree
node .agents/scripts/runner.js blast-radius --target scanSecrets --json
```

---

## 3. Risk Levels & Safety Guidelines

| Risk Level | Score Range | Criteria & Implications | Required Action |
| :--- | :--- | :--- | :--- |
| **🟢 LOW** | $< 30$ | Isolated leaf module, no downstream callers or 1 test. | Run affected test suite; proceed with standard Red-Green-Refactor. |
| **🟡 MEDIUM** | $30 - 69$ | Multiple direct caller modules or several test suites impacted. | Inspect call-sites before modifying; execute all listed test suites. |
| **🔴 HIGH** | $\ge 70$ | Core architectural infrastructure with wide ripple effects. | Preserve backward compatibility (aliases/shims); run full regression test (`npm test`). |

---

## 4. Programmatic API (Inside Node.js)

```javascript
const { calculateBlastRadius } = require('./src/graph/blastRadius');

const report = calculateBlastRadius('src/memory/db.js');
console.log(`Risk Score: ${report.riskScore}, Level: ${report.riskLevel}`);
console.log('Impacted Tests:', report.impactedTests);
```
