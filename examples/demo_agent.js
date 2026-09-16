const { AgentLoop } = require('../src/agent/index');

const agent = new AgentLoop();

console.log('====================================================');
console.log('  Unified Agent Loop Demo');
console.log('====================================================\n');

console.log('1) plan() — retrieve memory + suggest tools\n');
const plan = agent.plan('install sqlite native compilation addon');
if (plan.guidance) {
  console.log(plan.guidance);
} else {
  console.log('(no matching lessons yet — run examples/demo_memory.js first)');
}
console.log('\nSuggested tools:');
if (plan.suggestedTools.length === 0) {
  console.log('  (none)');
} else {
  for (const t of plan.suggestedTools) {
    console.log(`  • ${t.name} — ${t.description}`);
  }
}

console.log('\n2) plan() with blast-radius for a refactor\n');
const blast = agent.plan('refactor MemoryDatabase', { target: 'src/memory/db.js' });
if (blast.blastRadius) {
  const br = blast.blastRadius;
  console.log(`  ${br.target}  ${br.riskLevel} (${br.riskScore}/100)`);
  console.log(`  direct=${br.directCount}  tests=${br.testCount}`);
}

console.log('\n3) run() audit-locales through the loop\n');
const ran = agent.run('audit locale keys between zh-CN and en-US', {
  tool: 'audit-locales',
  args: []
});
if (ran.result) {
  console.log(`  exit ${ran.result.status}  recorded=${ran.recorded ? ran.recorded.id : 'no'}`);
  if (ran.result.stdout) {
    console.log(ran.result.stdout);
  }
}

console.log('\nDone. Next failure you hit: node src/agent/index.js reflect --intent ... --trigger ... --cause ... --heuristic ...');
agent.close();
