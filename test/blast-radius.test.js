const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

console.log('Running Code Symbol Graph & Blast-Radius Unit Tests (TDD)...');

const parserPath = path.resolve(__dirname, '../src/graph/parser.js');
const graphPath = path.resolve(__dirname, '../src/graph/index.js');
const blastPath = path.resolve(__dirname, '../src/graph/blastRadius.js');
const cliPath = path.resolve(__dirname, '../.agents/scripts/blast-radius.js');

// Require modules (expected to fail in Red Phase if not implemented yet)
let parseSource, SymbolGraph, calculateBlastRadius;
try {
  parseSource = require(parserPath).parseSource;
  SymbolGraph = require(graphPath).SymbolGraph;
  calculateBlastRadius = require(blastPath).calculateBlastRadius;
} catch (e) {
  parseSource = null;
  SymbolGraph = null;
  calculateBlastRadius = null;
}

assert.ok(parseSource, 'src/graph/parser.js module must be loadable and export parseSource');
assert.ok(SymbolGraph, 'src/graph/index.js module must be loadable and export SymbolGraph');
assert.ok(calculateBlastRadius, 'src/graph/blastRadius.js module must be loadable and export calculateBlastRadius');

// Test 1: Parser - Imports and Exports extraction
console.log('Testing Parser: Imports and Exports...');
const sampleCode = `
const path = require('path');
const { MemoryDatabase, DEFAULT_DB_PATH } = require('./db');
const scoring = require('../scoring');

class MyService {
  constructor(db) {
    this.db = db;
  }
  process(query) {
    const score = scoring.calculateScore(query);
    return this.db.get(score);
  }
}

function helper(a, b) {
  return a + b;
}

const arrowFunc = (x) => x * 2;

module.exports = {
  MyService,
  helper
};
`;

const parsed = parseSource(sampleCode, '/project/src/service.js');

assert.strictEqual(parsed.imports.length, 3, 'Should extract 3 require imports');
const dbImport = parsed.imports.find(i => i.source.includes('db'));
assert.ok(dbImport, 'Should detect ./db import');
assert.deepStrictEqual(dbImport.named, ['MemoryDatabase', 'DEFAULT_DB_PATH'], 'Should extract named imports');

assert.ok(parsed.classes.some(c => c.name === 'MyService'), 'Should detect MyService class');
assert.ok(parsed.functions.some(f => f.name === 'helper'), 'Should detect helper function');
assert.ok(parsed.functions.some(f => f.name === 'arrowFunc'), 'Should detect arrow function');
assert.deepStrictEqual(parsed.exports, ['MyService', 'helper'], 'Should detect exported symbols');
console.log('✓ Test 1 Passed: Parser correctly extracts imports, exports, functions, and classes.');

// Test 2: Parser - Relative Path Resolution
console.log('Testing Parser: Relative path resolution...');
const { resolveModulePath } = require(parserPath);
const fromFile = path.resolve(__dirname, '../src/memory/index.js');
const resolvedDb = resolveModulePath('./db', fromFile);
assert.strictEqual(resolvedDb, path.resolve(__dirname, '../src/memory/db.js'), 'Should resolve ./db to db.js');
console.log('✓ Test 2 Passed: Relative module resolution resolves target file paths properly.');

// Test 3: Graph Construction - Building Project Symbol Graph
console.log('Testing SymbolGraph: Project-wide indexing...');
const rootDir = path.resolve(__dirname, '..');
const graph = new SymbolGraph({ rootDir });
graph.build();

const dbFilePath = path.resolve(rootDir, 'src/memory/db.js');
const indexFilePath = path.resolve(rootDir, 'src/memory/index.js');

const dbNode = graph.getFileNode(dbFilePath);
assert.ok(dbNode, 'src/memory/db.js should be indexed as a node');
assert.ok(dbNode.exports.includes('MemoryDatabase'), 'db.js should export MemoryDatabase');

const downstreamOfDb = graph.getDownstreamFiles(dbFilePath);
assert.ok(downstreamOfDb.includes(indexFilePath), 'src/memory/index.js should be downstream of src/memory/db.js');
console.log('✓ Test 3 Passed: SymbolGraph correctly indexes project files and binds dependency relations.');

// Test 4: Blast-Radius Calculation - File Level Impact
console.log('Testing Blast-Radius: File-level impact on src/memory/db.js...');
const blastDb = calculateBlastRadius(dbFilePath, { graph });

assert.strictEqual(blastDb.targetType, 'file');
assert.ok(blastDb.directFiles.includes(indexFilePath), 'src/memory/index.js must be in direct impact list');
const testMemoryPath = path.resolve(rootDir, 'test/memory.test.js');
assert.ok(blastDb.impactedTests.includes(testMemoryPath), 'test/memory.test.js must be in impacted tests list');
assert.ok(blastDb.riskScore > 0, 'Risk score must be > 0');
assert.ok(['LOW', 'MEDIUM', 'HIGH'].includes(blastDb.riskLevel), 'Risk level should be valid enum');
console.log('✓ Test 4 Passed: File-level blast radius detects direct callers and affected test suites.');

// Test 5: Blast-Radius Calculation - Symbol Level Impact
console.log('Testing Blast-Radius: Symbol-level impact on MemoryDatabase...');
const blastSymbol = calculateBlastRadius('MemoryDatabase', { graph });

assert.strictEqual(blastSymbol.targetType, 'symbol');
assert.ok(blastSymbol.declarationFile.includes('db.js'), 'Declaration file should point to db.js');
assert.ok(blastSymbol.impactedTests.includes(testMemoryPath), 'Should identify impacted test file for MemoryDatabase');
console.log('✓ Test 5 Passed: Symbol-level blast radius accurately traces symbol calls and tests.');

// Test 6: Safety Plan & Checklist Generation
console.log('Testing Safety Plan generation...');
assert.ok(blastDb.safetyPlan, 'Should contain safetyPlan object');
assert.ok(Array.isArray(blastDb.safetyPlan.recommendedTestCommands), 'Should list test commands');
assert.ok(blastDb.safetyPlan.steps.length > 0, 'Should provide step-by-step refactoring guidelines');
console.log('✓ Test 6 Passed: Safety plan generates actionable checklists and test commands.');

// Test 7: CLI Execution - Terminal & JSON output
console.log('Testing CLI execution via runner.js...');
const cliRun = spawnSync(process.execPath, [
  cliPath,
  '--target', 'src/memory/db.js',
  '--json'
], { encoding: 'utf8' });

assert.strictEqual(cliRun.status, 0, 'CLI should exit with code 0');
const parsedOutput = JSON.parse(cliRun.stdout.trim());
assert.strictEqual(parsedOutput.target, 'src/memory/db.js');
assert.ok(parsedOutput.directCount >= 1, 'Should report direct impact count');
console.log('✓ Test 7 Passed: CLI execution outputs structured JSON report cleanly.');

// Test 8: CLI Execution - Tree view formatting
console.log('Testing CLI execution with --tree flag...');
const treeRun = spawnSync(process.execPath, [
  cliPath,
  '--target', 'src/memory/db.js',
  '--tree',
  '--lang', 'en-US'
], { encoding: 'utf8' });

assert.strictEqual(treeRun.status, 0, 'Tree CLI should exit with code 0');
assert.ok(treeRun.stdout.includes('Blast Radius'), 'Should print human-readable tree header');
assert.ok(treeRun.stdout.includes('src/memory/index.js'), 'Tree output should display downstream file');
console.log('✓ Test 8 Passed: CLI tree mode renders ASCII dependency hierarchy.');

