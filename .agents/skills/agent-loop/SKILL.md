---
name: agent-loop
description: Default host entry for any coding task. REQUIRED before editing src/. Run `node src/agent/index.js plan "<intent>" --target <file>` to retrieve reflexion memory, suggest tools, and compute blast-radius (auto --diff --semantic if the target is dirty). Use on refactor, fix, implement, or tool execution.
user-invocable: true
---

# Unified Agent Loop

This is the **single entry point** that turns the five pillars into one pipeline:

```
intent → reflexion memory → tool suggestion → (optional blast-radius) → execute → record failure
```

Do **not** call memory, toolmaker, or blast-radius as disconnected one-offs for a real task. Route through this loop first.

---

## 1. Plan before acting

```bash
node src/agent/index.js plan "<task intent>"
```

Refactoring a module? Always pass `--target` so blast-radius is attached. If that file is dirty, the loop turns on `--diff --semantic` by itself.

```bash
node src/agent/index.js plan "refactor MemoryDatabase" --target src/memory/db.js
```

If `Blast radius` reports semantic BREAKING, stop and tell the user. Do not keep editing.

If Historical Reflexion Guidance is returned, **follow the heuristic** before running hazardous commands.

---

## 2. Run a synthesized tool through the loop

```bash
node src/agent/index.js run "audit locale keys" --tool audit-locales --exec
node src/agent/index.js run "pre-push security scan" --tool pre-push-check --exec -- --json
```

Arguments after `--` are passed to the tool as an argv array (no shell interpolation).

`--exec` without `--tool` runs the tool only when **exactly one** suggestion matches.

A non-zero exit is **not** written to memory by default. Opt in with `--record-failure`, then overwrite the stub via `reflect` once you know the root cause. Never copy an unrelated prior lesson into the heuristic.

---

## 3. Record a diagnosed lesson

```bash
node src/agent/index.js reflect \
  --intent "Install sqlite native addon in sandbox" \
  --trigger "npm install better-sqlite3" \
  --cause "Sandbox blocks node-gyp" \
  --heuristic "Do not compile native addons inside the sandbox; use npx or the user terminal"
```

---

## 4. Programmatic API

```javascript
const { AgentLoop } = require('./src/agent/index');

const agent = new AgentLoop();
const report = agent.run('audit locale keys', {
  tool: 'audit-locales',
  args: ['--json']
});
// report.guidance  — memory to inject into the next LLM turn
// report.result    — { ok, status, stdout, stderr }
// report.recorded  — failure reflection, if any
agent.close();
```

---

## 5. When NOT to use this loop

- Pure Q&A with no command execution
- Editing a single comment or string where blast-radius and memory cannot help
