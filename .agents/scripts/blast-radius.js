#!/usr/bin/env node
/**
 * Blast-Radius Analysis CLI Tool
 * 
 * Analyzes the impact scope and blast radius of modifying a file or symbol.
 * 
 * Usage:
 *   node .agents/scripts/blast-radius.js --target <fileOrSymbol> [options]
 *   node .agents/scripts/runner.js blast-radius --target src/memory/db.js
 */

const path = require('path');
const { calculateBlastRadius } = require(path.resolve(__dirname, '../../src/graph/blastRadius'));
const i18n = require(path.resolve(__dirname, '../../src/i18n'));

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--help' || arg === '-h') {
      args.help = true;
    } else if (arg === '--json') {
      args.json = true;
    } else if (arg === '--tree') {
      args.tree = true;
    } else if (arg.startsWith('--')) {
      const rawKey = arg.slice(2);
      if (rawKey.includes('=')) {
        const eqIdx = rawKey.indexOf('=');
        args[rawKey.slice(0, eqIdx)] = rawKey.slice(eqIdx + 1);
      } else if (i + 1 < argv.length && !argv[i + 1].startsWith('--')) {
        args[rawKey] = argv[++i];
      } else {
        args[rawKey] = true;
      }
    } else {
      args._.push(arg);
    }
  }
  return args;
}

function printHelp() {
  console.log(`
Code Symbol Graph & Blast-Radius Analysis Tool
==============================================
Usage:
  node .agents/scripts/blast-radius.js --target <fileOrSymbol> [options]

Options:
  --target <path|name>   Target file path or symbol identifier (required)
  --diff                 Enable Git Diff-aware analysis (converges private changes to LOCAL_PRIVATE)
  --semantic             Enable Stage 2 Semantic Contract evaluation (breaking change inference)
  --tree                 Render hierarchical ASCII dependency tree
  --json                 Output machine-readable JSON format
  --lang <lang>          Language ('en-US' or 'zh-CN', defaults to $LANG)
  --max-depth <N>        Maximum depth for transitive dependency search (default: 10)
  --help, -h             Show this help message

Examples:
  node .agents/scripts/runner.js blast-radius --target src/memory/db.js
  node .agents/scripts/runner.js blast-radius --target src/memory/db.js --diff
  node .agents/scripts/runner.js blast-radius --target src/memory/db.js --diff --semantic
  node .agents/scripts/runner.js blast-radius --target MemoryDatabase --tree
  node .agents/scripts/runner.js blast-radius --target calculateScore --json
`);
}

function renderTree(report, rootDir) {
  console.log(`\n📌 Blast Radius Tree: [${report.targetType.toUpperCase()}] ${report.target}`);
  if (report.isDiffAware && report.scope === 'LOCAL_PRIVATE') {
    console.log('└── 🟢 (Changes strictly confined to internal private scope - 0 downstream regressions)');
    return;
  }
  if (report.directFiles.length === 0) {
    console.log('└── (No downstream dependents found - Safe isolated change)');
    return;
  }

  report.directFiles.forEach((df, idx) => {
    const isLast = idx === report.directFiles.length - 1 && report.indirectFiles.length === 0;
    const branch = isLast ? '└── ' : '├── ';
    console.log(`${branch} [Direct] ${path.relative(rootDir, df)}`);
  });

  if (report.indirectFiles.length > 0) {
    report.indirectFiles.forEach((inf, idx) => {
      const isLast = idx === report.indirectFiles.length - 1;
      const branch = isLast ? '└── ' : '├── ';
      console.log(`${branch} [Indirect] ${path.relative(rootDir, inf)}`);
    });
  }

  if (report.impactedTests.length > 0) {
    console.log('\n🧪 Impacted Test Suites to Verify:');
    report.impactedTests.forEach(t => {
      console.log(`  • node ${path.relative(rootDir, t)}`);
    });
  }
}