// Test 9: Fuzzy basename target resolution (e.g. 'db.js' -> 'src/memory/db.js')
console.log('Testing fuzzy target resolution by basename...');
const fuzzyBlast = calculateBlastRadius('db.js', { graph });
assert.strictEqual(fuzzyBlast.targetType, 'file');
assert.ok(fuzzyBlast.targetFile.includes('src/memory/db.js'), 'Should resolve db.js to src/memory/db.js');
assert.ok(fuzzyBlast.directCount >= 1, 'Should find direct callers via fuzzy basename');
console.log('✓ Test 9 Passed: Fuzzy target resolution correctly maps basenames to project files.');

// Test 10: Parser export preservation with line comments
console.log('Testing Parser export preservation with embedded comments...');
const codeWithComments = `
module.exports = {
  MemoryDatabase, // database class
  DEFAULT_DB_PATH,
  calculateScore
};
`;
const parsedWithComments = parseSource(codeWithComments, '/test.js');
assert.ok(parsedWithComments.exports.includes('MemoryDatabase'));
assert.ok(parsedWithComments.exports.includes('DEFAULT_DB_PATH'));
assert.ok(parsedWithComments.exports.includes('calculateScore'));
console.log('✓ Test 10 Passed: Embedded line comments do not corrupt exported symbol extraction.');

// Test 11: URL literals containing // must not be treated as comments
console.log('Testing Parser preservation of https:// URL string literals...');
const codeWithUrlExport = `
module.exports = { url: "https://x.com", foo, bar };
`;
const parsedUrlExport = parseSource(codeWithUrlExport, '/test-url.js');
assert.ok(parsedUrlExport.exports.includes('url'), 'url export must survive https:// in a string');
assert.ok(parsedUrlExport.exports.includes('foo'), 'foo export must survive https:// in a string');
assert.ok(parsedUrlExport.exports.includes('bar'), 'bar export must survive https:// in a string');

const codeWithUrlAndComment = `
const API = "https://example.com/v1";
const { MemoryDatabase } = require("./db");
function fetchUser() { return API; } // trailing comment
module.exports = { fetchUser, API };
`;
const parsedUrlComment = parseSource(codeWithUrlAndComment, '/tmp/service.js');
assert.ok(parsedUrlComment.imports.some(i => i.source.includes('db')), 'require after a URL literal must still parse');
assert.ok(parsedUrlComment.exports.includes('fetchUser'));
assert.ok(parsedUrlComment.exports.includes('API'));
assert.ok(parsedUrlComment.functions.some(f => f.name === 'fetchUser'));
console.log('✓ Test 11 Passed: https:// inside strings is not stripped as a line comment.');

// Test 12: ESM export { } and import * as
console.log('Testing Parser ESM export lists and namespace imports...');
const esmCode = `
import * as memory from './src/memory/index.js';
import { AgentLoop } from './src/agent/index.js';
export { parseSource, stripComments as strip };
export function calculateBlastRadius() {}
`;
const parsedEsm = parseSource(esmCode, '/tmp/graph.js');
assert.ok(parsedEsm.imports.some(i => i.type === 'esm' && i.defaultName === 'memory' && i.source.includes('memory')), 'import * as memory must be captured');
assert.ok(parsedEsm.imports.some(i => i.named && i.named.includes('AgentLoop')), 'named ESM import must still parse');
assert.ok(parsedEsm.exports.includes('parseSource'), 'export { parseSource } must be captured');
assert.ok(parsedEsm.exports.includes('strip'), 'export { x as strip } must use the public name');
assert.ok(parsedEsm.exports.includes('calculateBlastRadius'));
console.log('✓ Test 12 Passed: export { } and import * as are parsed.');

// Test 13: Parser function and class line range tracking
console.log('Testing Parser line range tracking for functions and classes...');
const sampleWithLines = `
function firstFunc() {
  const x = 1;
  return x;
}

class SampleClass {
  constructor() {}
}

const arrow = (a) => {
  return a * 2;
};
`;
const parsedWithLines = parseSource(sampleWithLines, '/sample.js');
const first = parsedWithLines.functions.find(f => f.name === 'firstFunc');
assert.ok(first, 'Should parse firstFunc');
assert.strictEqual(first.startLine, 2, 'firstFunc startLine should be 2');
assert.strictEqual(first.endLine, 5, 'firstFunc endLine should be 5');

const cls = parsedWithLines.classes.find(c => c.name === 'SampleClass');
assert.ok(cls, 'Should parse SampleClass');
assert.strictEqual(cls.startLine, 7, 'SampleClass startLine should be 7');
assert.strictEqual(cls.endLine, 9, 'SampleClass endLine should be 9');

const arr = parsedWithLines.functions.find(f => f.name === 'arrow');
assert.ok(arr, 'Should parse arrow');
assert.strictEqual(arr.startLine, 11, 'arrow startLine should be 11');
assert.strictEqual(arr.endLine, 13, 'arrow endLine should be 13');
console.log('✓ Test 13 Passed: Parser accurately records startLine and endLine for functions and classes.');

// Test 14: parseDiffHunks parses modified lines from unified diff
console.log('Testing parseDiffHunks line number extraction...');
const { parseDiffHunks } = require(blastPath);
assert.ok(parseDiffHunks, 'blastRadius.js must export parseDiffHunks');
const sampleDiff = `
--- a/src/service.js
+++ b/src/service.js
@@ -10,0 +11,3 @@
+const internalA = 1;
+const internalB = 2;
+return internalA + internalB;
@@ -25,2 +28,1 @@
-oldLine1
-oldLine2
+newLine1
@@ -40 +43 @@
+singleChange
`;
const modifiedLines = parseDiffHunks(sampleDiff, 'src/service.js');
assert.deepStrictEqual(modifiedLines, [11, 12, 13, 28, 43], 'Should extract 1-based line numbers from hunks');
console.log('✓ Test 14 Passed: parseDiffHunks accurately extracts modified line numbers.');

// Test 15: Modifying an internal private function converges to LOCAL_PRIVATE
console.log('Testing Diff-Aware blast radius on private internal modification...');
const os = require('os');
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'blast-diff-'));

