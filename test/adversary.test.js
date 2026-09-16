const assert = require('assert');
const { AdversaryAuditor } = require('../.agents/scripts/adversary-check');

console.log('Running Adversarial Auditor Unit Tests...');

const auditor = new AdversaryAuditor();

// Test 1: Catches tests modifying real .git
const testGitDiff = `
diff --git a/test/bad.test.js b/test/bad.test.js
+ const hookPath = path.resolve(__dirname, '../.git/hooks/pre-push');
`;
const r1 = auditor.auditDiff(testGitDiff);
assert.strictEqual(r1.passed, false, 'Should fail when test mutates real .git');
assert.ok(r1.findings.some(f => f.lens === 'Hermetic Test Isolation'));
console.log('✓ Test 1 Passed: Auditor catches non-hermetic test touching real .git.');

// Test 2: Catches greedy regex lacking boundaries
const badRegexDiff = `
diff --git a/src/scanner.js b/src/scanner.js
+ const regex: /sk-[a-zA-Z0-9]{20,}/;
`;
const r2 = auditor.auditDiff(badRegexDiff);
assert.strictEqual(r2.passed, false, 'Should fail on greedy regex');
assert.ok(r2.findings.some(f => f.lens === 'Regex Boundary & False-Positive Defense'));
console.log('✓ Test 2 Passed: Auditor flags greedy regex without word boundary defenses.');

// Test 3: Catches tracked runtime db / html
const trackedArtifactDiff = `
diff --git a/agent-workflow.html b/agent-workflow.html
new file mode 100644
`;
const r3 = auditor.auditDiff(trackedArtifactDiff);
assert.ok(r3.findings.some(f => f.lens === 'VCS & Artifact Hygiene'));
console.log('✓ Test 3 Passed: Auditor warns on tracked generated HTML/database files.');

// Test 4: Clean diff passes cleanly
const cleanDiff = `
diff --git a/src/math.js b/src/math.js
+ function add(a, b) { return a + b; }
+ module.exports = { add };
`;
const r4 = auditor.auditDiff(cleanDiff);
assert.strictEqual(r4.passed, true);
assert.strictEqual(r4.findings.length, 0);
console.log('✓ Test 4 Passed: Clean diff passes auditor with zero findings.');

// Test 5: Skips self-referential fixtures inside test/adversary.test.js
const selfReferentialDiff = `
diff --git a/test/adversary.test.js b/test/adversary.test.js
+ const testGitDiff = 'path.resolve(__dirname, "../.git/hooks")';
+ const badRegex = 'regex: /sk-[a-zA-Z0-9]{20,}/';
+ const tracked = 'diff --git a/agent-workflow.html b/agent-workflow.html';
`;
const r5 = auditor.auditDiff(selfReferentialDiff);
assert.strictEqual(r5.passed, true, 'Should not fail on self-referential auditor test fixtures');
assert.strictEqual(r5.findings.length, 0);
console.log('✓ Test 5 Passed: Auditor ignores test fixture lines inside test/adversary.test.js.');

// Test 6: default getDiff scans the working tree vs HEAD, not only HEAD~1..HEAD
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

console.log('Testing default getDiff uses working tree against HEAD...');
const advRepo = fs.mkdtempSync(path.join(os.tmpdir(), 'adv-diff-'));
const git = (args) => spawnSync('git', args, { cwd: advRepo, encoding: 'utf8' });
assert.strictEqual(git(['init']).status, 0);
git(['config', 'user.email', 'test@example.com']);
git(['config', 'user.name', 'Test']);
fs.writeFileSync(path.join(advRepo, 'tracked.js'), 'const v = 1;\n');
git(['add', '.']);
assert.strictEqual(git(['commit', '-m', 'one']).status, 0);
fs.writeFileSync(path.join(advRepo, 'tracked.js'), 'const v = 2;\n');
git(['add', '.']);
assert.strictEqual(git(['commit', '-m', 'two']).status, 0);
fs.writeFileSync(path.join(advRepo, 'tracked.js'), 'const v = "NEW_WORKTREE_MARKER";\n');

const worktreeAuditor = new AdversaryAuditor({ repoRoot: advRepo, staged: false });
const worktreeDiff = worktreeAuditor.getDiff();
assert.ok(worktreeDiff.includes('NEW_WORKTREE_MARKER'), 'default scan must include uncommitted working-tree changes');
assert.ok(!/HEAD~1/.test(worktreeDiff));
fs.rmSync(advRepo, { recursive: true, force: true });
console.log('✓ Test 6 Passed: Default adversary diff is git diff HEAD (working tree), not HEAD~1..HEAD.');

console.log('Testing ACID check ignores unchanged INSERT context...');
const contextInsertsDiff = `
diff --git a/src/memory/db.js b/src/memory/db.js
--- a/src/memory/db.js
+++ b/src/memory/db.js
@@ -40,8 +40,9 @@
     const stmt = this.db.prepare('INSERT INTO episodes (id) VALUES (?)');
     const other = this.db.prepare('INSERT INTO reflections (id) VALUES (?)');
+    const comment = 'touching a file that already has two inserts';
`;
const r7 = auditor.auditDiff(contextInsertsDiff);
assert.strictEqual(
  r7.findings.some(f => f.lens === 'ACID Transaction Completeness'),
  false,
  'context INSERT lines must not trigger the ACID warning'
);

const addedInsertsDiff = `
diff --git a/src/memory/db.js b/src/memory/db.js
--- a/src/memory/db.js
+++ b/src/memory/db.js
@@ -1,2 +1,4 @@
+    const stmt = this.db.prepare('INSERT INTO episodes (id) VALUES (?)');
+    const other = this.db.prepare('INSERT INTO reflections (id) VALUES (?)');
`;
const r8 = auditor.auditDiff(addedInsertsDiff);
assert.ok(r8.findings.some(f => f.lens === 'ACID Transaction Completeness'), 'two added INSERTs without transaction() must warn');
console.log('✓ Test 7 Passed: ACID check only inspects added lines, not hunk context.');

console.log('\nAll Adversarial Auditor tests passed successfully! 🎉');

