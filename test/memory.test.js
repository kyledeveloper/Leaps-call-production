const assert = require('assert');
const { LongTermMemory } = require('../src/memory/index');

console.log('Running Long-Term Memory Unit Tests...');

const mem = new LongTermMemory(':memory:');

// Test 1: Initial stats
const initialStats = mem.stats();
assert.strictEqual(initialStats.episodeCount, 0, 'Initial episodes should be 0');
assert.strictEqual(initialStats.reflectionCount, 0, 'Initial reflections should be 0');
console.log('✓ Test 1 Passed: Initial empty database verified.');

// Test 2: Record an experience (simulating better-sqlite3 native build failure)
const res1 = mem.recordExperience({
  intent: 'Install context-mode global or local package',
  context_summary: 'Node 24, macOS Darwin arm64, IDE sandbox',
  domain_tags: ['npm', 'native-build', 'permission'],
  status: 'failure',
  trigger_pattern: 'npm install context-mode or better-sqlite3',
  failure_mode: 'EPERM operation not permitted on node-gyp rebuild scandir build',
  root_cause: 'Sandbox environment restricts underlying C++ native module compilation',
  corrective_heuristic: 'Avoid building native C++ addons inside sandbox; prefer npx or ask user to install in system terminal'
});

assert.ok(res1.id.startsWith('ref_'), 'Reflection ID should start with ref_');
assert.strictEqual(res1.reinforced, false, 'Should be a new reflection');

const statsAfterAdd = mem.stats();
assert.strictEqual(statsAfterAdd.episodeCount, 1);
assert.strictEqual(statsAfterAdd.reflectionCount, 1);
console.log('✓ Test 2 Passed: Record experience successfully stored.');

// Test 3: Deduplication / reinforcement on repeating same trigger
const res2 = mem.recordExperience({
  intent: 'Retry npm install context-mode',
  domain_tags: ['npm'],
  status: 'failure',
  trigger_pattern: 'npm install context-mode or better-sqlite3',
  root_cause: 'Sandbox environment restricts underlying C++ native module compilation',
  corrective_heuristic: 'Avoid building native C++ addons inside sandbox; prefer npx or ask user to install in system terminal'
});

assert.strictEqual(res2.reinforced, true, 'Duplicate pattern should reinforce instead of creating duplicate');
assert.strictEqual(mem.stats().reflectionCount, 1, 'Reflection count should remain 1 after reinforcement');
console.log('✓ Test 3 Passed: Deduplication and reinforcement working properly.');

// Test 3b: Same heuristic, different trigger → two reflections (do not merge on heuristic text)
const resOtherTrigger = mem.recordExperience({
  intent: 'Write project-scoped MCP config',
  domain_tags: ['mcp'],
  status: 'failure',
  trigger_pattern: 'write global mcp_config.json',
  root_cause: 'Global config leaks across workspaces',
  corrective_heuristic: 'Avoid building native C++ addons inside sandbox; prefer npx or ask user to install in system terminal'
});
assert.strictEqual(resOtherTrigger.reinforced, false, 'Different trigger_pattern must not collapse onto an existing heuristic');
assert.strictEqual(mem.stats().reflectionCount, 2, 'Distinct triggers should create a second reflection');
console.log('✓ Test 3b Passed: Dedup keys on trigger_pattern only, not heuristic text.');

// Test 3c: Reinforcing the same trigger refreshes the stored diagnosis
const resUpdated = mem.recordExperience({
  intent: 'Retry npm install context-mode with a better post-mortem',
  domain_tags: ['npm', 'native-build'],
  status: 'recovered',
  trigger_pattern: 'npm install context-mode or better-sqlite3',
  failure_mode: 'EPERM node-gyp',
  root_cause: 'Sandbox still blocks node-gyp; use the runtime sqlite binding',
  corrective_heuristic: 'Use node:sqlite DatabaseSync instead of compiling better-sqlite3'
});
assert.strictEqual(resUpdated.reinforced, true, 'Same trigger must still dedup');
assert.strictEqual(mem.stats().reflectionCount, 2, 'Diagnosis refresh must not insert a third row');
const refreshed = mem.query('npm install better-sqlite3 error');
assert.ok(refreshed[0].corrective_heuristic.includes('node:sqlite'), 'FTS and row must carry the latest heuristic');
console.log('✓ Test 3c Passed: Reinforcement updates root cause and heuristic instead of freezing the first diagnosis.');