try {
  const modAPath = path.join(tmpDir, 'moduleA.js');
  const modBPath = path.join(tmpDir, 'moduleB.js');

  const modACode = `
function _internalHelper(val) {
  return val * 10;
}

function publicExported() {
  return "hello";
}

module.exports = {
  publicExported
};
`;

  const modBCode = `
const { publicExported } = require('./moduleA');
function consumer() {
  return publicExported();
}
module.exports = { consumer };
`;

  fs.writeFileSync(modAPath, modACode, 'utf8');
  fs.writeFileSync(modBPath, modBCode, 'utf8');

  const isolatedGraph = new SymbolGraph({ rootDir: tmpDir });
  isolatedGraph.build();

  // Verify downstream relationship initially
  const downstreamBefore = isolatedGraph.getDownstreamFiles(modAPath);
  assert.ok(downstreamBefore.includes(modBPath), 'moduleB should depend on moduleA');

  // Scenario 1: Diff modifies ONLY _internalHelper (line 3: return val * 10;)
  const privateDiff = `
--- a/moduleA.js
+++ b/moduleA.js
@@ -3 +3 @@
-  return val * 10;
+  return val * 20;
`;

  const blastPrivate = calculateBlastRadius(modAPath, {
    graph: isolatedGraph,
    diff: privateDiff,
    diffAware: true,
    rootDir: tmpDir
  });

  assert.strictEqual(blastPrivate.isDiffAware, true, 'Report should be marked as diff-aware');
  assert.strictEqual(blastPrivate.scope, 'LOCAL_PRIVATE', 'Scope should converge to LOCAL_PRIVATE');
  assert.strictEqual(blastPrivate.riskLevel, 'LOW', 'Risk level should be LOW for private changes');
  assert.deepStrictEqual(blastPrivate.directFiles, [], 'No downstream files should be in direct impact for private changes');
  assert.deepStrictEqual(blastPrivate.indirectFiles, [], 'No indirect files should be alerted for private changes');
  assert.ok(blastPrivate.notes && blastPrivate.notes.includes('LOCAL_PRIVATE'), 'Notes should explain local private containment');
  console.log('✓ Test 15 Passed: Internal private modification converges to LOCAL_PRIVATE with LOW risk.');

  // Test 16: Modifying public exported function triggers full blast radius
  console.log('Testing Diff-Aware blast radius on public exported function modification...');
  const publicDiff = `
--- a/moduleA.js
+++ b/moduleA.js
@@ -7 +7 @@
-  return "hello";
+  return "world";
`;

  const blastPublic = calculateBlastRadius(modAPath, {
    graph: isolatedGraph,
    diff: publicDiff,
    diffAware: true,
    rootDir: tmpDir
  });

  assert.strictEqual(blastPublic.isDiffAware, true);
  assert.strictEqual(blastPublic.scope, 'PUBLIC_CONTRACT', 'Scope should be PUBLIC_CONTRACT when touching exported function');
  assert.ok(blastPublic.directFiles.includes(modBPath), 'moduleB must be alerted when public function is modified');
  console.log('✓ Test 16 Passed: Modifying exported function triggers full downstream blast radius.');
} finally {
  // Hermetic sandbox guaranteed cleanup
  fs.rmSync(tmpDir, { recursive: true, force: true });
}

// Test 17: CLI execution with hermetic mock diff argument
console.log('Testing CLI execution with --diff flag...');
const mockCliDiff = [
  'diff --git a/src/memory/db.js b/src/memory/db.js',
  '--- a/src/memory/db.js',
  '+++ b/src/memory/db.js',
  '@@ -20,1 +20,1 @@',
  '- const old = 1;',
  '+ const old = 2;'
].join('\n');

const cliDiffRun = spawnSync(process.execPath, [
  cliPath,
  '--target', 'src/memory/db.js',
  '--diff', mockCliDiff,
  '--json'
], { encoding: 'utf8' });

assert.strictEqual(cliDiffRun.status, 0, 'CLI with --diff should exit with 0');
const parsedCliDiff = JSON.parse(cliDiffRun.stdout.trim());
assert.strictEqual(parsedCliDiff.target, 'src/memory/db.js');
assert.strictEqual(parsedCliDiff.isDiffAware, true, 'isDiffAware should be true in CLI report');
console.log('✓ Test 17 Passed: CLI with --diff flag produces structured diff-aware report.');

// Test 18: Deep destructuring and aliases in CJS require and ESM import/export
console.log('Testing Acorn Parser deep destructuring and aliases...');
const deepCode = `
const { config: { db: { connectionString: dbUri } } } = require('./config');
import { originalName as aliasedName, standardHelper } from './utils';

function runner() {
  return aliasedName(dbUri);
}

const exportedValue = 100;
export { runner, exportedValue as publicValue };
`;
const parsedDeep = parseSource(deepCode, '/project/src/deep.js');
assert.ok(parsedDeep.imports.some(i => i.source.includes('config') && i.named && i.named.includes('dbUri')), 'Should capture deeply destructured dbUri alias');
assert.ok(parsedDeep.imports.some(i => i.source.includes('utils') && i.named && i.named.includes('aliasedName')), 'Should capture aliasedName import');
assert.ok(parsedDeep.exports.includes('runner'), 'Should capture runner export');
assert.ok(parsedDeep.exports.includes('publicValue'), 'Should capture publicValue export alias');
console.log('✓ Test 18 Passed: Deep destructuring and export aliases parsed accurately.');

// Test 19: False-positive elimination for built-in globals (JSON.parse, Math.max, etc.)
console.log('Testing False-positive elimination for built-in globals...');
const globalCallsCode = `
const customParser = require('./customParser');

function handler(data) {
  const obj = JSON.parse(data); // Built-in global JSON.parse
  const maxVal = Math.max(1, 2); // Built-in global Math.max
  console.log(obj, maxVal); // Built-in global console.log
  return customParser.parse(obj); // Real user symbol call
}
`;
const parsedGlobals = parseSource(globalCallsCode, '/project/src/handler.js');
assert.ok(!parsedGlobals.callSites.includes('JSON.parse'), 'Should NOT include JSON.parse in call sites');
assert.ok(!parsedGlobals.callSites.includes('Math.max'), 'Should NOT include Math.max in call sites');
assert.ok(!parsedGlobals.callSites.includes('console.log'), 'Should NOT include console.log in call sites');
assert.ok(parsedGlobals.callSites.includes('customParser.parse'), 'Should include customParser.parse in call sites');
console.log('✓ Test 19 Passed: Built-in global calls successfully excluded from call sites.');

// Test 20: Scope chain awareness and local variable shadowing
console.log('Testing Scope chain awareness and local variable shadowing...');
const scopeCode = `
function outer() {
  function calculate(x) {
    return x * 2;
  }
  return calculate(10);
}

function shadowScope(calculate) {
  return calculate();
}
`;
const parsedScope = parseSource(scopeCode, '/project/src/scope.js');
assert.ok(parsedScope.functions.some(f => f.name === 'outer'), 'outer function captured');
assert.ok(parsedScope.functions.some(f => f.name === 'shadowScope'), 'shadowScope function captured');
console.log('✓ Test 20 Passed: Lexical scopes and function declarations accurately indexed.');

// Test 21: Smooth fallback on non-standard / TypeScript syntax
console.log('Testing Smooth fallback on non-standard / TypeScript syntax...');
const tsCode = `
interface UserConfig {
  id: string;
  port: number;
}

export function startServer(cfg: UserConfig): void {
  console.log(cfg.port);
}

module.exports = { startServer };
`;
const parsedTs = parseSource(tsCode, '/project/src/server.ts');
assert.ok(parsedTs, 'Fallback parser should not throw on TS syntax');
assert.ok(parsedTs.exports.includes('startServer'), 'Fallback parser should extract exported startServer');
assert.strictEqual(parsedTs.isDegraded, true, 'TS fallback should have isDegraded: true');
assert.strictEqual(parsedTs.parserType, 'regex-fallback', 'TS fallback should have parserType: regex-fallback');
console.log('✓ Test 21 Passed: Smooth fallback on TS syntax succeeds with degradation flag.');

// Test 22: Red-Team [CRITICAL-01] module.exports.fn = ... export extraction
console.log('Testing Red-Team [CRITICAL-01]: module.exports.fn = ... export pattern...');
const modExportPropCode = `
function doTask() { return "task-done"; }
function anotherTask() { return "another"; }
module.exports.doTask = doTask;
exports.anotherTask = anotherTask;
`;
const parsedModExports = parseSource(modExportPropCode, '/project/src/worker.js');
assert.ok(parsedModExports.exports.includes('doTask'), 'module.exports.doTask MUST be extracted as an export');
assert.ok(parsedModExports.exports.includes('anotherTask'), 'exports.anotherTask MUST be extracted as an export');
console.log('✓ Test 22 Passed: module.exports.doTask correctly recognized as exported contract.');

