const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

console.log('Running Pre-Push Gatekeeper Unit Tests...');

const scriptPath = path.resolve(__dirname, '../.agents/scripts/pre-push-check.js');
const installerPath = path.resolve(__dirname, '../.agents/scripts/install-git-hooks.js');

// Require script module functions
let gatekeeper;
try {
  gatekeeper = require(scriptPath);
} catch (e) {
  gatekeeper = null;
}

assert.ok(gatekeeper, 'pre-push-check.js module should be exportable and loadable');

// Dynamically construct test tokens so static test files do not trigger git diff scanners
const fakeGhp = ['ghp', '1234567890abcdefghijklmnopqrstuvwxyz'].join('_');
const fakeOpenAI = ['sk', 'abcdefghijklmnopqrstuvwxyz1234567890'].join('-');
const fakeAws = ['AKIA', 'IOSFODNN7EXAMPLE'].join('');
const fakePrivKey = ['-----BEGIN ', 'RSA PRIVATE KEY-----'].join('');

// Test 1: Secret Scanning - Positive Matches
console.log('Testing Secret Detection patterns...');
const secretDiffSamples = [
  `const token = "${fakeGhp}";`,
  `export const OPENAI_KEY = "${fakeOpenAI}";`,
  `AWS_KEY_ID="${fakeAws}"`,
  `${fakePrivKey}\nMIIEowIBAAKCAQEA0...`
];

for (const sample of secretDiffSamples) {
  const result = gatekeeper.scanSecrets(`+ ${sample}`);
  assert.strictEqual(result.hasSecrets, true, `Should detect secret in: ${sample}`);
  assert.ok(result.findings.length > 0, 'Findings should contain details');
}
console.log('✓ Test 1 Passed: Secret scanner successfully detects API keys, tokens, and private keys.');

// Test 2: Secret Scanning - Negative (Clean code)
console.log('Testing Secret Detection on clean code...');
const cleanDiffSample = `
+ function calculateTotal(items) {
+   return items.reduce((sum, item) => sum + item.price, 0);
+ }
+ const placeholderKey = "YOUR_API_KEY_HERE";
`;
const cleanResult = gatekeeper.scanSecrets(cleanDiffSample);
assert.strictEqual(cleanResult.hasSecrets, false, 'Clean code should have no secrets detected');
console.log('✓ Test 2 Passed: Clean code produces zero false positives.');

// Test 2b: OpenAI key pattern must not match substrings like disk- or task-sk-
console.log('Testing OpenAI key false positives (disk-/task-sk- prefixes)...');
const diskFalse = gatekeeper.scanSecrets('+ const path = "disk-abcdefghijklmnopqrstuvwxyz";');
assert.strictEqual(diskFalse.hasSecrets, false, 'disk-... must not be flagged as an OpenAI key');
const taskSkFalse = gatekeeper.scanSecrets('+ const id = "task-sk-abcdefghijklmnopqrstuvwxyz";');
assert.strictEqual(taskSkFalse.hasSecrets, false, 'task-sk-... must not be flagged as an OpenAI key');
const realKey = gatekeeper.scanSecrets(`+ export const OPENAI_KEY = "${fakeOpenAI}";`);
assert.strictEqual(realKey.hasSecrets, true, 'A standalone sk- key must still be detected');
console.log('✓ Test 2b Passed: OpenAI key regex no longer matches disk-/task-sk- substrings.');

// Test 2c: Inline data URIs (e.g. base64 fonts or images) must not be flagged as high-entropy secrets
console.log('Testing base64 data URI false positives (fonts/images)...');
const fontDataUriDiff = '+      src: url(data:font/woff2;base64,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) format("woff2");';
const fontResult = gatekeeper.scanSecrets(fontDataUriDiff);
assert.strictEqual(fontResult.hasSecrets, false, 'Inline base64 font data URI must not be flagged as secret');
const imgDataUriDiff = '+   const img = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==";';
const imgResult = gatekeeper.scanSecrets(imgDataUriDiff);
assert.strictEqual(imgResult.hasSecrets, false, 'Inline base64 image data URI must not be flagged as secret');
console.log('✓ Test 2c Passed: Base64 data URIs produce zero false positives.');

// Test 3: Code Smell & Complexity Analysis
console.log('Testing Code Smell Detection (function length and nesting)...');
const longFunctionContent = `
function veryLongFunction() {
${Array.from({ length: 90 }, (_, i) => `  const step${i} = ${i};`).join('\n')}
  return step89;
}
`;
const deeplyNestedContent = `
function deeplyNested() {
  if (true) {
    for (let i = 0; i < 10; i++) {
      while (i < 5) {
        if (i % 2 === 0) {
          if (true) {
            console.log("Too deep!");
          }
        }
      }
    }
  }
}
`;

