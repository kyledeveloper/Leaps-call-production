---
name: self-toolmaker
description: AI Agent Self-Toolmaking and Script Assets Engine. Consult this skill to check available project-specific synthesized CLI tools in .agents/scripts/, or to synthesize new automated scripts when performing repetitive (>= 3 times) tasks.
user-invocable: true
---

# Self-Toolmaker: AI Agent Self-Toolmaking System

This skill allows the AI Agent to evolve into a **Toolmaker** rather than just a tool user.
When project-specific tasks are performed repeatedly, the agent encapsulates them into permanent, reliable, tested CLI tools.

---

## 1. Using Existing Synthesized Tools

Before executing multi-step ad-hoc shell commands, **always check if a project-specific tool already exists**:

Check registered tools:
```bash
node .agents/scripts/runner.js --list
```

Run a tool:
```bash
node .agents/scripts/runner.js <tool-name> [options]
```

### Currently Available Project Tools:
- **`audit-locales`**: Audit and compare keys across locale files (e.g., `zh-CN` vs `en-US`) to detect missing or extra translation keys.
  - Usage: `node .agents/scripts/runner.js audit-locales [--base locales/zh-CN] [--target locales/en-US] [--json]`
- **`pre-push-check`**: Security & quality gatekeeper running before `git push` to intercept hardcoded secrets, flag high-risk CVEs, and alert on code smell/complexity.
  - Usage: `node .agents/scripts/runner.js pre-push-check [--lang zh-CN|en-US] [--strict] [--json]`
- **`blast-radius`**: Code Symbol Graph & Blast-Radius analysis to evaluate impact scope, downstream modules, and affected tests before refactoring.
  - Usage: `node .agents/scripts/runner.js blast-radius --target <fileOrSymbol> [--tree] [--json] [--lang zh-CN|en-US]`

---

## 2. When to Synthesize New Tools

Synthesize a tool when any of the following conditions occur:
1. You or the user perform the same multi-step data processing or code conversion task for the **3rd time**;
2. A complex one-liner regex, jq, or AST manipulation command is frequently needed;
3. A repetitive project health-check or build verification step is established.

---

## 3. How to Synthesize and Register a New Tool

Use the Toolmaker engine in Node.js:
```javascript
const { getToolmaker } = require('./src/toolmaker/index');
const toolmaker = getToolmaker();

toolmaker.synthesizeTool({
  name: 'my-custom-tool',
  description: 'Concise explanation of what this tool does',
  parameters: [
    { name: 'input', description: 'Input file path', default: 'src/index.js' }
  ],
  codeBody: `
    // Standalone logic using args.<param>
    console.log("Executed with:", args.input);
  `,
  examples: ['--input src/main.js']
});
```

This automatically:
1. Generates `.agents/scripts/<name>.js` with `--help`, parameter parsing, and error handling.
2. Validates syntax with `node -c`.
3. Registers it into `.agents/scripts/registry.json`.
4. Makes it immediately callable via `node .agents/scripts/runner.js <name>`.