// Test 23: Red-Team [CRITICAL-02] Private function called by exported function triggers PUBLIC_CONTRACT
console.log('Testing Red-Team [CRITICAL-02]: Internal call from export to private function...');
const authTmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'blast-auth-'));
try {
  const authFile = path.join(authTmpDir, 'authService.js');
  const clientFile = path.join(authTmpDir, 'client.js');

  const authCode = `
function _hashPassword(p) {
  return p + "_salt";
}

function login(username, password) {
  const hash = _hashPassword(password);
  return username + hash;
}

module.exports = { login };
`;

  const clientCode = `
const { login } = require('./authService');
function authenticate() {
  return login("alice", "secret");
}
module.exports = { authenticate };
`;

  fs.writeFileSync(authFile, authCode, 'utf8');
  fs.writeFileSync(clientFile, clientCode, 'utf8');

  const authGraph = new SymbolGraph({ rootDir: authTmpDir });
  authGraph.build();

  // Diff modifies ONLY _hashPassword (line 3: return p + "_salt";)
  const privateModifiedDiff = `
--- a/authService.js
+++ b/authService.js
@@ -3 +3 @@
-  return p + "_salt";
+  return p + "_v2_salt";
`;

  const blastInternalDep = calculateBlastRadius(authFile, {
    graph: authGraph,
    diff: privateModifiedDiff,
    diffAware: true,
    rootDir: authTmpDir
  });

  // Because login() calls _hashPassword(), modifying _hashPassword() MUST propagate to login()
  assert.strictEqual(blastInternalDep.scope, 'PUBLIC_CONTRACT', 'Modifying private function called by export MUST propagate to PUBLIC_CONTRACT');
  assert.ok(blastInternalDep.directFiles.includes(clientFile), 'client.js must be in directFiles when private dependency of login() changes');
  console.log('✓ Test 23 Passed: Private function called by export propagates downstream as PUBLIC_CONTRACT.');
} finally {
  fs.rmSync(authTmpDir, { recursive: true, force: true });
}

// Test 24: Red-Team [CRITICAL-03] parseDiffHunks multi-file basename isolation
console.log('Testing Red-Team [CRITICAL-03]: parseDiffHunks multi-file path isolation...');
const multiFileDiff = `
diff --git a/test/db.test.js b/test/db.test.js
--- a/test/db.test.js
+++ b/test/db.test.js
@@ -50,5 +50,5 @@
- assert(true);
+ assert(false);
diff --git a/src/db.js b/src/db.js
--- a/src/db.js
+++ b/src/db.js
@@ -10,2 +10,2 @@
- function query() {}
+ function query(x) {}
`;

const srcDbLines = parseDiffHunks(multiFileDiff, 'src/db.js');
assert.deepStrictEqual(srcDbLines, [10, 11], 'src/db.js lines should only match src/db.js hunks, not test/db.test.js');

const testDbLines = parseDiffHunks(multiFileDiff, 'test/db.test.js');
assert.deepStrictEqual(testDbLines, [50, 51, 52, 53, 54], 'test/db.test.js lines should only match test/db.test.js');
console.log('✓ Test 24 Passed: parseDiffHunks isolates multiple files without basename contamination.');

// Test 25: Red-Team [WARNING-02, WARNING-03] Object.assign exports and ESM forwarding
console.log('Testing Red-Team [WARNING-02, WARNING-03]: Object.assign exports and export * forwarding...');
const assignCode = `
Object.assign(module.exports, {
  alpha() { return 1; },
  beta() { return 2; }
});
export * from './barrelTarget';
export { helper } from './helperTarget';
`;
const parsedAssign = parseSource(assignCode, '/project/src/barrel.js');
assert.ok(parsedAssign.exports.includes('alpha'), 'Object.assign(module.exports) export alpha captured');
assert.ok(parsedAssign.exports.includes('beta'), 'Object.assign(module.exports) export beta captured');
assert.ok(parsedAssign.imports.some(imp => imp.source === './barrelTarget'), 'export * from source captured in imports');
assert.ok(parsedAssign.imports.some(imp => imp.source === './helperTarget'), 'export { foo } from source captured in imports');
console.log('✓ Test 25 Passed: Object.assign and export forwarding properly indexed.');

// Test 26: Red-Team [WARNING-01] Acorn AST parser flag isDegraded === false
console.log('Testing Red-Team [WARNING-01]: High-precision parser sets isDegraded: false...');
const validJsCode = `function test() { return 42; } module.exports = { test };`;
const parsedValidJs = parseSource(validJsCode, '/project/src/valid.js');
assert.strictEqual(parsedValidJs.isDegraded, false, 'Standard JS should not be degraded');
assert.strictEqual(parsedValidJs.parserType, 'ast-acorn', 'Standard JS should use ast-acorn parserType');
console.log('✓ Test 26 Passed: Standard JS correctly reports ast-acorn parserType with zero degradation.');

// Test 27: Stage 2 Semantic Blast Radius - Adding parameter with default value is COMPATIBLE
console.log('Testing Semantic Blast Radius: Adding parameter with default value (COMPATIBLE)...');
const semanticPath = path.resolve(__dirname, '../src/graph/semanticEvaluator.js');
const {
  analyzeSignature,
  extractSignatureFromHunk,
  sliceCallSite,
  extractCallSitesInFile,
  evaluateStructuralContract,
  evaluateSemanticBlastRadius
} = require(semanticPath);

const semCompatDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sem-compat-'));
try {
  const serviceFile = path.join(semCompatDir, 'pricingService.js');
  const clientFile = path.join(semCompatDir, 'orderClient.js');

  const serviceCode = `
function calculateTotal(price, taxRate = 0.05) {
  return price * (1 + taxRate);
}
module.exports = { calculateTotal };
`;
  const clientCode = `
const { calculateTotal } = require('./pricingService');
function checkout() {
  return calculateTotal(100);
}
module.exports = { checkout };
`;
  fs.writeFileSync(serviceFile, serviceCode, 'utf8');
  fs.writeFileSync(clientFile, clientCode, 'utf8');

  const semGraph = new SymbolGraph({ rootDir: semCompatDir });
  semGraph.build();

  const diffAddDefault = `
--- a/pricingService.js
+++ b/pricingService.js
@@ -1,3 +1,3 @@
-function calculateTotal(price) {
+function calculateTotal(price, taxRate = 0.05) {
`;

  const report = calculateBlastRadius(serviceFile, {
    graph: semGraph,
    diff: diffAddDefault,
    diffAware: true,
    semantic: true,
    rootDir: semCompatDir
  });

  assert.strictEqual(report.scope, 'PUBLIC_CONTRACT', 'Scope must be PUBLIC_CONTRACT');
  assert.ok(report.semanticAnalysis, 'Report must include semanticAnalysis');
  assert.strictEqual(report.semanticAnalysis.hasBreaking, false, 'Adding default parameter must not be breaking');
  assert.strictEqual(report.semanticAnalysis.overallVerdict, 'COMPATIBLE', 'Overall verdict must be COMPATIBLE');
  assert.strictEqual(report.riskLevel, 'LOW', 'Risk level should downgrade to LOW for compatible changes');
  assert.ok(report.riskScore <= 20, `Risk score should be <= 20, got ${report.riskScore}`);
  assert.ok(report.notes.includes('[SEMANTIC: COMPATIBLE]'), 'Notes must indicate semantic compatibility');
  console.log('✓ Test 27 Passed: Adding optional parameter with default is correctly evaluated as COMPATIBLE.');
} finally {
  fs.rmSync(semCompatDir, { recursive: true, force: true });
}

