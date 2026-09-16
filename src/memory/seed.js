/**
 * Reflexion Memory Cold-Start Seeds
 *
 * Contains vetted, zero-pollution engineering heuristics specific to
 * the Node 24 runtime, IDE sandboxes, and hermetic project testing.
 */


const GOLDEN_MEMORIES = [
  {
    intent: 'Use SQLite database without triggering native addon compilation failures',
    context_summary: 'Node 24+, macOS arm64/Linux, IDE sandbox isolation',
    domain_tags: ['sqlite', 'native-addons', 'node24', 'sandbox'],
    status: 'recovered',
    trigger_pattern: 'install sqlite native compilation node-gyp better-sqlite3 rebuild',
    failure_mode: 'node-gyp rebuild EPERM: operation not permitted inside IDE sandbox',
    root_cause: 'IDE sandbox intercepts low-level syscalls required by node-gyp; building native C++ addons fails',
    corrective_heuristic: 'Never compile native addons in sandbox. In Node 24, use zero-dependency built-in node:sqlite (DatabaseSync) directly without npm installs. If native compilation is mandatory, delegate to user terminal.',
    confidence_score: 1.0,
    importance_score: 0.95
  },
  {
    intent: 'Configure project-scoped MCP server plugin avoiding global pollution',
    context_summary: 'Antigravity workspace plugin customization',
    domain_tags: ['mcp', 'plugin', 'antigravity', 'isolation'],
    status: 'recovered',
    trigger_pattern: 'configure MCP server plugin project scope workspace mcp_config',
    failure_mode: 'Writing ~/.gemini/config/mcp_config.json pollutes all workspaces globally',
    root_cause: 'Antigravity global config path applies across all projects; project-scoped configs require explicit local manifest paths',
    corrective_heuristic: 'Isolate MCP configs strictly in project root under .agents/plugins/<name>/plugin.json and workspace mcp_config.json. Never mutate ~/.gemini/config/ directly.',
    confidence_score: 1.0,
    importance_score: 0.85
  },
  {
    intent: 'Author hermetic unit/integration tests involving filesystem writes or git hooks',
    context_summary: 'TDD local offline testing with git hooks',
    domain_tags: ['testing', 'isolation', 'tmpdir', 'git-hooks'],
    status: 'recovered',
    trigger_pattern: 'unit test file write git hook mutation filesystem sandbox',
    failure_mode: 'Modifying workspace root or real .git corrupts repository state and fails adversary checks',
    root_cause: 'Tests running in-place leak dirty artifacts and trip pre-push security hooks',
    corrective_heuristic: 'Strictly isolate filesystem/git tests inside os.tmpdir() using fs.mkdtempSync(). Never mutate root .git. Always clean up in afterEach/finally using fs.rmSync(..., { recursive: true, force: true }).',
    confidence_score: 1.0,
    importance_score: 0.90
  }
];

function seedDefaultMemories(mem) {
  const results = [];
  for (const item of GOLDEN_MEMORIES) {
    const res = mem.recordExperience(item);
    results.push(res);
  }
  return results;
}

if (require.main === module) {
  const { getMemory } = require('./index');
  const mem = getMemory();
  console.log('Seeding Reflexion Memory with verified golden heuristics...');
  const seeded = seedDefaultMemories(mem);
  for (let i = 0; i < seeded.length; i++) {
    const s = seeded[i];
    const prefix = s.reinforced ? '🔄 Reinforced' : '🌱 Seeded';
    console.log(`${prefix}: [${GOLDEN_MEMORIES[i].intent}] -> ${s.id}`);
  }
  console.log('Current stats:', mem.stats());
}

module.exports = {
  GOLDEN_MEMORIES,
  seedDefaultMemories
};
