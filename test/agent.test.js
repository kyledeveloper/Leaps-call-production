const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');
const { LongTermMemory } = require('../src/memory/index');
const { AgentLoop } = require('../src/agent/index');
const { ToolmakerEngine } = require('../src/toolmaker/index');

console.log('Running Unified Agent Loop Unit Tests...');

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-loop-'));
let mem = null;
try {
  const okScript = path.join(tmp, 'ok-tool.js');
  const failScript = path.join(tmp, 'fail-tool.js');
fs.writeFileSync(okScript, 'console.log("ok-output");\n');
fs.writeFileSync(failScript, 'console.error("simulated-failure"); process.exit(2);\n');
const registryPath = path.join(tmp, 'registry.json');
fs.writeFileSync(registryPath, JSON.stringify({
  version: 1,
  tools: {
    'ok-tool': {
      name: 'ok-tool',
      description: 'Succeeds while auditing locale keys',
      scriptPath: okScript
    },
    'fail-tool': {
      name: 'fail-tool',
      description: 'Always fails for loop tests',
      scriptPath: failScript
    }
  }
}, null, 2));

mem = new LongTermMemory(':memory:');
mem.recordExperience({
  intent: 'Install sqlite native module',
  trigger_pattern: 'npm install better-sqlite3',
  failure_mode: 'EPERM node-gyp',
  root_cause: 'Sandbox blocks native compilation',
  corrective_heuristic: 'Do not compile native addons inside the sandbox',
  domain_tags: ['npm', 'sqlite']
});

const loop = new AgentLoop({
  memory: mem,
  registryPath,
  rootDir: tmp
});

// Test 1: plan() retrieves historical lessons before any tool runs
const planNative = loop.plan('install sqlite native addon');
assert.ok(planNative.lessons.length > 0, 'plan() should retrieve matching reflexion lessons');
assert.ok(planNative.guidance.includes('Do not compile native addons'), 'guidance should include corrective heuristic');
assert.strictEqual(planNative.executed, undefined);
console.log('✓ Test 1 Passed: plan() injects reflexion memory for the upcoming task.');

// Test 2: plan() suggests synthesized tools by intent keywords
const planAudit = loop.plan('audit locale keys');
assert.ok(planAudit.suggestedTools.some(t => t.name === 'ok-tool'), 'locale audit intent should suggest ok-tool');
console.log('✓ Test 2 Passed: plan() matches intent tokens to registered tools.');

// Test 3: execute() runs a registered tool and captures stdout
const okResult = loop.execute('ok-tool');
assert.strictEqual(okResult.ok, true);
assert.strictEqual(okResult.status, 0);
assert.ok(okResult.stdout.includes('ok-output'));
console.log('✓ Test 3 Passed: execute() runs a registry tool without a shell string.');

// Test 4: run() without --exec is plan-only and does not execute
const dryRun = loop.run('audit locale keys');
assert.strictEqual(dryRun.executed, false);
assert.strictEqual(dryRun.result, null);
console.log('✓ Test 4 Passed: run() stays plan-only unless a tool is selected.');

// Test 5: run() executes the selected tool on success
const successRun = loop.run('audit locale keys', { tool: 'ok-tool', exec: true });
assert.strictEqual(successRun.executed, true);
assert.strictEqual(successRun.result.ok, true);
assert.strictEqual(successRun.recorded, null, 'successful runs should not write a failure reflection');
console.log('✓ Test 5 Passed: run() executes the chosen tool and skips reflect on success.');

// Test 6: failed run does not pollute memory with undiagnosed stubs
const silentFail = loop.run('always fails for loop tests', { tool: 'fail-tool' });
assert.strictEqual(silentFail.executed, true);
assert.strictEqual(silentFail.result.ok, false);
assert.strictEqual(silentFail.recorded, null, 'stub failures must not be written by default');

const statsBefore = mem.stats();
const undiagnosed = loop.run('always fails for loop tests', { tool: 'fail-tool', recordOnFailure: true });
assert.strictEqual(undiagnosed.executed, true);
assert.strictEqual(undiagnosed.result.ok, false);
assert.strictEqual(undiagnosed.recorded, null, 'recordOnFailure without a diagnosis must not write');
assert.strictEqual(undiagnosed.recordSkipped, 'undiagnosed');
assert.strictEqual(mem.stats().reflectionCount, statsBefore.reflectionCount);

const failedRun = loop.run('always fails for loop tests', {
  tool: 'fail-tool',
  recordOnFailure: true,
  diagnosis: {
    trigger_pattern: 'tool:fail-tool',
    root_cause: 'fail-tool is a fixture that always exits 2',
    corrective_heuristic: 'Do not retry fail-tool; replace the fixture or assert on status 2'
  }
});
assert.ok(failedRun.recorded && failedRun.recorded.id, 'diagnosed failure should record a reflection');
assert.strictEqual(failedRun.recorded.reinforced, false);
assert.strictEqual(mem.stats().reflectionCount, statsBefore.reflectionCount + 1);

const planFail = loop.plan('tool:fail-tool fixture always exits');
const failLesson = planFail.lessons.find(l => l.trigger_pattern === 'tool:fail-tool');
assert.ok(failLesson, 'diagnosed failure must be retrievable');
assert.ok(failLesson.corrective_heuristic.includes('replace the fixture'), 'must store the supplied heuristic');
assert.ok(!failLesson.corrective_heuristic.includes('native addons'), 'must not copy an unrelated prior heuristic');
console.log('✓ Test 6 Passed: undiagnosed auto-reflect is skipped; diagnosed failures are stored.');

// Test 7: reflect() records an explicit post-mortem and is searchable
const reflected = loop.reflect({
  intent: 'Configure project MCP isolation',
  trigger_pattern: 'write global mcp_config.json',
  root_cause: 'Global config leaks across workspaces',
  corrective_heuristic: 'Use .agents/plugins/<name>/ for project-scoped MCP config',
  status: 'recovered',
  domain_tags: ['mcp']
});
assert.ok(reflected.id.startsWith('ref_'));
const planMcp = loop.plan('configure project MCP tool isolation');
assert.ok(planMcp.lessons.some(l => l.trigger_pattern.includes('mcp_config')), 'explicit reflect() should be searchable');
console.log('✓ Test 7 Passed: reflect() stores a diagnosed lesson for later plan() calls.');

// Test 8: unknown tool does not throw; returns a structured failure
const missing = loop.execute('does-not-exist');
assert.strictEqual(missing.ok, false);
assert.ok(missing.stderr.includes('not found'));
console.log('✓ Test 8 Passed: execute() returns a structured error for unknown tools.');

console.log('Testing execute refuses scripts outside rootDir...');
const evilPath = path.join(os.tmpdir(), 'evil-outside-exec.js');
fs.writeFileSync(evilPath, 'console.log("ESCAPED");\n');
const escapeReg = path.join(tmp, 'escape-registry.json');
fs.writeFileSync(escapeReg, JSON.stringify({
  version: 1,
  tools: {
    evil: { name: 'evil', description: 'escape', scriptPath: evilPath },
    'ok-tool': { name: 'ok-tool', description: 'in-repo', scriptPath: okScript }
  }
}));
const escapeLoop = new AgentLoop({ memory: mem, registryPath: escapeReg, rootDir: tmp });
const escaped = escapeLoop.execute('evil');
assert.strictEqual(escaped.ok, false, 'absolute path outside rootDir must not run');
assert.ok(!String(escaped.stdout).includes('ESCAPED'), 'outside script must not execute');
assert.ok(/outside|refus/i.test(escaped.stderr), `stderr should explain confinement, got: ${escaped.stderr}`);

const relEscapeReg = path.join(tmp, 'rel-escape-registry.json');
fs.writeFileSync(relEscapeReg, JSON.stringify({
  version: 1,
  tools: {
    evil: { name: 'evil', description: 'escape', scriptPath: path.relative(tmp, evilPath) }
  }
}));
const relLoop = new AgentLoop({ memory: mem, registryPath: relEscapeReg, rootDir: tmp });
const relEscaped = relLoop.execute('evil');
assert.strictEqual(relEscaped.ok, false, 'relative path traversal must not run');
assert.ok(!String(relEscaped.stdout).includes('ESCAPED'));

const stillOk = escapeLoop.execute('ok-tool');
assert.strictEqual(stillOk.ok, true, 'in-repo scripts must still run');
assert.ok(String(stillOk.stdout).includes('ok-output'));
fs.unlinkSync(evilPath);
console.log('✓ Test 8b Passed: execute() confines scripts to rootDir.');

// Test 9: plan({ target }) attaches blast-radius for refactoring tasks
const repoRoot = path.resolve(__dirname, '..');
const repoLoop = new AgentLoop({
  memory: mem,
  registryPath,
  rootDir: repoRoot
});
const planBlast = repoLoop.plan('refactor MemoryDatabase', { target: 'src/memory/db.js' });
assert.ok(planBlast.blastRadius, 'target should compute blast radius');
assert.strictEqual(planBlast.blastRadius.targetType, 'file');
assert.ok(planBlast.blastRadius.riskScore > 0);
assert.ok(Array.isArray(planBlast.blastRadius.safetyPlan.steps));
console.log('✓ Test 9 Passed: plan() attaches blast-radius when a refactor target is provided.');

// Test 9b: dirty target auto-enables diff-aware semantic analysis
console.log('Testing plan() auto --diff --semantic on a dirty target...');
const dirtyRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'agent-dirty-'));
const dirtySrc = path.join(dirtyRoot, 'src');
fs.mkdirSync(dirtySrc);
fs.writeFileSync(path.join(dirtySrc, 'lib.js'), 'function login(user) { return user; }\nmodule.exports = { login };\n');
fs.writeFileSync(path.join(dirtySrc, 'app.js'), 'const { login } = require("./lib");\nlogin("a");\nmodule.exports = { app: true };\n');
const gitOpts = { cwd: dirtyRoot, encoding: 'utf8' };
assert.strictEqual(spawnSync('git', ['init'], gitOpts).status, 0);
spawnSync('git', ['config', 'user.email', 'test@example.com'], gitOpts);
spawnSync('git', ['config', 'user.name', 'Test'], gitOpts);
spawnSync('git', ['add', '.'], gitOpts);
assert.strictEqual(spawnSync('git', ['commit', '-m', 'init'], gitOpts).status, 0, 'temp repo commit should succeed');
fs.writeFileSync(path.join(dirtySrc, 'lib.js'), 'function login(user, password) { return user; }\nmodule.exports = { login };\n');