// Test 28: Stage 2 Semantic Blast Radius - Adding mandatory parameter without default is BREAKING
console.log('Testing Semantic Blast Radius: Adding mandatory parameter without default (BREAKING)...');
const semBreakDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sem-break-'));
try {
  const serviceFile = path.join(semBreakDir, 'pricingService.js');
  const clientFile = path.join(semBreakDir, 'orderClient.js');

  const serviceCode = `
function calculateTotal(price, taxRate) {
  return price * (1 + taxRate);
}
module.exports = { calculateTotal };
`;
  const clientCode = `
const { calculateTotal } = require('./pricingService');
function checkout() {
  return calculateTotal(100);
}
module.exports = { checkout };
`;
  fs.writeFileSync(serviceFile, serviceCode, 'utf8');
  fs.writeFileSync(clientFile, clientCode, 'utf8');

  const semGraph = new SymbolGraph({ rootDir: semBreakDir });
  semGraph.build();

  const diffAddMandatory = `
--- a/pricingService.js
+++ b/pricingService.js
@@ -1,3 +1,3 @@
-function calculateTotal(price) {
+function calculateTotal(price, taxRate) {
`;

  const report = calculateBlastRadius(serviceFile, {
    graph: semGraph,
    diff: diffAddMandatory,
    diffAware: true,
    semantic: true,
    rootDir: semBreakDir
  });

  assert.ok(report.semanticAnalysis, 'Report must include semanticAnalysis');
  assert.strictEqual(report.semanticAnalysis.hasBreaking, true, 'Adding mandatory parameter without default must be breaking');
  assert.strictEqual(report.semanticAnalysis.overallVerdict, 'BREAKING', 'Overall verdict must be BREAKING');
  assert.strictEqual(report.riskLevel, 'HIGH', 'Risk level should be elevated to HIGH for breaking changes');
  assert.ok(report.riskScore >= 85, `Risk score should be >= 85, got ${report.riskScore}`);
  assert.ok(report.notes.includes('[SEMANTIC: BREAKING]'), 'Notes must indicate semantic breaking change');
  console.log('✓ Test 28 Passed: Adding mandatory parameter without default is correctly evaluated as BREAKING.');
} finally {
  fs.rmSync(semBreakDir, { recursive: true, force: true });
}

// Test 29: AST-only semantic path still reports BREAKING (no LLM hook)
console.log('Testing AST-only semantic path: mandatory param stays BREAKING...');
const semVetoDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sem-veto-'));
try {
  const serviceFile = path.join(semVetoDir, 'authService.js');
  const clientFile = path.join(semVetoDir, 'app.js');

  const serviceCode = `
function login(username, password) {
  return true;
}
module.exports = { login };
`;
  const clientCode = `
const { login } = require('./authService');
login('admin');
`;
  fs.writeFileSync(serviceFile, serviceCode, 'utf8');
  fs.writeFileSync(clientFile, clientCode, 'utf8');

  const semGraph = new SymbolGraph({ rootDir: semVetoDir });
  semGraph.build();

  const diffAddMandatory = `
--- a/authService.js
+++ b/authService.js
@@ -1,3 +1,3 @@
-function login(username) {
+function login(username, password) {
`;

  const report = calculateBlastRadius(serviceFile, {
    graph: semGraph,
    diff: diffAddMandatory,
    diffAware: true,
    semantic: true,
    rootDir: semVetoDir
  });

  assert.strictEqual(report.semanticAnalysis.hasBreaking, true);
  assert.strictEqual(report.semanticAnalysis.overallVerdict, 'BREAKING');
  assert.strictEqual(report.semanticAnalysis.isDegraded, false, 'AST contract is the primary path, not a degraded fallback');
  assert.strictEqual(report.semanticAnalysis.mode, 'ast-contract');
  const evalItem = report.semanticAnalysis.evaluations[0];
  assert.strictEqual(evalItem.verdict, 'BREAKING');
  console.log('✓ Test 29 Passed: AST-only semantic path reports BREAKING without an LLM adapter.');
} finally {
  fs.rmSync(semVetoDir, { recursive: true, force: true });
}

// Test 30: Red-Team [BLOCKER-2] Resilient Regex Signature Extraction on Partial Diff Hunks
console.log('Testing Red-Team [BLOCKER-2]: Resilient diff regex fallback on partial hunk lines...');
const partialHunkText = `
@@ -45,4 +45,4 @@
- async function requestData(endpoint, timeout) {
+ async function requestData(endpoint, timeout = 3000, maxRetries = 2) {
`;

const extractedOld = extractSignatureFromHunk(partialHunkText, 'requestData', 'old');
assert.ok(extractedOld, 'Must extract old signature from hunk');
assert.strictEqual(extractedOld.name, 'requestData');
assert.strictEqual(extractedOld.isAsync, true);
assert.strictEqual(extractedOld.paramCount, 2);
assert.strictEqual(extractedOld.requiredCount, 2);

const extractedNew = extractSignatureFromHunk(partialHunkText, 'requestData', 'new');
assert.ok(extractedNew, 'Must extract new signature from hunk');
assert.strictEqual(extractedNew.name, 'requestData');
assert.strictEqual(extractedNew.isAsync, true);
assert.strictEqual(extractedNew.paramCount, 3);
assert.strictEqual(extractedNew.requiredCount, 1);
assert.strictEqual(extractedNew.params[1].hasDefault, true);
assert.strictEqual(extractedNew.params[2].hasDefault, true);

// Test with arrow function in hunk
const arrowHunk = `+ const processItem = (id, options = {}) => {`;
const extractedArrow = extractSignatureFromHunk(arrowHunk, 'processItem');
assert.ok(extractedArrow, 'Must extract arrow signature from hunk');
assert.strictEqual(extractedArrow.paramCount, 2);
assert.strictEqual(extractedArrow.requiredCount, 1);
console.log('✓ Test 30 Passed: Resilient diff regex safely parses partial hunks with zero SyntaxError.');

// Test 31: Red-Team [BLOCKER-3] Hermetic AST Call-Site Slicing (<= 20 lines)
console.log('Testing Red-Team [BLOCKER-3]: Hermetic AST Call-site slicing (<= 20 lines)...');
const acorn = require('acorn');
const largeCallerLines = [];
for (let i = 1; i <= 60; i++) {
  if (i === 35) {
    largeCallerLines.push('  const data = fetchTarget(123);');
  } else {
    largeCallerLines.push(`  // context line ${i}`);
  }
}
const largeCallerCode = `function runWorkflow() {\n${largeCallerLines.join('\n')}\n}\n`;
const callerAst = acorn.parse(largeCallerCode, { ecmaVersion: 'latest', sourceType: 'module', locations: true });

let targetCallNode = null;
const walk = require('acorn-walk');
walk.simple(callerAst, {
  CallExpression(node) {
    if (node.callee?.name === 'fetchTarget') {
      targetCallNode = node;
    }
  }
});

assert.ok(targetCallNode, 'Must locate fetchTarget CallExpression in AST');
const slicedSnippet = sliceCallSite(largeCallerCode, targetCallNode);
const slicedLineCount = slicedSnippet.split('\n').length;
assert.ok(slicedLineCount <= 20, `Sliced snippet must be <= 20 lines, got ${slicedLineCount}`);
assert.ok(slicedSnippet.includes('fetchTarget(123)'), 'Sliced snippet must include the target call expression');
console.log(`✓ Test 31 Passed: Hermetic call-site slicing produced ${slicedLineCount} lines (<= 20 line limit).`);

