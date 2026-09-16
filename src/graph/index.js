/**
 * SymbolGraph: Project-wide Code Dependency & Symbol Graph
 */

const fs = require('fs');
const path = require('path');
const { parseSource, resolveModulePath } = require('./parser');

class SymbolGraph {
  constructor(options = {}) {
    this.rootDir = path.resolve(options.rootDir || process.cwd());
    this.fileNodes = new Map(); // filePath -> parsed metadata
    this.dependencies = new Map(); // fromFile -> Set(toFile)
    this.dependents = new Map(); // toFile -> Set(fromFile)
    this.symbolDeclarations = new Map(); // symbolName -> [{ filePath, type }]
  }

  /**
   * Scan directory recursively for JavaScript/TypeScript files.
   * @param {string} dir
   * @returns {string[]}
   */
  crawlFiles(dir) {
    let results = [];
    if (!fs.existsSync(dir)) return results;

    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      const fullPath = path.join(dir, entry.name);
      const normalized = fullPath.replace(/\\/g, '/');

      if (entry.isDirectory()) {
        // Exclude unwanted directories
        if (
          entry.name === 'node_modules' ||
          entry.name === '.git' ||
          entry.name === 'plugins' ||
          normalized.includes('/.agents/plugins')
        ) {
          continue;
        }
        results = results.concat(this.crawlFiles(fullPath));
      } else if (entry.isFile()) {
        if (/\.(js|ts|jsx|tsx|cjs|mjs)$/i.test(entry.name)) {
          results.push(fullPath);
        }
      }
    }
    return results;
  }

  /**
   * Build the complete project graph by scanning and analyzing files.
   */
  build() {
    const candidateDirs = [
      path.join(this.rootDir, 'src'),
      path.join(this.rootDir, 'test'),
      path.join(this.rootDir, '.agents/scripts'),
      path.join(this.rootDir, 'examples')
    ].filter(d => fs.existsSync(d));

    const scanDirs = candidateDirs.length > 0 ? candidateDirs : [this.rootDir];

    let allFiles = [];
    for (const d of scanDirs) {
      allFiles = allFiles.concat(this.crawlFiles(d));
    }
    // Also include root entry files if present
    const rootIndex = path.join(this.rootDir, 'index.js');
    if (fs.existsSync(rootIndex) && !allFiles.includes(rootIndex)) {
      allFiles.push(rootIndex);
    }

    // Step 1: Parse all files
    for (const filePath of allFiles) {
      try {
        const code = fs.readFileSync(filePath, 'utf8');
        const parsed = parseSource(code, filePath);
        this.fileNodes.set(filePath, parsed);

        // Index exported symbols and declared classes/functions
        const allLocalSymbols = [
          ...parsed.exports.map(e => ({ name: e, type: 'export' })),
          ...parsed.classes.map(c => ({ name: c.name, type: 'class' })),
          ...parsed.functions.map(f => ({ name: f.name, type: 'function' }))
        ];

        for (const s of allLocalSymbols) {
          if (!this.symbolDeclarations.has(s.name)) {
            this.symbolDeclarations.set(s.name, []);
          }
          this.symbolDeclarations.get(s.name).push({
            filePath,
            type: s.type
          });
        }
      } catch (err) {
        // Skip unparseable files
      }
    }

    // Step 2: Build dependency and reverse-dependent edges
    for (const [filePath, node] of this.fileNodes.entries()) {
      if (!this.dependencies.has(filePath)) {
        this.dependencies.set(filePath, new Set());
      }

      for (const imp of node.imports) {
        let resolved = imp.resolvedPath;
        if (!resolved && imp.source.startsWith('.')) {
          resolved = resolveModulePath(imp.source, filePath);
        }

        if (resolved && fs.existsSync(resolved)) {
          this.dependencies.get(filePath).add(resolved);

          if (!this.dependents.has(resolved)) {
            this.dependents.set(resolved, new Set());
          }
          this.dependents.get(resolved).add(filePath);
        }
      }
    }
  }

  /**
   * Get metadata for a file.
   * @param {string} filePath
   * @returns {object|null}
   */
  getFileNode(filePath) {
    const resolved = path.resolve(this.rootDir, filePath);
    return this.fileNodes.get(resolved) || null;
  }

  /**
   * Get files that directly import the given file.
   * @param {string} filePath
   * @returns {string[]}
   */
  getDownstreamFiles(filePath) {
    const resolved = path.resolve(this.rootDir, filePath);
    const deps = this.dependents.get(resolved);
    return deps ? Array.from(deps) : [];
  }

  /**
   * Get all transitive downstream files using BFS traversal.
   * @param {string} filePath
   * @param {number} maxDepth
   * @returns {Array<{ file: string, depth: number, path: string[] }>}
   */
  getTransitiveDownstream(filePath, maxDepth = 10) {
    const startPath = path.resolve(this.rootDir, filePath);
    const visited = new Set([startPath]);
    const queue = [{ file: startPath, depth: 0, chain: [startPath] }];
    const results = [];

    while (queue.length > 0) {
      const current = queue.shift();
      if (current.depth >= maxDepth) continue;

      const directDependents = this.dependents.get(current.file);
      if (directDependents) {
        for (const dep of directDependents) {
          if (!visited.has(dep)) {
            visited.add(dep);
            const nextItem = {
              file: dep,
              depth: current.depth + 1,
              chain: [...current.chain, dep]
            };
            results.push(nextItem);
            queue.push(nextItem);
          }
        }
      }
    }

    return results;
  }

  /**
   * Find where a symbol is declared.
   * @param {string} symbolName
   * @returns {Array<{ filePath: string, type: string }>}
   */
  findSymbol(symbolName) {
    return this.symbolDeclarations.get(symbolName) || [];
  }

  /**
   * Find all files that invoke or reference a symbol name.
   * @param {string} symbolName
   * @returns {string[]}
   */
  findSymbolCallers(symbolName) {
    const decls = this.findSymbol(symbolName);
    const declFiles = new Set(decls.map(d => path.resolve(d.filePath)));
    const callers = [];

    for (const [filePath, node] of this.fileNodes.entries()) {
      const importsSymbolFromDecl = (node.imports || []).some(imp => {
        const namedHit = Array.isArray(imp.named) && imp.named.includes(symbolName);
        const defaultHit = imp.defaultName === symbolName;
        if (!namedHit && !defaultHit) return false;
        if (!imp.resolvedPath || declFiles.size === 0) return namedHit || defaultHit;
        return declFiles.has(path.resolve(imp.resolvedPath));
      });

      const qualifiedFromDecl = (node.callSites || []).some(cs => {
        if (!cs.endsWith('.' + symbolName)) return false;
        const ns = cs.slice(0, cs.length - symbolName.length - 1);
        return (node.imports || []).some(imp => {
          if (imp.defaultName !== ns) return false;
          if (!imp.resolvedPath || declFiles.size === 0) return true;
          return declFiles.has(path.resolve(imp.resolvedPath));
        });
      });

      if (importsSymbolFromDecl || qualifiedFromDecl) {
        callers.push(filePath);
      }
    }
    return callers;
  }
}

module.exports = { SymbolGraph };