const dirtyLoop = new AgentLoop({ memory: mem, registryPath, rootDir: dirtyRoot });
const dirtyPlan = dirtyLoop.plan('refactor login', { target: 'src/lib.js' });
assert.strictEqual(dirtyPlan.blastRadius.isDiffAware, true, 'dirty file must enable diff-aware mode');
assert.ok(dirtyPlan.blastRadius.semantic, 'dirty file must run AST semantic contract');
assert.strictEqual(dirtyPlan.blastRadius.semantic.mode, 'ast-contract');
fs.writeFileSync(path.join(dirtySrc, 'new.js'), 'function signup(user) { return user; }\nmodule.exports = { signup };\n');
assert.strictEqual(require('../src/agent/index').hasUncommittedDiff(dirtyRoot, 'src/new.js'), true, 'untracked file must be dirty');
const untrackedPlan = dirtyLoop.plan('refactor signup', { target: 'src/new.js' });
assert.strictEqual(untrackedPlan.blastRadius.isDiffAware, true, 'untracked file must enable diff-aware mode');
assert.notStrictEqual(untrackedPlan.blastRadius.scope, 'CLEAN', 'a new untracked file is not a clean no-op diff');
assert.ok(untrackedPlan.blastRadius.scope === 'PUBLIC_CONTRACT' || untrackedPlan.blastRadius.riskScore > 0);
fs.rmSync(dirtyRoot, { recursive: true, force: true });
console.log('✓ Test 9b Passed: plan() auto-enables --diff --semantic when the target is dirty.');
console.log('✓ Test 9c Passed: untracked new files are dirty and not treated as CLEAN.');