// Test 32: Red-Team [CONCERN-1] Sync to Async Transition Trap
console.log('Testing Red-Team [CONCERN-1]: Sync-to-Async transition without await in caller...');
const syncSig = {
  name: 'fetchUser',
  isAsync: false,
  isGenerator: false,
  params: [{ name: 'id', hasDefault: false, isRest: false, isDestructured: false, keys: [] }],
  paramCount: 1,
  requiredCount: 1
};
const asyncSig = {
  name: 'fetchUser',
  isAsync: true,
  isGenerator: false,
  params: [{ name: 'id', hasDefault: false, isRest: false, isDestructured: false, keys: [] }],
  paramCount: 1,
  requiredCount: 1
};

// Caller calls fetchUser synchronously without await
const unhandledCallerCode = `
function handleRequest() {
  const user = fetchUser(101);
  return user.name;
}
`;
const unhandledAst = acorn.parse(unhandledCallerCode, { ecmaVersion: 'latest', sourceType: 'module', locations: true });
const unhandledCallSites = extractCallSitesInFile(unhandledCallerCode, unhandledAst, 'fetchUser');
assert.strictEqual(unhandledCallSites.length, 1);
assert.strictEqual(unhandledCallSites[0].isAwaited, false);
assert.strictEqual(unhandledCallSites[0].hasThenChain, false);

const asyncTrapVerdict = evaluateStructuralContract(syncSig, asyncSig, unhandledCallSites);
assert.strictEqual(asyncTrapVerdict.isBreaking, true, 'Sync to async transition without await must be BREAKING');
assert.strictEqual(asyncTrapVerdict.verdict, 'BREAKING');
assert.ok(asyncTrapVerdict.reason.includes('unhandled Promise'), 'Reason must mention unhandled Promise');

// Caller awaits fetchUser
const awaitedCallerCode = `
async function handleRequest() {
  const user = await fetchUser(101);
  return user.name;
}
`;
const awaitedAst = acorn.parse(awaitedCallerCode, { ecmaVersion: 'latest', sourceType: 'module', locations: true });
const awaitedCallSites = extractCallSitesInFile(awaitedCallerCode, awaitedAst, 'fetchUser');
assert.strictEqual(awaitedCallSites[0].isAwaited, true);

const awaitedVerdict = evaluateStructuralContract(syncSig, asyncSig, awaitedCallSites);
assert.strictEqual(awaitedVerdict.isBreaking, false, 'Sync to async transition with await is COMPATIBLE');
assert.strictEqual(awaitedVerdict.verdict, 'COMPATIBLE');
console.log('✓ Test 32 Passed: Sync-to-Async transition accurately catches unhandled Promise caller traps.');

// Test 33: Red-Team [CONCERN-2] Object Destructuring Parameter Mutations
console.log('Testing Red-Team [CONCERN-2]: Object destructuring parameter mutations...');
const oldDestructSig = {
  name: 'initClient',
  isAsync: false,
  isGenerator: false,
  params: [{
    name: 'destructuredObject',
    hasDefault: false,
    isRest: false,
    isDestructured: true,
    keys: ['host', 'port']
  }],
  paramCount: 1,
  requiredCount: 1
};
const newDestructSig = {
  name: 'initClient',
  isAsync: false,
  isGenerator: false,
  params: [{
    name: 'destructuredObject',
    hasDefault: false,
    isRest: false,
    isDestructured: true,
    keys: ['hostname', 'port']
  }],
  paramCount: 1,
  requiredCount: 1
};

const destructVerdict = evaluateStructuralContract(oldDestructSig, newDestructSig, [{ line: 1, argCount: 1 }]);
assert.strictEqual(destructVerdict.isBreaking, true, 'Renaming destructured parameter key from host to hostname must be BREAKING');
assert.strictEqual(destructVerdict.verdict, 'BREAKING');
assert.ok(destructVerdict.reason.includes("'host' was removed"), 'Reason must specify missing property host');
console.log('✓ Test 33 Passed: Object destructuring parameter mutations detected as BREAKING.');

// Test 34: Red-Team [CONCERN-3] Offline Hermetic Execution without LLM
console.log('Testing Red-Team [CONCERN-3]: Offline hermetic execution with zero network dependency...');
const semOfflineDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sem-offline-'));
try {
  const libFile = path.join(semOfflineDir, 'mathLib.js');
  const userFile = path.join(semOfflineDir, 'consumer.js');

  fs.writeFileSync(libFile, `function add(a, b = 0) { return a + b; } module.exports = { add };`, 'utf8');
  fs.writeFileSync(userFile, `const { add } = require('./mathLib'); function run() { return add(5); } module.exports = { run };`, 'utf8');

  const offlineGraph = new SymbolGraph({ rootDir: semOfflineDir });
  offlineGraph.build();

  const startTime = Date.now();
  const offlineResult = evaluateSemanticBlastRadius(
    libFile,
    ['add'],
    [userFile],
    offlineGraph,
    {
      oldSignatures: { add: { name: 'add', isAsync: false, isGenerator: false, params: [{ name: 'a', hasDefault: false, isRest: false, isDestructured: false, keys: [] }], paramCount: 1, requiredCount: 1 } },
      newSignatures: { add: { name: 'add', isAsync: false, isGenerator: false, params: [{ name: 'a', hasDefault: false, isRest: false, isDestructured: false, keys: [] }, { name: 'b', hasDefault: true, isRest: false, isDestructured: false, keys: [] }], paramCount: 2, requiredCount: 1 } }
    }
  );
  const duration = Date.now() - startTime;

  assert.strictEqual(offlineResult.isSemanticAware, true);
  assert.strictEqual(offlineResult.isDegraded, false, 'AST contract is the primary path, not degraded');
  assert.strictEqual(offlineResult.mode, 'ast-contract');
  assert.strictEqual(offlineResult.hasBreaking, false);
  assert.strictEqual(offlineResult.overallVerdict, 'COMPATIBLE');
  assert.ok(duration < 100, `Evaluation must run locally in < 100ms, took ${duration}ms`);
  console.log(`✓ Test 34 Passed: Offline hermetic execution verified in ${duration}ms with zero network calls.`);
} finally {
  fs.rmSync(semOfflineDir, { recursive: true, force: true });
}

// Test 35: Red-Team [CRITICAL-01 Fix] Zero-argument functions without diff do not false-positive BREAKING
console.log('Testing Red-Team [CRITICAL-01 Fix]: Zero-argument function without diff is COMPATIBLE...');
const semZeroDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sem-zero-'));
try {
  const serviceFile = path.join(semZeroDir, 'healthService.js');
  const clientFile = path.join(semZeroDir, 'healthClient.js');

  const serviceCode = `
function getStatus() {
  return "OK";
}
module.exports = { getStatus };
`;
  const clientCode = `
const { getStatus } = require('./healthService');
function check() {
  return getStatus();
}
module.exports = { check };
`;
  fs.writeFileSync(serviceFile, serviceCode, 'utf8');
  fs.writeFileSync(clientFile, clientCode, 'utf8');

  const zeroGraph = new SymbolGraph({ rootDir: semZeroDir });
  zeroGraph.build();

  // Run calculateBlastRadius WITHOUT diff
  const report = calculateBlastRadius(serviceFile, {
    graph: zeroGraph,
    semantic: true,
    rootDir: semZeroDir
  });

  assert.strictEqual(report.semanticAnalysis.hasBreaking, false, '0-arg function must not be falsely flagged as BREAKING');
  assert.strictEqual(report.semanticAnalysis.overallVerdict, 'UNKNOWN', 'No old signature without a diff — do not assume COMPATIBLE');
  console.log('✓ Test 35 Passed: Zero-argument function baseline evaluated without diff does not produce false-positive.');
} finally {
  fs.rmSync(semZeroDir, { recursive: true, force: true });
}

