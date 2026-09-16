const fs = require('fs');
const path = require('path');

const DEFAULT_REGISTRY_PATH = path.join(__dirname, '../../.agents/scripts/registry.json');

class ToolRegistry {
  constructor(registryPath = DEFAULT_REGISTRY_PATH) {
    this.registryPath = registryPath;
    this.scriptsDir = path.dirname(registryPath);
    this.ensureDir();
  }

  ensureDir() {
    if (!fs.existsSync(this.scriptsDir)) {
      fs.mkdirSync(this.scriptsDir, { recursive: true });
    }
  }

  load() {
    this.ensureDir();
    if (!fs.existsSync(this.registryPath)) {
      const initial = { version: 1, tools: {} };
      fs.writeFileSync(this.registryPath, JSON.stringify(initial, null, 2) + '\n');
      return initial;
    }
    try {
      const content = fs.readFileSync(this.registryPath, 'utf8');
      return JSON.parse(content);
    } catch (e) {
      return { version: 1, tools: {} };
    }
  }

  save(data) {
    this.ensureDir();
    fs.writeFileSync(this.registryPath, JSON.stringify(data, null, 2) + '\n');
  }

  registerTool({ name, description, scriptPath, args = [], examples = [] }) {
    const data = this.load();
    const relScriptPath = path.isAbsolute(scriptPath)
      ? path.relative(path.join(__dirname, '../../'), scriptPath)
      : scriptPath;

    data.tools[name] = {
      name,
      description,
      scriptPath: relScriptPath,
      args,
      examples,
      registeredAt: new Date().toISOString()
    };
    this.save(data);
    return data.tools[name];
  }

  getTool(name) {
    const data = this.load();
    return data.tools[name] || null;
  }

  listTools() {
    const data = this.load();
    return Object.values(data.tools);
  }

  removeTool(name) {
    const data = this.load();
    if (data.tools[name]) {
      delete data.tools[name];
      this.save(data);
      return true;
    }
    return false;
  }
}

module.exports = {
  ToolRegistry,
  DEFAULT_REGISTRY_PATH
};