// Test 4: Full-text search retrieval (FTS5)
const queryResults = mem.query('npm install better-sqlite3 error');
assert.ok(queryResults.length > 0, 'Should find matching lesson by keywords');
assert.strictEqual(queryResults[0].id, res1.id);
console.log('✓ Test 4 Passed: FTS5 keyword matching retrieved relevant reflection.');

// Test 5: Prompt formatting
const formattedPrompt = mem.formatPrompt(queryResults);
assert.ok(formattedPrompt.includes('Historical Reflexion Guidance'), 'Formatted prompt should include English header');
assert.ok(formattedPrompt.includes('Heuristic Advice'), 'Formatted prompt should contain heuristic advice');
console.log('✓ Test 5 Passed: Prompt formatting produces high-density markdown.');

// Test 6: Rule promotion counts failure recurrences, not retrieval hits
const hitBeforeQuery = mem.db.db.prepare('SELECT hit_count FROM reflections WHERE id = ?').get(res1.id).hit_count;
for (let i = 0; i < 3; i++) {
  mem.query('better-sqlite3', { autoIncrementHit: true });
}
const hitAfterQuery = mem.db.db.prepare('SELECT hit_count FROM reflections WHERE id = ?').get(res1.id).hit_count;
assert.strictEqual(hitAfterQuery, hitBeforeQuery, 'autoIncrementHit must not treat recall as a failure recurrence');

mem.recordExperience({
  intent: 'Third recurrence of native sqlite install failure',
  trigger_pattern: 'npm install context-mode or better-sqlite3',
  root_cause: 'Sandbox still blocks node-gyp; use the runtime sqlite binding',
  corrective_heuristic: 'Use node:sqlite DatabaseSync instead of compiling better-sqlite3'
});
const candidates = mem.getCandidateRules(3);
assert.ok(candidates.length > 0, 'Should have candidate rules when hit_count >= 3');
assert.ok(candidates[0].ruleText.includes('Bypass count:'), 'Rule text should format English metadata');
console.log('✓ Test 6 Passed: High-frequency memory consolidation / rule promotion candidates identified.');

// Test 7: Multi-dimensional score breakdown & mathematical verification
const scoredResults = mem.query('npm install better-sqlite3');
assert.ok(scoredResults.length > 0);
const topItem = scoredResults[0];
assert.ok(typeof topItem.score === 'number' && topItem.score > 0, 'Final score should be a positive number');
assert.ok(typeof topItem.relScore === 'number' && topItem.relScore >= 0 && topItem.relScore <= 1, 'relScore should be in [0, 1]');
assert.ok(typeof topItem.recScore === 'number' && topItem.recScore >= 0 && topItem.recScore <= 1, 'recScore should be in [0, 1]');
assert.ok(typeof topItem.impScore === 'number' && topItem.impScore >= 0 && topItem.impScore <= 1, 'impScore should be in [0, 1]');

// Default weights: alpha=0.5, beta=0.2, gamma=0.3
const expectedScore = 0.5 * topItem.relScore + 0.2 * topItem.recScore + 0.3 * topItem.impScore;
assert.ok(Math.abs(topItem.score - expectedScore) < 0.001, `Final score ${topItem.score} should match formula ${expectedScore}`);
console.log('✓ Test 7 Passed: Multi-dimensional score breakdown and mathematical formula verified.');

// Test 8: Recency exponential decay verification
const thirtyDaysAgo = Date.now() - (30 * 24 * 3600 * 1000);
mem.db.insertEpisode({
  id: 'ep_old',
  intent: 'Legacy build test',
  status: 'recovered',
  created_at: thirtyDaysAgo
});
mem.db.insertReflection({
  id: 'ref_old',
  episode_id: 'ep_old',
  intent: 'Legacy build test',
  trigger_pattern: 'npm install legacy-module-old',
  root_cause: 'Unsupported on modern runtime',
  corrective_heuristic: 'Avoid installation',
  created_at: thirtyDaysAgo,
  last_accessed_at: thirtyDaysAgo
});

const recentVsOldQuery = mem.query('legacy-module-old');
assert.strictEqual(recentVsOldQuery[0].id, 'ref_old');
assert.ok(recentVsOldQuery[0].recScore < 0.3, `30-day old record should have decayed recency (got ${recentVsOldQuery[0].recScore})`);
console.log(`✓ Test 8 Passed: Exponential recency decay verified (old recScore = ${recentVsOldQuery[0].recScore}).`);