// Test 10: CLI plan --json is executable and returns structured output
const cliPath = path.resolve(__dirname, '../src/agent/index.js');
const cliPlan = spawnSync(process.execPath, [
  cliPath, 'plan', 'install sqlite native addon',
  '--json',
  '--db', ':memory:'
], { encoding: 'utf8', cwd: repoRoot });
assert.strictEqual(cliPlan.status, 0, `CLI plan should exit 0, stderr=${cliPlan.stderr}`);
const cliPayload = JSON.parse(cliPlan.stdout.trim());
assert.strictEqual(cliPayload.intent, 'install sqlite native addon');
assert.ok(Array.isArray(cliPayload.suggestedTools));
console.log('✓ Test 10 Passed: CLI plan --json prints a structured loop report.');

// Test 11: CLI tools --json lists registry entries
const cliTools = spawnSync(process.execPath, [
  cliPath, 'tools', '--json', '--registry', registryPath, '--root', tmp
], { encoding: 'utf8', cwd: repoRoot });
assert.strictEqual(cliTools.status, 0, `CLI tools should exit 0, stderr=${cliTools.stderr}`);
const listed = JSON.parse(cliTools.stdout.trim());
assert.ok(listed.tools.some(t => t.name === 'ok-tool'));
console.log('✓ Test 11 Passed: CLI tools --json lists registry entries.');

const cliZh = spawnSync(process.execPath, [
  cliPath, 'plan', 'no-such-intent-xyz',
  '--lang', 'zh-CN',
  '--db', ':memory:'
], { encoding: 'utf8', cwd: repoRoot, env: { ...process.env, LANG: 'C', LANGUAGE: '', LC_ALL: 'C' } });
assert.strictEqual(cliZh.status, 0, `CLI zh plan should exit 0, stderr=${cliZh.stderr}`);
assert.ok(cliZh.stdout.includes('=== Agent Loop：计划 ==='), 'human CLI must follow --lang zh-CN');
assert.ok(cliZh.stdout.includes('没有与该意图匹配的既有反思经验'));
console.log('✓ Test 12 Passed: Agent CLI human output uses src/i18n.js.');