const smellLong = gatekeeper.analyzeComplexity(longFunctionContent, 'longFunc.js');
assert.ok(smellLong.warnings.some(w => w.type === 'LONG_FUNCTION'), 'Should warn on long function > 80 lines');

const smellDeep = gatekeeper.analyzeComplexity(deeplyNestedContent, 'nested.js');
assert.ok(smellDeep.warnings.some(w => w.type === 'DEEP_NESTING'), 'Should warn on nesting depth > 4');
console.log('✓ Test 3 Passed: Code smell analyzer identifies long functions and excessive nesting.');

// Test 4: CLI Execution with Hardcoded Secret (Exit Code 1)
console.log('Testing CLI execution with hardcoded secret detection...');
const secretRun = spawnSync(process.execPath, [scriptPath, '--scan-text', fakeOpenAI], {
  encoding: 'utf8'
});
assert.strictEqual(secretRun.status, 1, 'CLI should exit with code 1 when hardcoded secrets are present');
console.log('✓ Test 4 Passed: CLI correctly blocks execution (exit code 1) on secrets.');

// Test 5: Git Hook Installer Verification (isolated temp repo — never touch the real .git)
console.log('Testing Git Hook Installer...');
const { installHooks } = require(installerPath);
const tmpRepo = fs.mkdtempSync(path.join(os.tmpdir(), 'pre-push-hooks-'));
try {
  fs.mkdirSync(path.join(tmpRepo, '.git', 'hooks'), { recursive: true });
  const hookFilePath = installHooks({ repoRoot: tmpRepo });
  assert.strictEqual(hookFilePath, path.join(tmpRepo, '.git', 'hooks', 'pre-push'));
  assert.ok(fs.existsSync(hookFilePath), 'pre-push hook must be written under the supplied repoRoot');
  const hookContent = fs.readFileSync(hookFilePath, 'utf8');
  assert.ok(hookContent.includes('pre-push-check'), 'Hook script should call pre-push-check');
  console.log('✓ Test 5 Passed: Git hook installer writes an executable pre-push hook into an isolated repo.');
} finally {
  fs.rmSync(tmpRepo, { recursive: true, force: true });
}

// Test 6: Diff Deletion Test - Removing a secret must NOT block push
console.log('Testing Diff Deletion (removing old secrets should not be flagged)...');
const deletedSecretDiff = `
diff --git a/config.js b/config.js
--- a/config.js
+++ b/config.js
@@ -1,2 +1,2 @@
-const oldKey = "${fakeOpenAI}";
+const newKey = process.env.API_KEY;
`;
const deletedRes = gatekeeper.scanSecrets(deletedSecretDiff);
assert.strictEqual(deletedRes.hasSecrets, false, 'Deleted secrets in diff should not trigger blocking');
console.log('✓ Test 6 Passed: Removing old secrets does not trigger false positive.');

// Test 7: Secret in Comments - Should be caught and blocked
console.log('Testing Secret in comments (must be caught)...');
const commentedSecret = `+ // Note: temporary token is ${fakeGhp}`;
const commentRes = gatekeeper.scanSecrets(commentedSecret);
assert.strictEqual(commentRes.hasSecrets, true, 'Secrets inside comments must be detected');
console.log('✓ Test 7 Passed: Sensitive credentials in code comments are caught.');

// Test 8: String literals containing braces should not cause false DEEP_NESTING
console.log('Testing string literals with braces for complexity analysis...');
const bracesInStringContent = `
function formatData() {
  const jsonTemplate = "{ item1: { nested: { deep: true } } }";
  return jsonTemplate;
}
`;
const bracesRes = gatekeeper.analyzeComplexity(bracesInStringContent, 'template.js');
assert.strictEqual(bracesRes.warnings.length, 0, 'Braces in string literals must not cause false DEEP_NESTING');
console.log('✓ Test 8 Passed: String literals with braces do not distort cyclomatic depth.');

// Test 9: Sensitive file detection (.env, .pem, .key)
console.log('Testing Sensitive File Detection...');
const sensitiveFilesResult = gatekeeper.checkSensitiveFiles([
  '.env',
  '.env.local',
  '.env.production',
  'server.key',
  'id_rsa',
  'README.md',
  '.env.example'
]);
assert.strictEqual(sensitiveFilesResult.hasSensitiveFiles, true);
assert.strictEqual(sensitiveFilesResult.findings.length, 5); // .env, .env.local, .env.production, server.key, id_rsa (excludes .env.example)
console.log('✓ Test 9 Passed: Sensitive files (.env, key, id_rsa) correctly detected while .env.example allowed.');

