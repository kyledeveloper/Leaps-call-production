const { PatternTracker } = require('./tracker');
const { ToolRegistry } = require('./registry');
const { ToolSynthesizer } = require('./synthesizer');

class ToolmakerEngine {
  constructor(options = {}) {
    this.registry = new ToolRegistry(options.registryPath);
    this.tracker = new PatternTracker(options.dbOrPath);
    this.synthesizer = new ToolSynthesizer(this.registry, this.tracker);
  }

  /**
   * Track a task pattern. Returns if synthesis threshold (>= 3) is reached.
   */
  track(data) {
    return this.tracker.track(data);
  }

  /**
   * Get all recurring candidates eligible for tool synthesis
   */
  getCandidates(minOccurrences = 3) {
    return this.tracker.getCandidates(minOccurrences);
  }

  /**
   * Synthesize a tool from code body and register it into .agents/scripts/
   */
  synthesizeTool(params) {
    return this.synthesizer.synthesize(params);
  }

  /**
   * List all currently registered tools
   */
  listTools() {
    return this.registry.listTools();
  }

  /**
   * Get single tool metadata
   */
  getTool(name) {
    return this.registry.getTool(name);
  }

  close() {
    this.tracker.close();
  }
}

// Global default singleton
let defaultInstance = null;
function getToolmaker(options = {}) {
  if (!defaultInstance || !defaultInstance.tracker?.db?.isOpen) {
    defaultInstance = new ToolmakerEngine(options);
  }
  return defaultInstance;
}

// CLI utility runner
if (require.main === module) {
  const args = process.argv.slice(2);
  const command = args[0] || 'list';
  const toolmaker = getToolmaker();

  if (command === 'list') {
    console.log('=== Synthesized Script Assets (.agents/scripts) ===');
    const tools = toolmaker.listTools();
    if (tools.length === 0) {
      console.log('No tools registered yet.');
    } else {
      for (const t of tools) {
        console.log(`• [${t.name}] - ${t.description} (${t.scriptPath})`);
      }
    }
  } else if (command === 'candidates') {
    console.log('=== Recurring Task Candidates (Occurrences >= 3) ===');
    const candidates = toolmaker.getCandidates(3);
    if (candidates.length === 0) {
      console.log('No recurring candidates have reached the threshold yet.');
    } else {
      for (const c of candidates) {
        console.log(`• [${c.name_slug}] (count: ${c.occurrences}, status: ${c.status})`);
        console.log(`  Intent: ${c.intent_summary}`);
        if (c.command_template) console.log(`  Template: ${c.command_template}`);
      }
    }
  } else {
    console.log('Usage: node src/toolmaker/index.js [list|candidates]');
  }
}

module.exports = {
  ToolmakerEngine,
  getToolmaker
};
