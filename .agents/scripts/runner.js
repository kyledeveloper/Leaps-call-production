#!/usr/bin/env node
/**
 * Unified Agent Tool Runner & Dispatcher
 *
 * Usage:
 *   node .agents/scripts/runner.js <tool-name> [options]
 *   node .agents/scripts/runner.js --list
 */

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const REGISTRY_PATH = path.join(__dirname, 'registry.json');

function loadRegistry() {
  if (!fs.existsSync(REGISTRY_PATH)) return { version: 1, tools: {} };
  try {
    return JSON.parse(fs.readFileSync(REGISTRY_PATH, 'utf8'));
  } catch (e) {
    return { version: 1, tools: {} };
  }
}

function printUsage(registry) {
  console.log(`
Agent Tool Runner (.agents/scripts)
===================================
Usage:
  node .agents/scripts/runner.js <tool-name> [args...]
  node .agents/scripts/runner.js --list
  node .agents/scripts/runner.js --help

Available Synthesized Tools:
`);
  const tools = Object.values(registry.tools || {});
  if (tools.length === 0) {
    console.log('  (No tools synthesized yet. Use Toolmaker to create one.)\n');
    return;
  }
  for (const t of tools) {
    console.log(`  • ${t.name.padEnd(20)} ${t.description}`);
  }
  console.log();
}

function main() {
  const args = process.argv.slice(2);
  const registry = loadRegistry();

  if (args.length === 0 || args[0] === '--help' || args[0] === '-h') {
    printUsage(registry);
    process.exit(0);
  }

  if (args[0] === '--list' || args[0] === '-l') {
    console.log('=== Registered Agent Tools ===');
    console.log(JSON.stringify(registry.tools, null, 2));
    process.exit(0);
  }

  const toolName = args[0];
  const tool = registry.tools[toolName];

  if (!tool) {
    console.error(`❌ Tool "${toolName}" not found in registry.`);
    console.error(`Available tools: ${Object.keys(registry.tools).join(', ') || 'none'}`);
    process.exit(1);
  }

  const rootDir = path.join(__dirname, '../../');
  const fullScriptPath = path.isAbsolute(tool.scriptPath)
    ? tool.scriptPath
    : path.join(rootDir, tool.scriptPath);

  if (!fs.existsSync(fullScriptPath)) {
    console.error(`❌ Script file not found at: ${fullScriptPath}`);
    process.exit(1);
  }

  const toolArgs = args.slice(1);
  const child = spawnSync(process.execPath, [fullScriptPath, ...toolArgs], {
    stdio: 'inherit',
    cwd: process.cwd()
  });

  if (child.error) {
    console.error(`❌ Execution error: ${child.error.message}`);
    process.exit(1);
  }

  const exitCode = child.status !== null ? child.status : (child.signal ? 1 : 0);
  process.exit(exitCode);
}

main();