// Test 9: Importance score differentiation
mem.recordExperience({
  intent: 'Minor warning test',
  trigger_pattern: 'vite build minor warning (minor-warning-trigger)',
  root_cause: 'Unused variable',
  corrective_heuristic: 'Clean unused imports',
  importance_score: 0.3
});

mem.recordExperience({
  intent: 'Critical crash test',
  trigger_pattern: 'vite build system crash (critical-crash-trigger)',
  root_cause: 'Out of memory OOM',
  corrective_heuristic: 'Increase node max heap size parameter',
  importance_score: 1.0
});

const minorResult = mem.query('minor-warning-trigger');
const criticalResult = mem.query('critical-crash-trigger');
assert.ok(criticalResult[0].impScore > minorResult[0].impScore, `Critical importance (${criticalResult[0].impScore}) should exceed minor (${minorResult[0].impScore})`);
console.log(`✓ Test 9 Passed: Importance weighting verified (critical: ${criticalResult[0].impScore} vs minor: ${minorResult[0].impScore}).`);

// Test 10: Dynamic weights override
const recencyFocusedResults = mem.query('legacy-module-old', {
  weights: { alpha: 0.1, beta: 0.8, gamma: 0.1 }
});
assert.ok(recencyFocusedResults[0].score < recentVsOldQuery[0].score, 'Higher beta should penalize old memories more');
console.log('✓ Test 10 Passed: Dynamic weights adjustment verified.');

// Test 11: Auto-refresh last_accessed_at on retrieval hit
const nowAccessTime = Date.now();
mem.query('legacy-module-old', { autoIncrementHit: true, now: nowAccessTime });
const afterAccess = mem.db.db.prepare('SELECT last_accessed_at FROM reflections WHERE id = ?').get('ref_old').last_accessed_at;
assert.strictEqual(afterAccess, nowAccessTime, 'autoIncrementHit should update last_accessed_at to current timestamp');
console.log('✓ Test 11 Passed: Retrieval hit refreshes last_accessed_at activation.');

// Test 12: Episode + reflection + FTS insert roll back together
const beforeRollback = mem.stats();
const originalInsert = mem.db.insertReflection.bind(mem.db);
mem.db.insertReflection = () => {
  throw new Error('simulated fts failure');
};
assert.throws(
  () => mem.recordExperience({
    intent: 'Should roll back',
    trigger_pattern: 'unique-rollback-trigger',
    root_cause: 'injected failure',
    corrective_heuristic: 'transaction must drop the episode too'
  }),
  /simulated fts failure/
);
mem.db.insertReflection = originalInsert;
const afterRollback = mem.stats();
assert.strictEqual(afterRollback.episodeCount, beforeRollback.episodeCount, 'episode insert must roll back with FTS failure');
assert.strictEqual(afterRollback.reflectionCount, beforeRollback.reflectionCount, 'reflection count must be unchanged after rollback');
const leaked = mem.query('unique-rollback-trigger');
assert.strictEqual(leaked.length, 0, 'rolled-back FTS rows must not be searchable');
console.log('✓ Test 12 Passed: recordExperience wraps episode/reflection/FTS in one transaction.');

// Test 13: Cold-start golden memories seeding & idempotency
const freshMem = new LongTermMemory(':memory:');
const seedResult1 = freshMem.seed();
assert.strictEqual(seedResult1.length, 3, 'Should seed exactly 3 golden memories');
assert.strictEqual(freshMem.stats().reflectionCount, 3);

// Idempotent seeding check
const seedResult2 = freshMem.seed();
assert.strictEqual(freshMem.stats().reflectionCount, 3, 'Re-seeding should not duplicate records');
assert.ok(seedResult2.every(r => r.reinforced === true), 'Subsequent seeds should reinforce existing entries');
console.log('✓ Test 13 Passed: Cold-start golden memory seeding is idempotent.');

// Test 14: Anti-pollution verification on unrelated queries
const unrelatedResults = freshMem.query('how to build a navigation bar');
assert.strictEqual(unrelatedResults.length, 0, 'Unrelated queries must return zero results to avoid prompt pollution');

const relevantSqlite = freshMem.query('install better-sqlite3 native compilation');
assert.ok(relevantSqlite.length > 0, 'Relevant task must retrieve targeted heuristic');
assert.ok(relevantSqlite[0].corrective_heuristic.includes('node:sqlite'), 'Retrieved heuristic must advise native node:sqlite');
console.log('✓ Test 14 Passed: Anti-pollution filter prevents unrelated prompt contamination.');

freshMem.close();
mem.close();
console.log('\nAll Long-Term Memory tests passed successfully! 🎉');