console.log('Testing Shannon entropy scanning...');
const highEntropyToken = 'n8QvL2wXk9Zm4RtY7pHs1BcD6gAj3UfE';
assert.ok(gatekeeper.isHighEntropySecret(highEntropyToken), 'mixed high-entropy token must be flagged');
assert.strictEqual(gatekeeper.isHighEntropySecret('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'), false, 'low-entropy repeats must not flag');
assert.strictEqual(gatekeeper.isHighEntropySecret('0123456789abcdef0123456789abcdef01234567'), false, 'hex blobs must not flag as secrets');
const entropyHit = gatekeeper.scanSecrets(`+ const token = "${highEntropyToken}";`);
assert.strictEqual(entropyHit.hasSecrets, true, 'high-entropy assignment must block');
assert.ok(entropyHit.findings.some(f => f.type === 'High-entropy secret'));
console.log('✓ Test 10 Passed: Shannon entropy scanner flags mixed high-entropy tokens and ignores hex/repeats.');

console.log('Testing nested functions do not close the outer function early...');
const nestedLong = `
function outerLong() {
  function innerTiny() {
    return 1;
  }
${Array.from({ length: 90 }, (_, i) => `  const step${i} = ${i};`).join('\n')}
  return innerTiny();
}
`;
const nestedSmell = gatekeeper.analyzeComplexity(nestedLong, 'nestedLong.js');
assert.ok(nestedSmell.warnings.some(w => w.type === 'LONG_FUNCTION' && w.message.includes('outerLong')), 'outer function length must still be measured past the inner function');
console.log('✓ Test 11 Passed: Nested functions do not truncate outer LONG_FUNCTION tracking.');

console.log('Testing hook installer refuses to fabricate .git...');
const notARepo = fs.mkdtempSync(path.join(os.tmpdir(), 'no-git-'));
try {
  assert.throws(() => installHooks({ repoRoot: notARepo }), /Not a git repository/);
  assert.strictEqual(fs.existsSync(path.join(notARepo, '.git')), false, 'must not create a fake .git directory');
  console.log('✓ Test 12 Passed: Hook installer does not fabricate .git outside a repository.');
} finally {
  fs.rmSync(notARepo, { recursive: true, force: true });
}

console.log('Testing outgoing diff scans the full unpushed history without remotes...');
const rangeRepo = fs.mkdtempSync(path.join(os.tmpdir(), 'pre-push-range-'));
try {
  const git = (args) => spawnSync('git', args, { cwd: rangeRepo, encoding: 'utf8' });
  assert.strictEqual(git(['init']).status, 0);
  git(['config', 'user.email', 'test@example.com']);
  git(['config', 'user.name', 'Test']);
  fs.writeFileSync(path.join(rangeRepo, 'a.js'), `const first = "${highEntropyToken}";\n`);
  git(['add', '.']);
  assert.strictEqual(git(['commit', '-m', 'one']).status, 0);
  fs.writeFileSync(path.join(rangeRepo, 'b.js'), 'const second = 2;\n');
  git(['add', '.']);
  assert.strictEqual(git(['commit', '-m', 'two']).status, 0);
  fs.writeFileSync(path.join(rangeRepo, 'c.js'), 'const third = 3;\n');
  git(['add', '.']);
  assert.strictEqual(git(['commit', '-m', 'three']).status, 0);

  const origCwd = process.cwd();
  process.chdir(rangeRepo);
  try {
    const ctx = gatekeeper.resolveGitContext();
    assert.ok(Array.isArray(ctx.diffArgs) && ctx.diffArgs.length >= 1, 'diff args must be an argv array');
    assert.ok(!ctx.diffArgs.includes('HEAD~1..HEAD') && ctx.diffArgs.join(' ') !== 'HEAD~1..HEAD');
    const outgoing = gatekeeper.getOutgoingDiff();
    assert.ok(outgoing.includes(highEntropyToken), 'first-commit secret must still be in the unpushed scan');
    assert.ok(outgoing.includes('third') || outgoing.includes('b.js') || outgoing.includes('second'), 'later commits must be included');
  } finally {
    process.chdir(origCwd);
  }
  console.log('✓ Test 13 Passed: No-remote fallback scans full local history, not only HEAD~1.');
} finally {
  fs.rmSync(rangeRepo, { recursive: true, force: true });
}