async function run() {
  const args = parseArgs(process.argv.slice(2));

  if (args.help || (!args.target && args._.length === 0)) {
    printHelp();
    process.exit(0);
  }

  const target = args.target || args._[0];
  const rootDir = path.resolve(__dirname, '../../');

  // Configure i18n
  const systemLang = process.env.LANG || process.env.LANGUAGE || '';
  const selectedLang = args.lang
    ? (args.lang.startsWith('zh') ? 'zh-CN' : 'en-US')
    : (systemLang.includes('zh') ? 'zh-CN' : 'en-US');
  i18n.changeLanguage(selectedLang);

  const maxDepth = parseInt(args['max-depth'] || '10', 10);
  const diffAware = !!args.diff;
  const semantic = !!args.semantic;
  const report = calculateBlastRadius(target, { rootDir, maxDepth, diffAware, semantic });

  if (args.json) {
    console.log(JSON.stringify(report, null, 2));
    process.exit(0);
  }

  if (args.tree) {
    renderTree(report, rootDir);
    process.exit(0);
  }

  // Standard output format
  console.log(i18n.t('cli.blast.header'));
  console.log(i18n.t('cli.blast.target', { target: report.target, type: report.targetType }));

  if (report.isDiffAware) {
    const scopeEmoji = report.scope === 'LOCAL_PRIVATE' ? '🟢 [LOCAL_PRIVATE]' : (report.scope === 'CLEAN' ? '⚪ [CLEAN]' : '🔵 [PUBLIC_CONTRACT]');
    console.log(`Diff Scope: ${scopeEmoji}`);
    if (report.notes) console.log(`Note: ${report.notes}`);
  }

  if (report.semanticAnalysis && report.semanticAnalysis.isSemanticAware) {
    const sVerdict = report.semanticAnalysis.hasBreaking ? '🔴 [BREAKING CHANGE]' : '🟢 [COMPATIBLE]';
    console.log(`Semantic Analysis: ${sVerdict} (${report.semanticAnalysis.mode})`);
    if (report.semanticAnalysis.evaluations && report.semanticAnalysis.evaluations.length > 0) {
      report.semanticAnalysis.evaluations.forEach(e => {
        const vTag = e.isBreaking ? '🔴' : '🟢';
        console.log(`  ${vTag} ${e.callerFile}: ${e.reason}`);
        if (e.suggestedRemediation) {
          console.log(`    ↳ Action: ${e.suggestedRemediation}`);
        }
      });
    }
  }

  const riskEmoji = report.riskLevel === 'HIGH' ? '🔴' : (report.riskLevel === 'MEDIUM' ? '🟡' : '🟢');
  console.log(i18n.t('cli.blast.risk_level', { level: `${riskEmoji} ${report.riskLevel}`, score: report.riskScore }));
  console.log();

  if (report.directCount === 0 && report.indirectCount === 0) {
    console.log(i18n.t('cli.blast.no_impact'));
  } else {
    console.log(i18n.t('cli.blast.direct_impact', { count: report.directCount }));
    report.directFiles.forEach(f => {
      console.log(`  • ${path.relative(rootDir, f)}`);
    });

    if (report.indirectCount > 0) {
      console.log();
      console.log(i18n.t('cli.blast.indirect_impact', { count: report.indirectCount }));
      report.indirectFiles.forEach(f => {
        console.log(`  • ${path.relative(rootDir, f)}`);
      });
    }

    console.log();
    console.log(i18n.t('cli.blast.impacted_tests', { count: report.testCount }));
    report.impactedTests.forEach(t => {
      console.log(`  • ${path.relative(rootDir, t)}`);
    });
  }

  console.log();
  console.log(i18n.t('cli.blast.safety_plan_header'));
  report.safetyPlan.steps.forEach(s => {
    console.log(`  ${s}`);
  });
  console.log();
}

if (require.main === module) {
  run();
}

module.exports = { run };