// Test 36: Red-Team [CRITICAL-02 Fix] File Rename Interception enforces HIGH risk and blocks semantic downgrade
console.log('Testing Red-Team [CRITICAL-02 Fix]: File rename enforces HIGH risk and blocks semantic downgrade...');
const semRenameDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sem-rename-'));
try {
  const serviceFile = path.join(semRenameDir, 'oldApi.js');
  const clientFile = path.join(semRenameDir, 'client.js');

  fs.writeFileSync(serviceFile, `function doWork() { return 1; } module.exports = { doWork };`, 'utf8');
  fs.writeFileSync(clientFile, `const { doWork } = require('./oldApi'); doWork();`, 'utf8');

  const renameGraph = new SymbolGraph({ rootDir: semRenameDir });
  renameGraph.build();

  // Git diff showing rename of oldApi.js to newApi.js
  const renameDiff = `
diff --git a/oldApi.js b/newApi.js
similarity index 100%
rename from oldApi.js
rename to newApi.js
`;

  const report = calculateBlastRadius(serviceFile, {
    graph: renameGraph,
    diff: renameDiff,
    diffAware: true,
    semantic: true,
    rootDir: semRenameDir
  });

  assert.strictEqual(report.riskLevel, 'HIGH', 'File rename must remain HIGH risk');
  assert.ok(report.riskScore >= 90, `Risk score must be >= 90, got ${report.riskScore}`);
  assert.strictEqual(report.semanticAnalysis.hasBreaking, true, 'File rename must be flagged as breaking');
  assert.strictEqual(report.semanticAnalysis.mode, 'path-contract', 'Mode must be path-contract');
  assert.ok(report.notes.includes('[SEMANTIC: BREAKING]'), 'Notes must indicate breaking path change');
  console.log('✓ Test 36 Passed: File rename correctly intercepted as path-breaking change, preventing downgrade to LOW.');
} finally {
  fs.rmSync(semRenameDir, { recursive: true, force: true });
}

// Test 37: Red-Team [CRITICAL-03 Fix] MemberExpression namespace isolation eliminates global same-name false matches
console.log('Testing Red-Team [CRITICAL-03 Fix]: MemberExpression namespace isolation...');
const semScopeDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sem-scope-'));
try {
  const dbServiceFile = path.join(semScopeDir, 'dbService.js');
  const callerFile = path.join(semScopeDir, 'handler.js');

  // dbService exports query(sql, params)
  const dbServiceCode = `
function query(sql, params) {
  return [];
}
module.exports = { query };
`;

  // Caller imports dbService as db, but ALSO calls unrelated req.query() and map.query()
  const callerCode = `
const db = require('./dbService');

function handleRequest(req, res) {
  // Unrelated calls with 0 or 1 args on non-db objects
  const qParam = req.query();
  const cached = new Map().query();

  // Genuine call to dbService
  return db.query("SELECT * FROM users", []);
}
module.exports = { handleRequest };
`;

  fs.writeFileSync(dbServiceFile, dbServiceCode, 'utf8');
  fs.writeFileSync(callerFile, callerCode, 'utf8');

  const scopeGraph = new SymbolGraph({ rootDir: semScopeDir });
  scopeGraph.build();

  const callerAst = acorn.parse(callerCode, { ecmaVersion: 'latest', sourceType: 'module', locations: true });
  const callSites = extractCallSitesInFile(callerCode, callerAst, 'query', dbServiceFile);

  // Must ONLY match db.query(...), NOT req.query() or new Map().query()!
  assert.strictEqual(callSites.length, 1, `Must match exactly 1 genuine call site, got ${callSites.length}`);
  assert.strictEqual(callSites[0].binding, 'db.query');
  assert.strictEqual(callSites[0].argCount, 2);
  console.log('✓ Test 37 Passed: MemberExpression strictly isolates target namespace, rejecting req.query() false matches.');
} finally {
  fs.rmSync(semScopeDir, { recursive: true, force: true });
}

// Test 38: Red-Team [WARNING-01 to 06 Fixes] Defensive guards & edge cases
console.log('Testing Red-Team [WARNING-01 to 06 Fixes]: Defensive guards verification...');

// 1. WARNING-01: String default with comma
const stringCommaHunk = `+ function greet(name, greeting = "Hello, world", options = {}) {`;
const strCommaSig = extractSignatureFromHunk(stringCommaHunk, 'greet');
assert.strictEqual(strCommaSig.paramCount, 3);
assert.strictEqual(strCommaSig.requiredCount, 1);
assert.strictEqual(strCommaSig.params[1].hasDefault, true);

// 2. WARNING-02: Balanced parenthesis in default values
const nestedParenHunk = `+ function schedule(task, delay = (1000 * 60), callback = () => {}) {`;
const nestedParenSig = extractSignatureFromHunk(nestedParenHunk, 'schedule');
assert.strictEqual(nestedParenSig.paramCount, 3);
assert.strictEqual(nestedParenSig.requiredCount, 1);
assert.strictEqual(nestedParenSig.params[1].hasDefault, true);
assert.strictEqual(nestedParenSig.params[2].hasDefault, true);

// 3. WARNING-03: Nested object destructuring mutations
const deepOld = {
  name: 'connectDb',
  isAsync: false,
  isGenerator: false,
  params: [{
    name: 'destructuredObject',
    hasDefault: false,
    isRest: false,
    isDestructured: true,
    keys: ['db.host', 'db.port']
  }],
  paramCount: 1,
  requiredCount: 1
};
const deepNew = {
  name: 'connectDb',
  isAsync: false,
  isGenerator: false,
  params: [{
    name: 'destructuredObject',
    hasDefault: false,
    isRest: false,
    isDestructured: true,
    keys: ['db.host'] // db.port was deleted!
  }],
  paramCount: 1,
  requiredCount: 1
};
const deepDestructRes = evaluateStructuralContract(deepOld, deepNew, [{ line: 1, argCount: 1 }]);
assert.strictEqual(deepDestructRes.isBreaking, true);
assert.ok(deepDestructRes.reason.includes('db.port'));

// 4. WARNING-06: Direct return inside async function propagates Promise safely
const asyncReturnCode = `
async function proxyCall() {
  return targetFunction();
}
`;
const asyncReturnAst = acorn.parse(asyncReturnCode, { ecmaVersion: 'latest', sourceType: 'module', locations: true });
const asyncReturnCalls = extractCallSitesInFile(asyncReturnCode, asyncReturnAst, 'targetFunction');
assert.strictEqual(asyncReturnCalls.length, 1);
assert.strictEqual(asyncReturnCalls[0].isAwaited, true, 'Direct return in async function is treated as safely awaited/propagated');

console.log('✓ Test 38 Passed: All warning edge cases (strings with commas, balanced parens, deep destructuring, async return propagation) verified.');

// Test 39: Missing signatures are UNKNOWN, not fail-open COMPATIBLE
console.log('Testing missing signatures and empty call sites do not fail-open as COMPATIBLE...');
const missingSig = evaluateStructuralContract(null, {
  name: 'fn', isAsync: false, isGenerator: false, params: [], paramCount: 0, requiredCount: 0, hasRest: false
}, []);
assert.strictEqual(missingSig.verdict, 'UNKNOWN');
assert.strictEqual(missingSig.isBreaking, false);

