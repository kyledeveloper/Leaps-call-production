---
name: reflexion-memory
description: Long-Term Reflexive Memory system for the AI Agent. Consult this skill to recall past task trajectories, failure modes, root causes, and distilled corrective heuristics (avoiding repeating past mistakes), or to record new reflections after resolving errors.
user-invocable: true
---

# Long-Term Reflexive Memory

This skill equips the AI Agent with an episodic, self-improving memory mechanism based on the **Reflexion** architectural pattern.
Unlike static document RAG, this memory indexes **causal relationships, failure patterns, root causes, and corrective heuristics**.

---

## 1. When to Query Memory (Pre-Task Retrieval)

Before executing commands or plans involving:
- Package installations (`npm`, `pip`, binary toolchains)
- Native compilations (C++/Rust addons, `node-gyp`)
- Environment permissions and sandbox constraints
- Git and remote platform authentication
- Project-level vs. global configuration decisions

Run the memory search command to check for past lessons:
```bash
node src/memory/index.js search "<your task intent>"
```

### Multi-Dimensional Scoring
Retrieval employs the Generative Agents / MemGPT multi-dimensional scoring model:
$$\text{Final Score} = \alpha \cdot S_{\text{rel}} + \beta \cdot S_{\text{rec}} + \gamma \cdot S_{\text{imp}}$$
- **Relevance $S_{\text{rel}}$ ($\alpha = 0.5$)**: FTS5 BM25 + keyword matching + domain tag overlap.
- **Recency $S_{\text{rec}}$ ($\beta = 0.2$)**: Exponential decay $e^{-\lambda \Delta t}$ with 14-day half-life; hit retrieval refreshes activation timestamp.
- **Importance $S_{\text{imp}}$ ($\gamma = 0.3$)**: Inherent severity / importance rating (0.1~1.0) reinforced by log-scaled `hit_count`.

If relevant lessons are returned, **strictly follow the `Heuristic Advice (Corrective Heuristic)`** and proactively inform the user of the potential pitfall before running hazardous commands.

---

## 2. When to Record Memory (Post-Recovery Reflexion)

Whenever a command or tool call fails unexpectedly, and you diagnose the root cause and successfully resolve/recover from it:
**Do NOT leave the lesson unrecorded.**

Record the reflection by running Node script:
```javascript
const { getMemory } = require('./src/memory/index');
getMemory().recordExperience({
  intent: "<Original goal of the task>",
  context_summary: "<Environment details, OS, runtime version>",
  domain_tags: ["<tag1>", "<tag2>"], // e.g. ['npm', 'permissions', 'git']
  status: "recovered", // 'recovered' or 'failure'
  importance_score: 0.8, // 0.1~1.0: 0.95 for fatal crashes / EPERM, 0.6 for warnings
  trigger_pattern: "<Concrete operation or syntax that caused the issue>",
  failure_mode: "<Specific error message or unexpected symptom>",
  root_cause: "<The underlying reason why the error happened>",
  corrective_heuristic: "<Actionable heuristic guide for the future: what to do instead>"
});
```

---

## 3. Heuristic Distillation Standard

Good reflections are **concise, causal, and actionable**:
- ❌ **Poor Heuristic**: "Command failed with code 7. Tried sudo next." *(No causality, not actionable)*
- ✅ **Good Heuristic**: "When installing packages with C++ native extensions in IDE sandbox, node-gyp will fail with EPERM. Prefer `npx` or ask the user to run in their system terminal." *(Specific trigger, clear root cause, concrete alternative)*

---

## 4. Memory Consolidation (Promoting Experience to Permanent Rules)

Check for high-frequency memories:
```bash
node src/memory/index.js stats
```
When a reflection's `hit_count >= 3`, promote it to `AGENTS.md` as an authoritative project rule.