console.log('Testing pre-push stdin uses the commits being pushed, not origin/main...HEAD...');
const pushRepo = fs.mkdtempSync(path.join(os.tmpdir(), 'pre-push-stdin-'));
try {
  const git = (args) => spawnSync('git', args, { cwd: pushRepo, encoding: 'utf8' });
  assert.strictEqual(git(['init']).status, 0);
  git(['config', 'user.email', 'test@example.com']);
  git(['config', 'user.name', 'Test']);
  fs.writeFileSync(path.join(pushRepo, 'clean.js'), 'const ok = 1;\n');
  git(['add', '.']);
  assert.strictEqual(git(['commit', '-m', 'base']).status, 0);
  const shaA = git(['rev-parse', 'HEAD']).stdout.trim();

  fs.writeFileSync(path.join(pushRepo, 'secret.js'), `const alreadyPushed = "${highEntropyToken}";\n`);
  git(['add', '.']);
  assert.strictEqual(git(['commit', '-m', 'secret already on remote']).status, 0);
  const shaB = git(['rev-parse', 'HEAD']).stdout.trim();

  fs.writeFileSync(path.join(pushRepo, 'next.js'), 'const fresh = 3;\n');
  git(['add', '.']);
  assert.strictEqual(git(['commit', '-m', 'new unpushed commit']).status, 0);
  const shaC = git(['rev-parse', 'HEAD']).stdout.trim();

  git(['update-ref', 'refs/remotes/origin/main', shaA]);
  git(['update-ref', 'refs/remotes/origin/feature', shaB]);
  git(['checkout', '-B', 'feature']);

  const stdin = `refs/heads/feature ${shaC} refs/heads/feature ${shaB}\n`;
  const ctx = gatekeeper.resolveGitContext({ stdin, repoRoot: pushRepo });
  assert.deepStrictEqual(ctx.diffArgs, [shaB, shaC], 'hook stdin must scan remote_sha..local_sha');
  const pushed = gatekeeper.getOutgoingDiff({ stdin, repoRoot: pushRepo });
  assert.ok(pushed.includes('fresh'), 'new commit must be in the push scan');
  assert.ok(!pushed.includes(highEntropyToken), 'already-pushed secret must not be re-scanned');

  const fallback = gatekeeper.resolveGitContext({ stdin: '', repoRoot: pushRepo });
  assert.ok(
    String(fallback.diffArgs.join(' ')).includes('origin/main') || String(fallback.diffArgs.join(' ')).includes('origin/HEAD'),
    'manual CLI without stdin still falls back to origin/main'
  );
  const wide = gatekeeper.getOutgoingDiff({ stdin: '', repoRoot: pushRepo });
  assert.ok(wide.includes(highEntropyToken), 'origin/main...HEAD includes already-pushed feature history');

  const created = gatekeeper.parsePushStdin(
    `refs/heads/feature ${shaC} refs/heads/new ${'0'.repeat(40)}\n`
  );
  assert.strictEqual(created.length, 1);
  assert.strictEqual(created[0][0], gatekeeper.EMPTY_TREE);
  assert.strictEqual(created[0][1], shaC);
  console.log('✓ Test 15 Passed: Hook stdin range is remote_sha..local_sha, not origin/main...HEAD.');
} finally {
  fs.rmSync(pushRepo, { recursive: true, force: true });
}

console.log('Testing object braces do not hide nested control flow...');
const objectInsideIfs = `
function f() {
  if (a) {
    const o = { x: 1 };
    if (b) {
      if (c) {
        if (d) {
          if (e) {
            return 1;
          }
        }
      }
    }
  }
}
`;
const objectSmell = gatekeeper.analyzeComplexity(objectInsideIfs, 'object-if.js');
assert.ok(
  objectSmell.warnings.some(w => w.type === 'DEEP_NESTING'),
  'five nested ifs with an object literal must still warn DEEP_NESTING'
);

const elseIfChain = `
function g() {
  if (a) { return 1; }
  else if (b) { return 2; }
  else if (c) { return 3; }
  else if (d) { return 4; }
  else if (e) { return 5; }
  else if (f) { return 6; }
}
`;
const elseIfSmell = gatekeeper.analyzeComplexity(elseIfChain, 'elseif.js');
assert.ok(
  !elseIfSmell.warnings.some(w => w.type === 'DEEP_NESTING'),
  'a flat else-if chain must not be treated as deep nesting'
);
console.log('✓ Test 14 Passed: Control-flow depth ignores object braces and else-if chains.');

console.log('\nAll Pre-Push Gatekeeper tests passed successfully! 🎉');