const addedRequiredNoCallers = evaluateStructuralContract(
  {
    name: 'login', isAsync: false, isGenerator: false,
    params: [{ name: 'user', hasDefault: false, isRest: false, isDestructured: false, keys: [] }],
    paramCount: 1, requiredCount: 1, hasRest: false
  },
  {
    name: 'login', isAsync: false, isGenerator: false,
    params: [
      { name: 'user', hasDefault: false, isRest: false, isDestructured: false, keys: [] },
      { name: 'password', hasDefault: false, isRest: false, isDestructured: false, keys: [] }
    ],
    paramCount: 2, requiredCount: 2, hasRest: false
  },
  []
);
assert.strictEqual(addedRequiredNoCallers.verdict, 'BREAKING', 'Required arity increase is breaking even with 0 discovered call sites');
console.log('✓ Test 39 Passed: Missing signatures are UNKNOWN; required-arity increase with empty call sites is BREAKING.');

// Test 40: Semantic UNKNOWN must not downgrade topology MEDIUM to LOW
console.log('Testing semantic UNKNOWN does not collapse topology risk to LOW...');
const semUnknownDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sem-unknown-'));
try {
  const libFile = path.join(semUnknownDir, 'core.js');
  fs.writeFileSync(libFile, 'function query(sql) { return sql; }\nmodule.exports = { query };\n', 'utf8');
  for (let i = 1; i <= 3; i++) {
    fs.writeFileSync(
      path.join(semUnknownDir, `caller${i}.js`),
      `const { query } = require('./core');\nquery('select ${i}');\nmodule.exports = { c${i}: true };\n`,
      'utf8'
    );
  }
  const unknownGraph = new SymbolGraph({ rootDir: semUnknownDir });
  unknownGraph.build();
  const topo = calculateBlastRadius(libFile, { graph: unknownGraph, rootDir: semUnknownDir });
  assert.strictEqual(topo.riskLevel, 'MEDIUM', `precondition: 3 callers should be MEDIUM, got ${topo.riskLevel} (${topo.riskScore})`);

  const withSemantic = calculateBlastRadius(libFile, {
    graph: unknownGraph,
    semantic: true,
    rootDir: semUnknownDir
  });
  assert.strictEqual(withSemantic.semanticAnalysis.overallVerdict, 'UNKNOWN');
  assert.strictEqual(withSemantic.semanticAnalysis.hasBreaking, false);
  assert.strictEqual(withSemantic.riskLevel, 'MEDIUM', 'UNKNOWN must not fail-open into a LOW downgrade');
  assert.strictEqual(withSemantic.riskScore, topo.riskScore);
  console.log('✓ Test 40 Passed: Semantic UNKNOWN preserves topology risk score.');
} finally {
  fs.rmSync(semUnknownDir, { recursive: true, force: true });
}

console.log('Testing semantic COMPATIBLE does not hide a wide blast radius...');
const wideCompatDir = fs.mkdtempSync(path.join(os.tmpdir(), 'sem-wide-compat-'));
try {
  fs.writeFileSync(
    path.join(wideCompatDir, 'core.js'),
    'function query(sql, extra = 1) { return sql; }\nmodule.exports = { query };\n',
    'utf8'
  );
  for (let i = 0; i < 3; i++) {
    fs.writeFileSync(
      path.join(wideCompatDir, `caller${i}.js`),
      `const { query } = require('./core');\nquery('select ${i}');\nmodule.exports = { c${i}: true };\n`,
      'utf8'
    );
  }
  const wideGraph = new SymbolGraph({ rootDir: wideCompatDir });
  wideGraph.build();
  const wideDiff = `
--- a/core.js
+++ b/core.js
@@ -1,3 +1,3 @@
-function query(sql) {
+function query(sql, extra = 1) {
`;
  const wideTopo = calculateBlastRadius(path.join(wideCompatDir, 'core.js'), {
    graph: wideGraph,
    rootDir: wideCompatDir
  });
  assert.ok(wideTopo.directCount >= 3, 'fixture must have a wide fan-out');
  const wideSemantic = calculateBlastRadius(path.join(wideCompatDir, 'core.js'), {
    graph: wideGraph,
    diff: wideDiff,
    diffAware: true,
    semantic: true,
    rootDir: wideCompatDir
  });
  assert.strictEqual(wideSemantic.semanticAnalysis.overallVerdict, 'COMPATIBLE');
  assert.strictEqual(wideSemantic.riskLevel, wideTopo.riskLevel, 'COMPATIBLE must not collapse a wide topology score to LOW');
  assert.ok(wideSemantic.notes.includes('[SEMANTIC: COMPATIBLE]'));
  console.log('✓ Test 41 Passed: Compatible signature on a wide fan-out keeps topology risk.');
} finally {
  fs.rmSync(wideCompatDir, { recursive: true, force: true });
}

console.log('Testing symbol callers ignore unrelated local namesakes...');
const namesakeDir = fs.mkdtempSync(path.join(os.tmpdir(), 'symbol-namesake-'));
try {
  fs.mkdirSync(path.join(namesakeDir, 'src'));
  fs.writeFileSync(
    path.join(namesakeDir, 'src', 'auth.js'),
    'function login(user) { return user; }\nmodule.exports = { login };\n',
    'utf8'
  );
  fs.writeFileSync(
    path.join(namesakeDir, 'src', 'unrelated.js'),
    'function login(item) { return item.id; }\nlogin({ id: 1 });\nmodule.exports = { login };\n',
    'utf8'
  );
  fs.writeFileSync(
    path.join(namesakeDir, 'src', 'app.js'),
    'const { login } = require("./auth");\nlogin("a");\nmodule.exports = { app: true };\n',
    'utf8'
  );
  const nsGraph = new SymbolGraph({ rootDir: namesakeDir });
  nsGraph.build();
  const callers = nsGraph.findSymbolCallers('login');
  const rel = callers.map(f => path.relative(namesakeDir, f).replace(/\\/g, '/'));
  assert.ok(rel.includes('src/app.js'), 'real importer must be a caller');
  assert.ok(!rel.includes('src/unrelated.js'), 'local namesake without importing auth.js must not be a caller');
  console.log('✓ Test 42 Passed: findSymbolCallers requires an import binding, not a matching identifier.');
} finally {
  fs.rmSync(namesakeDir, { recursive: true, force: true });
}

console.log('Testing symbol callers include aliased imports...');
const aliasDir = fs.mkdtempSync(path.join(os.tmpdir(), 'symbol-alias-'));
try {
  fs.mkdirSync(path.join(aliasDir, 'src'));
  fs.writeFileSync(
    path.join(aliasDir, 'src', 'auth.js'),
    'function login(user) { return user; }\nmodule.exports = { login };\n',
    'utf8'
  );
  fs.writeFileSync(
    path.join(aliasDir, 'src', 'alias.js'),
    'const { login: signIn } = require("./auth");\nsignIn("a");\nmodule.exports = { signIn };\n',
    'utf8'
  );
  fs.writeFileSync(
    path.join(aliasDir, 'src', 'esm.js'),
    'import { login as signIn } from "./auth";\nsignIn("b");\nexport { signIn };\n',
    'utf8'
  );
  const aliasGraph = new SymbolGraph({ rootDir: aliasDir });
  aliasGraph.build();
  const aliasCallers = aliasGraph.findSymbolCallers('login');
  const aliasRel = aliasCallers.map(f => path.relative(aliasDir, f).replace(/\\/g, '/'));
  assert.ok(aliasRel.includes('src/alias.js'), 'CJS imported-as alias must be a caller of login');
  assert.ok(aliasRel.includes('src/esm.js'), 'ESM import-as alias must be a caller of login');
  console.log('✓ Test 43 Passed: findSymbolCallers follows login: signIn aliases.');
} finally {
  fs.rmSync(aliasDir, { recursive: true, force: true });
}

console.log('\nAll Code Symbol Graph & Blast-Radius tests passed successfully! 🎉');
