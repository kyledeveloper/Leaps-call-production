const assert = require('assert');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { spawnSync } = require('child_process');
const { ToolmakerEngine } = require('../src/toolmaker/index');

console.log('Running Self-Toolmaker Unit Tests...');

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'toolmaker-test-'));
const testRegistryPath = path.join(tmpDir, 'test_registry.json');

try {
  const toolmaker = new ToolmakerEngine({
    dbOrPath: ':memory:',
    registryPath: testRegistryPath
  });

// Test 1: Pattern tracking and threshold trigger
console.log('Testing PatternTracker frequency accumulation...');
const t1 = toolmaker.track({
  nameSlug: 'test-audit',
  intentSummary: 'Audit untranslated items in locale files',
  commandTemplate: 'node check-i18n.js --strict'
});
assert.strictEqual(t1.occurrences, 1);
assert.strictEqual(t1.shouldSynthesize, false);

const t2 = toolmaker.track({
  nameSlug: 'test-audit',
  intentSummary: 'Audit untranslated items in locale files'
});
assert.strictEqual(t2.occurrences, 2);
assert.strictEqual(t2.shouldSynthesize, false);

const t3 = toolmaker.track({
  nameSlug: 'test-audit',
  intentSummary: 'Audit untranslated items in locale files'
});
assert.strictEqual(t3.occurrences, 3);
assert.strictEqual(t3.shouldSynthesize, true, 'Occurrences >= 3 should trigger synthesis flag');
console.log('✓ Test 1 Passed: Pattern tracking accumulated and triggered threshold at 3.');

// Test 2: Check candidates list
const candidates = toolmaker.getCandidates(3);
assert.strictEqual(candidates.length, 1);
assert.strictEqual(candidates[0].name_slug, 'test-audit');
console.log('✓ Test 2 Passed: Candidate identified correctly in database.');

// Test 3: Tool Synthesis
console.log('Testing ToolSynthesizer script generation...');
const codeBody = `
    const num1 = parseInt(args.a || 10, 10);
    const num2 = parseInt(args.b || 20, 10);
    const sum = num1 + num2;
    if (args.json) {
      console.log(JSON.stringify({ a: num1, b: num2, sum }));
    } else {
      console.log(\`Sum of \${num1} + \${num2} = \${sum}\`);
    }
`;

const toolMeta = toolmaker.synthesizeTool({
  name: 'test-sum-calc',
  description: 'A test calculator tool synthesized by AI',
  parameters: [
    { name: 'a', description: 'First number', default: 10 },
    { name: 'b', description: 'Second number', default: 20 }
  ],
  codeBody,
  examples: ['--a 5 --b 15', '--json'],
  patternHash: t3.patternHash
});

assert.strictEqual(toolMeta.name, 'test-sum-calc');
assert.ok(fs.existsSync(toolMeta.scriptPath), 'Synthesized script file should exist');
console.log('✓ Test 3 Passed: Tool synthesized and written to script path.');

// Test 4: Registry verification
const regTool = toolmaker.getTool('test-sum-calc');
assert.ok(regTool, 'Tool should be registered in registry.json');
assert.strictEqual(regTool.name, 'test-sum-calc');
console.log('✓ Test 4 Passed: Tool registry reflects new synthesized tool.');

// Test 5: Execution via Runner
const runnerPath = path.join(__dirname, '../.agents/scripts/runner.js');
const runRes = spawnSync(process.execPath, [toolMeta.scriptPath, '--a', '100', '--b', '200', '--json'], {
  encoding: 'utf8'
});
assert.strictEqual(runRes.status, 0, 'Script execution should exit with 0');
const parsedOutput = JSON.parse(runRes.stdout.trim());
assert.strictEqual(parsedOutput.sum, 300, 'Sum should equal 300');
console.log('✓ Test 5 Passed: Synthesized tool executes properly and produces expected output.');

// Test 6: Syntax validation rollback on invalid JS (Auditor Recommendation)
console.log('Testing ToolSynthesizer syntax validation rollback on invalid code...');
assert.throws(() => {
  toolmaker.synthesizeTool({
    name: 'broken-tool',
    description: 'A tool with invalid JavaScript syntax',
    codeBody: 'const invalid syntax = ;'
  });
}, /Synthesized script failed syntax validation/);
const brokenPath = path.join(tmpDir, 'broken-tool.js');
assert.strictEqual(fs.existsSync(brokenPath), false, 'Failed tool script must be unlinked and not leave orphan files');
console.log('✓ Test 6 Passed: Syntax validation failure cleanly rolls back with zero orphan files.');

console.log('Testing ToolSynthesizer rejects path-traversal and unsafe names...');
assert.throws(() => {
  toolmaker.synthesizeTool({
    name: '../../evil',
    description: 'should not write outside scripts dir',
    codeBody: 'console.log(1);'
  });
}, /Invalid tool name/);
assert.throws(() => {
  toolmaker.synthesizeTool({
    name: 'foo";touch',
    description: 'should not reach node -c via a shell',
    codeBody: 'console.log(1);'
  });
}, /Invalid tool name/);
const escaped = path.join(tmpDir, '..', '..', 'evil.js');
assert.strictEqual(fs.existsSync(escaped), false, 'path-traversal name must not write a sibling script');
console.log('✓ Test 7 Passed: Unsafe tool names are rejected before any file is written.');

console.log('\nAll Self-Toolmaker tests passed successfully! 🎉');
} finally {
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
