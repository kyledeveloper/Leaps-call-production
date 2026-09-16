const path = require('path');
const { getToolmaker } = require('../src/toolmaker/index');
const { spawnSync } = require('child_process');

console.log('====================================================');
console.log('  🛠️  AI Agent Self-Toolmaker: System Demo');
console.log('====================================================\n');

const toolmaker = getToolmaker();

// 1. Simulate recurring high-frequency task: comparing locale keys
console.log('📊 Simulating high-frequency task tracking: "Audit locale key discrepancies"...');
const taskName = 'audit-locales';
const taskIntent = 'Compare translation keys between zh-CN and en-US to report missing keys';

console.log('• Execution #1: Ad-hoc manual script execution...');
toolmaker.track({ nameSlug: taskName, intentSummary: taskIntent });

console.log('• Execution #2: Repeating ad-hoc shell command...');
toolmaker.track({ nameSlug: taskName, intentSummary: taskIntent });

console.log('• Execution #3: Triggering threshold (>= 3 times)!');
const check = toolmaker.track({ nameSlug: taskName, intentSummary: taskIntent });

if (check.shouldSynthesize) {
  console.log('\n🔔 [Toolmaker Triggered] Detected task performed 3 times, initiating tool synthesis!');
}

// 2. Synthesize dedicated CLI script: .agents/scripts/audit-locales.js
console.log('\n⚙️ Synthesizing dedicated CLI tool: .agents/scripts/audit-locales.js ...');

const auditToolCode = `
    function getFlattenedKeys(obj, prefix = '') {
      let keys = [];
      for (const [k, v] of Object.entries(obj)) {
        const fullKey = prefix ? prefix + '.' + k : k;
        if (v && typeof v === 'object' && !Array.isArray(v)) {
          keys = keys.concat(getFlattenedKeys(v, fullKey));
        } else {
          keys.push(fullKey);
        }
      }
      return keys;
    }

    const baseDir = path.resolve(process.cwd(), args.base || 'locales/zh-CN');
    const targetDir = path.resolve(process.cwd(), args.target || 'locales/en-US');

    if (!fs.existsSync(baseDir)) {
      throw new Error(\`Base locale dir not found: \${baseDir}\`);
    }
    if (!fs.existsSync(targetDir)) {
      throw new Error(\`Target locale dir not found: \${targetDir}\`);
    }

    const baseFiles = fs.readdirSync(baseDir).filter(f => f.endsWith('.json'));
    const report = {
      baseLocale: path.basename(baseDir),
      targetLocale: path.basename(targetDir),
      filesAudited: baseFiles.length,
      discrepancies: []
    };

    for (const file of baseFiles) {
      const basePath = path.join(baseDir, file);
      const targetPath = path.join(targetDir, file);

      if (!fs.existsSync(targetPath)) {
        report.discrepancies.push({ file, type: 'MISSING_FILE', message: \`Target file \${file} missing\` });
        continue;
      }

      const baseJson = JSON.parse(fs.readFileSync(basePath, 'utf8'));
      const targetJson = JSON.parse(fs.readFileSync(targetPath, 'utf8'));

      const baseKeys = getFlattenedKeys(baseJson);
      const targetKeys = getFlattenedKeys(targetJson);

      const missingInTarget = baseKeys.filter(k => !targetKeys.includes(k));
      const extraInTarget = targetKeys.filter(k => !baseKeys.includes(k));

      if (missingInTarget.length > 0 || extraInTarget.length > 0) {
        report.discrepancies.push({
          file,
          missingInTarget,
          extraInTarget
        });
      }
    }

    if (args.json) {
      console.log(JSON.stringify(report, null, 2));
    } else {
      console.log(\`=== 🌐 Locale Key Audit Result (\${report.baseLocale} ➔ \${report.targetLocale}) ===\`);
      console.log(\`Files Audited: \${report.filesAudited}\`);
      if (report.discrepancies.length === 0) {
        console.log('✅ All locale keys are 100% synchronized with zero missing entries.');
      } else {
        console.log(\`⚠️ Found \${report.discrepancies.length} discrepancy item(s):\`);
        for (const d of report.discrepancies) {
          console.log(\`  • File: \${d.file}\`);
          if (d.missingInTarget) console.log(\`    Missing Keys: \${d.missingInTarget.join(', ')}\`);
          if (d.extraInTarget) console.log(\`    Extra Keys: \${d.extraInTarget.join(', ')}\`);
        }
      }
    }
`;

const synthesized = toolmaker.synthesizeTool({
  name: taskName,
  description: 'Audit and compare translation keys across locale files (e.g. zh-CN vs en-US)',
  parameters: [
    { name: 'base', description: 'Base locale directory (e.g. locales/zh-CN)', default: 'locales/zh-CN' },
    { name: 'target', description: 'Target locale directory (e.g. locales/en-US)', default: 'locales/en-US' }
  ],
  codeBody: auditToolCode,
  examples: [
    '--base locales/zh-CN --target locales/en-US',
    '--json'
  ],
  patternHash: check.patternHash
});

console.log(`✅ Tool successfully synthesized: ${synthesized.scriptPath}`);

// 3. Test running the synthesized tool via unified runner
console.log('\n🚀 Testing execution via unified dispatcher: node .agents/scripts/runner.js audit-locales');
const runnerPath = path.join(__dirname, '../.agents/scripts/runner.js');
const runResult = spawnSync(process.execPath, [runnerPath, 'audit-locales'], {
  stdio: 'inherit',
  cwd: path.join(__dirname, '../')
});

console.log('\n📋 Registered Script Assets Catalog:');
const tools = toolmaker.listTools();
for (const t of tools) {
  console.log(`• [${t.name}] - ${t.description} (Script: ${t.scriptPath})`);
}

console.log('\n🎉 Demo complete! In future tasks, the Agent can directly run:');
console.log('   node .agents/scripts/runner.js audit-locales\n');