console.log('Testing CLI --record-failure requires a diagnosis...');
const recDb = path.join(tmp, 'record-failure.db');
const recMissing = spawnSync(process.execPath, [
  cliPath, 'run', 'always fails for loop tests',
  '--tool', 'fail-tool',
  '--record-failure',
  '--registry', registryPath,
  '--root', tmp,
  '--db', recDb,
  '--json'
], { encoding: 'utf8', cwd: repoRoot });
assert.strictEqual(recMissing.status, 1, `missing diagnosis must exit 1, stderr=${recMissing.stderr}`);
assert.ok(
  recMissing.stderr.includes('requires --cause') || recMissing.stderr.includes('--cause'),
  `stderr must demand --cause/--heuristic, got: ${recMissing.stderr}`
);

const recOk = spawnSync(process.execPath, [
  cliPath, 'run', 'always fails for loop tests',
  '--tool', 'fail-tool',
  '--record-failure',
  '--cause', 'fail-tool is a fixture that always exits 2',
  '--heuristic', 'Do not retry fail-tool; assert on status 2',
  '--trigger', 'tool:fail-tool',
  '--registry', registryPath,
  '--root', tmp,
  '--db', recDb,
  '--json'
], { encoding: 'utf8', cwd: repoRoot });
assert.ok(recOk.status !== 1 || recOk.stdout.includes('"recorded"'), `diagnosed run stderr=${recOk.stderr} stdout=${recOk.stdout.slice(0, 300)}`);
const recPayload = JSON.parse(recOk.stdout.trim());
assert.ok(recPayload.recorded && recPayload.recorded.id, 'diagnosed --record-failure must write a reflection');
assert.strictEqual(recPayload.recordSkipped, null);
console.log('✓ Test 12b Passed: CLI --record-failure without --cause/--heuristic writes nothing.');

console.log('Testing run() tracks tool usage on the Toolmaker when injected...');
const tm = new ToolmakerEngine({ dbOrPath: ':memory:', registryPath });
const trackingLoop = new AgentLoop({
  memory: mem,
  registryPath,
  rootDir: tmp,
  toolmaker: tm
});
const trackedRun = trackingLoop.run('audit locale keys', { tool: 'ok-tool', exec: true });
assert.ok(trackedRun.tracked, 'execute path should record a toolmaker pattern');
assert.strictEqual(trackedRun.tracked.occurrences, 1);
assert.strictEqual(trackedRun.tracked.candidate.name_slug, 'ok-tool');
tm.close();
console.log('✓ Test 13 Passed: Agent loop tracks executed tools for self-toolmaker frequency.');

console.log('Testing grill-me hard-stop blocks execute on macro intents...');
const grilled = loop.plan('build a social platform');
assert.strictEqual(grilled.blocked, true);
assert.strictEqual(grilled.suggestedTools.length, 0);
assert.strictEqual(grilled.blastRadius, null);
const grilledRun = loop.run('build a social platform', { tool: 'ok-tool', exec: true });
assert.strictEqual(grilledRun.executed, false);
assert.strictEqual(grilledRun.blocked, true);
assert.strictEqual(grilledRun.recordSkipped, 'blocked');
const forcedRun = loop.run('build a social platform', { tool: 'ok-tool', exec: true, force: true });
assert.strictEqual(forcedRun.executed, true);
assert.strictEqual(forcedRun.result.ok, true);
console.log('✓ Test 14 Passed: Macro intents hard-stop execute; --force resumes after clarification.');

console.log('Testing ponytail attaches to plan without blocking...');
const ponyPlan = loop.plan('ponytail yagni slim the locale audit');
assert.strictEqual(ponyPlan.blocked, false, 'ponytail + concrete audit must not grill');
assert.strictEqual(ponyPlan.ponytail.active, true);
assert.ok(ponyPlan.ponytail.rungs.length > 0);
console.log('✓ Test 15 Passed: Ponytail is on-demand and does not replace the grill gate.');

console.log('Testing optional adversary audit on run()...');
const fakeAuditor = { run: () => ({ passed: true, findings: [] }) };
const audited = loop.run('audit locale keys', { tool: 'ok-tool', exec: true, audit: true, auditor: fakeAuditor });
assert.ok(audited.audit && audited.audit.passed === true);
console.log('✓ Test 16 Passed: run({ audit: true }) attaches an adversary report without inventing one.');

console.log('\nAll Unified Agent Loop tests passed successfully! 🎉');
} finally {
  if (mem) mem.close();
  fs.rmSync(tmp, { recursive: true, force: true });
}
