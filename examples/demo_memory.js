const { getMemory } = require('../src/memory/index');
const { seedDefaultMemories } = require('../src/memory/seed');

const mem = getMemory();

console.log('====================================================');
console.log('  🧠 Long-Term Reflexive Memory: System Demo');
console.log('====================================================\n');

// 1. Seed Audited Golden Heuristics
console.log('📥 Seeding 3 vetted golden heuristics into Reflexion Memory...');
seedDefaultMemories(mem);

console.log('\n📊 Current Memory Database Stats:');
console.log(mem.stats());

// 2. Scenario 1: Relevant query on SQLite in sandbox
console.log('\n----------------------------------------------------');
console.log('🔍 Scenario 1: Agent receives task "install better-sqlite3 native compilation"');
console.log('----------------------------------------------------');
const query1 = 'install better-sqlite3 native compilation';
const lessons1 = mem.query(query1, { autoIncrementHit: true });
console.log(mem.formatPrompt(lessons1, { showScores: true }));

// 3. Scenario 2: Relevant query on MCP plugin isolation
console.log('\n----------------------------------------------------');
console.log('🔍 Scenario 2: Agent receives task "configure MCP server plugin project scope"');
console.log('----------------------------------------------------');
const query2 = 'configure MCP server plugin project scope';
const lessons2 = mem.query(query2, { autoIncrementHit: true });
console.log(mem.formatPrompt(lessons2, { showScores: true }));

// 4. Scenario 3: Unrelated query to verify zero-pollution
console.log('\n----------------------------------------------------');
console.log('🛡️ Scenario 3: Anti-Pollution Verification: "how to build a navigation bar"');
console.log('----------------------------------------------------');
const query3 = 'how to build a navigation bar';
const lessons3 = mem.query(query3);
console.log(mem.formatPrompt(lessons3, { showScores: true }) || '✅ Zero pollution: No irrelevant heuristics injected.');

// 5. Rule Promotion Check
console.log('\n----------------------------------------------------');
console.log('📈 Rule Promotion Candidate Check (Memory Consolidation):');
console.log('----------------------------------------------------');
const candidates = mem.getCandidateRules(1);
for (const c of candidates) {
  console.log(c.ruleText);
}

console.log('\n✅ Demo complete! Reflexion Memory active and unpolluted.');
