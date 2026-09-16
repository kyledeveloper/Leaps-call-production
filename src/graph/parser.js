/**
 * Acorn-based AST parser for JavaScript (depends on acorn + acorn-walk).
 *
 * Extracts imports, exports, symbol declarations, and call sites.
 */

const fs = require('fs');
const path = require('path');
const acorn = require('acorn');
const walk = require('acorn-walk');

const BUILTIN_GLOBALS = new Set([
  'JSON', 'Math', 'Object', 'Array', 'String', 'Number', 'Boolean', 'Date',
  'RegExp', 'Error', 'Promise', 'Map', 'Set', 'WeakMap', 'WeakSet', 'Symbol',
  'BigInt', 'Reflect', 'Proxy', 'Intl', 'Atomics',
  'console', 'process', 'Buffer', 'global', 'globalThis', 'window', 'document',
  'performance', 'assert',
  'Uint8Array', 'Int8Array', 'Uint16Array', 'Int16Array', 'Uint32Array', 'Int32Array',
  'Float32Array', 'Float64Array', 'BigInt64Array', 'BigUint64Array', 'ArrayBuffer', 'SharedArrayBuffer', 'DataView',
  'fs', 'path', 'os', 'child_process', 'crypto', 'http', 'https', 'url', 'util'
]);

/**
 * Recursively collect identifier names from a pattern.
 */
function collectPatternNames(pattern, names = []) {
  if (!pattern) return names;
  if (pattern.type === 'Identifier') {
    names.push(pattern.name);
    return names;
  }
  if (pattern.type === 'AssignmentPattern') {
    return collectPatternNames(pattern.left, names);
  }
  if (pattern.type === 'RestElement') {
    return collectPatternNames(pattern.argument, names);
  }
  if (pattern.type === 'ArrayPattern') {
    pattern.elements.forEach(elem => elem && collectPatternNames(elem, names));
    return names;
  }
  if (pattern.type === 'ObjectPattern') {
    pattern.properties.forEach(prop => {
      const target = prop.type === 'Property' ? prop.value : prop.argument;
      if (target) collectPatternNames(target, names);
    });
    return names;
  }
  return names;
}

/**
 * CJS ObjectPattern require bindings: imported keys and local names.
 * `{ login: signIn }` → ['login', 'signIn']
 */
function collectCjsNamedImports(pattern, names = []) {
  if (!pattern) return names;
  if (pattern.type === 'ObjectPattern') {
    pattern.properties.forEach(prop => {
      if (prop.type === 'Property') {
        if (prop.key && prop.key.type === 'Identifier' && !prop.computed) {
          names.push(prop.key.name);
        }
        collectPatternNames(prop.value, names);
      } else if (prop.type === 'RestElement') {
        collectPatternNames(prop.argument, names);
      }
    });
  }
  return Array.from(new Set(names));
}

/**
 * Resolve relative or local module import path to an absolute file path.
 * @param {string} importPath - Path string from require() or import
 * @param {string} currentFilePath - File making the import
 * @returns {string|null} - Absolute path if local, or null if built-in/package
 */
function resolveModulePath(importPath, currentFilePath) {
  if (!importPath || typeof importPath !== 'string') return null;
  // Ignore built-in modules or node_modules packages
  if (!importPath.startsWith('.') && !importPath.startsWith('/')) {
    return null;
  }

  const baseDir = path.dirname(currentFilePath);
  const target = path.resolve(baseDir, importPath);

  // Exact file match
  if (fs.existsSync(target) && fs.statSync(target).isFile()) {
    return target;
  }

  // Check extensions
  const extensions = ['.js', '.json', '.mjs', '.cjs', '.ts', '.jsx', '.tsx'];
  for (const ext of extensions) {
    const withExt = target + ext;
    if (fs.existsSync(withExt) && fs.statSync(withExt).isFile()) {
      return withExt;
    }
  }

  // Check index file in directory
  for (const ext of extensions) {
    const indexPath = path.join(target, 'index' + ext);
    if (fs.existsSync(indexPath) && fs.statSync(indexPath).isFile()) {
      return indexPath;
    }
  }

  return target;
}

/**
 * Skip quoted string literal content in stripComments.
 */
function skipStringLiteral(code, startIdx, quote) {
  let out = quote;
  let i = startIdx + 1;
  const n = code.length;
  while (i < n) {
    const ch = code[i];
    out += ch;
    if (ch === '\\' && i + 1 < n) {
      out += code[i + 1];
      i += 2;
      continue;
    }
    if (ch === quote) {
      i++;
      break;
    }
    i++;
  }
  return { text: out, nextIdx: i };
}

/**
 * Strip line and block comments without treating // inside strings as comments.
 * Preserves "https://..." and similar URL literals that a naive //.* regex would truncate.
 */
function stripComments(code) {
  let out = '';
  let i = 0;
  const n = code.length;

  while (i < n) {
    const c = code[i];
    const next = i + 1 < n ? code[i + 1] : '';

    if (c === '"' || c === "'" || c === '`') {
      const res = skipStringLiteral(code, i, c);
      out += res.text;
      i = res.nextIdx;
      continue;
    }

    if (c === '/' && next === '/') {
      i += 2;
      while (i < n && code[i] !== '\n') i++;
      continue;
    }

    if (c === '/' && next === '*') {
      i += 2;
      while (i + 1 < n && !(code[i] === '*' && code[i + 1] === '/')) {
        if (code[i] === '\n') out += '\n';
        i++;
      }
      i = Math.min(n, i + 2);
      continue;
    }

    out += c;
    i++;
  }

  return out;
}

/**
 * Get 1-based line number for a character index in source code.
 */
function getLineNumber(code, index) {
  let line = 1;
  const len = Math.min(index, code.length);
  for (let i = 0; i < len; i++) {
    if (code[i] === '\n') line++;
  }
  return line;
}

/**
 * Find matching closing brace index for an opening brace starting at or after startIndex.
 */
function findMatchingBrace(code, startIndex) {
  const openIdx = code.indexOf('{', startIndex);
  if (openIdx === -1) return null;
  let depth = 1;
  for (let i = openIdx + 1; i < code.length; i++) {
    if (code[i] === '{') depth++;
    else if (code[i] === '}') {
      depth--;
      if (depth === 0) return i;
    }
  }
  return null;
}

/**
 * Fallback regex-based lexical extractor for non-standard / TypeScript syntax.
 */
function parseWithRegexFallback(code, filePath) {
  if (!code || typeof code !== 'string') {
    return {
      filePath,
      imports: [],
      exports: [],
      functions: [],
      classes: [],
      callSites: []
    };
  }

  // Strip block and line comments to avoid token corruption
  const cleanCode = stripComments(code);

  const imports = [];
  const exportsList = new Set();
  const functions = [];
  const classes = [];
  const callSites = new Set();

  // 1. Extract require() imports: const { a, b } = require('...') OR const a = require('...')
  const cjsRequireRegex = /(?:const|let|var)\s+(?:\{([^}]+)\}|([a-zA-Z0-9_$]+))\s*=\s*require\((["'][^"']+["'])\)/g;
  let match;
  while ((match = cjsRequireRegex.exec(cleanCode)) !== null) {
    const rawNamed = match[1];
    const defaultName = match[2] || null;
    const source = match[3].replace(/["']/g, '');
    const named = rawNamed ? rawNamed.split(',').map(s => s.trim().split(/\s+as\s+/)[0]).filter(Boolean) : null;
    const resolvedPath = resolveModulePath(source, filePath);

    imports.push({
      type: 'cjs',
      source,
      resolvedPath,
      defaultName,
      named
    });
  }

  // 2. Extract ESM imports: import { a, b } from '...' OR import a from '...'
  const esmImportRegex = /import\s+(?:\{([^}]+)\}|([a-zA-Z0-9_$]+))\s+from\s+(["'][^"']+["'])/g;
  while ((match = esmImportRegex.exec(cleanCode)) !== null) {
    const rawNamed = match[1];
    const defaultName = match[2] || null;
    const source = match[3].replace(/["']/g, '');
    const named = rawNamed ? rawNamed.split(',').map(s => s.trim().split(/\s+as\s+/)[0]).filter(Boolean) : null;
    const resolvedPath = resolveModulePath(source, filePath);

    imports.push({
      type: 'esm',
      source,
      resolvedPath,
      defaultName,
      named
    });
  }

  // import * as ns from '...'
  const esmNamespaceImport = /import\s+\*\s+as\s+([a-zA-Z0-9_$]+)\s+from\s+(["'][^"']+["'])/g;
  while ((match = esmNamespaceImport.exec(cleanCode)) !== null) {
    const source = match[2].replace(/["']/g, '');
    imports.push({
      type: 'esm',
      source,
      resolvedPath: resolveModulePath(source, filePath),
      defaultName: match[1],
      named: null
    });
  }

  // 3. Extract Classes and base class extensions
  const classRegex = /class\s+([a-zA-Z0-9_$]+)(?:\s+extends\s+([a-zA-Z0-9_$]+))?\s*\{/g;
  while ((match = classRegex.exec(cleanCode)) !== null) {
    const startLine = getLineNumber(cleanCode, match.index);
    const endIdx = findMatchingBrace(cleanCode, match.index + match[0].indexOf('{'));
    const endLine = endIdx !== null ? getLineNumber(cleanCode, endIdx) : startLine;

    classes.push({
      name: match[1],
      extends: match[2] || null,
      startLine,
      endLine
    });
    if (match[2]) {
      callSites.add(match[2]);
    }
  }

  // 4. Extract Functions
  // Standard functions: function foo(...)
  const stdFuncRegex = /(?:async\s+)?function\s+([a-zA-Z0-9_$]+)\s*\(([^)]*)\)/g;
  while ((match = stdFuncRegex.exec(cleanCode)) !== null) {
    const startLine = getLineNumber(cleanCode, match.index);
    const endIdx = findMatchingBrace(cleanCode, match.index + match[0].length);
    const endLine = endIdx !== null ? getLineNumber(cleanCode, endIdx) : startLine;

    functions.push({
      name: match[1],
      params: match[2].split(',').map(p => p.trim()).filter(Boolean),
      startLine,
      endLine
    });
  }

  // Arrow & variable functions: const foo = (params) => OR const foo = async function(...)
  const varFuncRegex = /(?:const|let|var)\s+([a-zA-Z0-9_$]+)\s*=\s*(?:async\s*)?(?:\(([^)]*)\)|[a-zA-Z0-9_$]+)\s*=>/g;
  while ((match = varFuncRegex.exec(cleanCode)) !== null) {
    const startLine = getLineNumber(cleanCode, match.index);
    const rest = cleanCode.slice(match.index + match[0].length);
    const firstNonWs = rest.search(/\S/);
    let endLine = startLine;
    if (firstNonWs !== -1 && rest[firstNonWs] === '{') {
      const endIdx = findMatchingBrace(cleanCode, match.index + match[0].length + firstNonWs);
      endLine = endIdx !== null ? getLineNumber(cleanCode, endIdx) : startLine;
    } else {
      const nextSemi = rest.indexOf(';');
      const nextNl = rest.indexOf('\n');
      const offset = nextSemi !== -1 ? nextSemi : (nextNl !== -1 ? nextNl : 0);
      endLine = getLineNumber(cleanCode, match.index + match[0].length + offset);
    }

    functions.push({
      name: match[1],
      params: (match[2] || '').split(',').map(p => p.trim()).filter(Boolean),
      startLine,
      endLine
    });
  }

  // 5. Extract Exports
  // module.exports = { a, b, c }
  const cjsMultiExport = /module\.exports\s*=\s*\{([^}]+)\}/;
  const multiMatch = cleanCode.match(cjsMultiExport);
  if (multiMatch) {
    const items = multiMatch[1].split(',').map(s => s.trim());
    items.forEach(item => {
      // Handles 'a' or 'key: a'
      const name = item.includes(':') ? item.split(':')[0].trim() : item;
      if (name && /^[a-zA-Z0-9_$]+$/.test(name)) {
        exportsList.add(name);
      }
    });
  }

  // module.exports = Name (direct export)
  const cjsSingleExport = /module\.exports\s*=\s*([a-zA-Z0-9_$]+)\s*(?:;|$)/g;
  while ((match = cjsSingleExport.exec(cleanCode)) !== null) {
    if (match[1] !== '{') exportsList.add(match[1]);
  }

  // exports.name = ...
  const cjsNamedExport = /exports\.([a-zA-Z0-9_$]+)\s*=/g;
  while ((match = cjsNamedExport.exec(cleanCode)) !== null) {
    exportsList.add(match[1]);
  }

  // ESM exports: export const a = ... OR export function a() ...
  const esmNamedExports = /export\s+(?:const|let|var|function|class)\s+([a-zA-Z0-9_$]+)/g;
  while ((match = esmNamedExports.exec(cleanCode)) !== null) {
    exportsList.add(match[1]);
  }

  // ESM named list: export { a, b as c }
  const esmListExport = /export\s+\{([^}]+)\}/g;
  while ((match = esmListExport.exec(cleanCode)) !== null) {
    match[1].split(',').forEach(item => {
      const trimmed = item.trim();
      if (!trimmed) return;
      const parts = trimmed.split(/\s+as\s+/);
      const publicName = (parts[1] || parts[0]).trim();
      if (publicName && /^[a-zA-Z0-9_$]+$/.test(publicName)) {
        exportsList.add(publicName);
      }
    });
  }

  // 6. Extract Call Sites (e.g. foo(...) or obj.method(...))
  const callRegex = /(?:([a-zA-Z0-9_$]+)\.)?([a-zA-Z0-9_$]{2,})\s*\(/g;
  while ((match = callRegex.exec(cleanCode)) !== null) {
    const objectName = match[1] || null;
    const methodName = match[2];
    const keywords = ['if', 'for', 'while', 'switch', 'catch', 'require', 'import', 'return', 'function'];
    if (!keywords.includes(methodName)) {
      if (objectName) callSites.add(`${objectName}.${methodName}`);
      callSites.add(methodName);
    }
  }

  return {
    filePath,
    imports,
    exports: Array.from(exportsList),
    functions,
    classes,
    callSites: Array.from(callSites)
  };
}

/**
 * Extract imports from Acorn AST node.
 */
/**
 * Parse import specifiers for ESM imports.
 */
function parseImportSpecifiers(specifiers) {
  let defaultName = null;
  const named = [];
  for (const spec of specifiers) {
    if (spec.type === 'ImportDefaultSpecifier' || spec.type === 'ImportNamespaceSpecifier') {
      defaultName = spec.local.name;
      continue;
    }
    if (spec.type === 'ImportSpecifier') {
      named.push(spec.local.name);
      if (spec.imported && spec.imported.name && spec.imported.name !== spec.local.name) {
        named.push(spec.imported.name);
      }
    }
  }
  return { defaultName, named };
}

/**
 * Extract ESM ImportDeclaration.
 */
function extractEsmImports(node, filePath, imports) {
  const source = node.source.value;
  const resolvedPath = resolveModulePath(source, filePath);
  const { defaultName, named } = parseImportSpecifiers(node.specifiers || []);
  imports.push({
    type: 'esm',
    source,
    resolvedPath,
    defaultName,
    named: named.length > 0 ? named : null
  });
}

/**
 * Extract CJS require call from variable declaration.
 */
function extractCjsRequire(decl, filePath, imports) {
  if (!decl.init || decl.init.type !== 'CallExpression' || !decl.init.callee || decl.init.callee.name !== 'require') {
    return;
  }
  const arg = decl.init.arguments[0];
  if (!arg || arg.type !== 'Literal' || typeof arg.value !== 'string') {
    return;
  }
  const source = arg.value;
  const resolvedPath = resolveModulePath(source, filePath);

  if (decl.id.type === 'Identifier') {
    imports.push({ type: 'cjs', source, resolvedPath, defaultName: decl.id.name, named: null });
    return;
  }
  if (decl.id.type === 'ObjectPattern') {
    const names = collectCjsNamedImports(decl.id);
    imports.push({ type: 'cjs', source, resolvedPath, defaultName: null, named: names });
  }
}

/**
 * Extract imports from Acorn AST node.
 */
function extractAcornImports(node, filePath, imports) {
  if (node.type === 'ImportDeclaration') {
    extractEsmImports(node, filePath, imports);
    return;
  }
  if (node.type === 'VariableDeclaration') {
    for (const decl of node.declarations) {
      extractCjsRequire(decl, filePath, imports);
    }
  }
}

/**
 * Extract named exports from Acorn ExportNamedDeclaration.
 */
function extractNamedExport(node, exportsList) {
  const decl = node.declaration;
  if (decl && decl.id && decl.id.name) {
    exportsList.add(decl.id.name);
  } else if (decl && decl.declarations) {
    decl.declarations.forEach(d => {
      collectPatternNames(d.id).forEach(n => exportsList.add(n));
    });
  }

  for (const spec of (node.specifiers || [])) {
    const publicName = spec.exported ? (spec.exported.name || spec.exported.value) : null;
    if (publicName) exportsList.add(String(publicName));
  }
}

/**
 * Extract exports from module.exports assignment.
 */
function extractModuleExports(right, exportsList) {
  if (right.type === 'Identifier') {
    exportsList.add(right.name);
    return;
  }
  if (right.type === 'ObjectExpression') {
    for (const prop of right.properties) {
      const name = prop.key ? (prop.key.name || prop.key.value) : null;
      if (name) exportsList.add(String(name));
    }
  }
}

/**
 * Extract exports from assignment expression.
 */
/**
 * Extract exports from assignment expression.
 */
function extractAssignmentExport(node, exportsList) {
  const left = node.left;
  if (!left || left.type !== 'MemberExpression') return;

  if (left.object && left.object.name === 'module' && left.property && left.property.name === 'exports') {
    extractModuleExports(node.right, exportsList);
    return;
  }
  if (left.object && left.object.name === 'exports') {
    const name = left.property ? (left.property.name || left.property.value) : null;
    if (name) exportsList.add(String(name));
    return;
  }
  if (
    left.object &&
    left.object.type === 'MemberExpression' &&
    left.object.object &&
    left.object.object.name === 'module' &&
    left.object.property &&
    left.object.property.name === 'exports'
  ) {
    const name = left.property ? (left.property.name || left.property.value) : null;
    if (name) exportsList.add(String(name));
  }
}

/**
 * Extract exports from Object.assign(module.exports, ...) or Object.assign(exports, ...).
 */
function extractObjectAssignExports(node, exportsList) {
  if (!node || node.type !== 'CallExpression') return;
  const callee = node.callee;
  if (!callee || callee.type !== 'MemberExpression') return;
  if (!callee.object || callee.object.name !== 'Object') return;
  if (!callee.property || callee.property.name !== 'assign') return;

  const firstArg = node.arguments[0];
  if (!firstArg) return;
  const isModExp =
    (firstArg.type === 'MemberExpression' && firstArg.object && firstArg.object.name === 'module' && firstArg.property && firstArg.property.name === 'exports') ||
    (firstArg.type === 'Identifier' && firstArg.name === 'exports');
  if (!isModExp) return;

  for (let i = 1; i < node.arguments.length; i++) {
    const arg = node.arguments[i];
    if (arg && arg.type === 'ObjectExpression' && arg.properties) {
      for (const prop of arg.properties) {
        const name = prop.key ? (prop.key.name || prop.key.value) : null;
        if (name) exportsList.add(String(name));
      }
    }
  }
}

/**
 * Extract exports from Acorn AST node.
 */
function extractAcornExports(node, exportsList) {
  if (node.type === 'ExportNamedDeclaration') {
    extractNamedExport(node, exportsList);
    return;
  }
  if (node.type === 'ExportDefaultDeclaration') {
    const name = (node.declaration && node.declaration.id) ? node.declaration.id.name : 'default';
    exportsList.add(name);
    return;
  }
  if (node.type === 'AssignmentExpression') {
    extractAssignmentExport(node, exportsList);
  }
}

/**
 * Extract call sites within a function body node.
 */
function extractFunctionCalls(funcBody) {
  const calls = new Set();
  if (!funcBody) return [];
  try {
    walk.simple(funcBody, {
      CallExpression(callNode) {
        if (callNode.callee && callNode.callee.type === 'Identifier') {
          calls.add(callNode.callee.name);
        } else if (callNode.callee && callNode.callee.type === 'MemberExpression') {
          const prop = callNode.callee.property ? (callNode.callee.property.name || callNode.callee.property.value) : null;
          if (prop) calls.add(String(prop));
        }
      }
    });
  } catch (e) {
    // Ignore AST walk errors on partial bodies
  }
  return Array.from(calls);
}

/**
 * Extract functions and classes from Acorn AST node.
 */
function extractAcornDeclarations(node, functions, classes, callSites) {
  if (node.type === 'FunctionDeclaration' && node.id) {
    const params = node.params.map(p => (p.type === 'Identifier' ? p.name : '')).filter(Boolean);
    const calls = extractFunctionCalls(node.body);
    functions.push({ name: node.id.name, params, startLine: node.loc.start.line, endLine: node.loc.end.line, calls });
    return;
  }
  if (node.type === 'ClassDeclaration' && node.id) {
    const ext = node.superClass && node.superClass.type === 'Identifier' ? node.superClass.name : null;
    classes.push({ name: node.id.name, extends: ext, startLine: node.loc.start.line, endLine: node.loc.end.line });
    if (ext) callSites.add(ext);
    return;
  }
  if (node.type === 'VariableDeclaration') {
    for (const decl of node.declarations) {
      if (decl.id.type === 'Identifier' && decl.init && (decl.init.type === 'ArrowFunctionExpression' || decl.init.type === 'FunctionExpression')) {
        const params = decl.init.params.map(p => (p.type === 'Identifier' ? p.name : '')).filter(Boolean);
        const calls = extractFunctionCalls(decl.init.body);
        functions.push({ name: decl.id.name, params, startLine: node.loc.start.line, endLine: node.loc.end.line, calls });
      }
    }
  }
}

/**
 * Extract call sites, filtering out global built-in methods (e.g. JSON.parse, Math.max).
 */
function extractAcornCallSites(node, callSites) {
  if (node.type !== 'CallExpression') return;
  const callee = node.callee;
  if (callee.type === 'Identifier') {
    if (!['require', 'import', 'if', 'for', 'while', 'switch'].includes(callee.name)) {
      callSites.add(callee.name);
    }
  } else if (callee.type === 'MemberExpression') {
    let root = callee.object;
    while (root && root.object) root = root.object;
    const isGlobal = root && root.name && BUILTIN_GLOBALS.has(root.name);
    if (!isGlobal) {
      const propName = callee.property ? (callee.property.name || callee.property.value) : null;
      if (callee.object.type === 'Identifier' && propName) {
        callSites.add(`${callee.object.name}.${propName}`);
      }
      if (propName) callSites.add(String(propName));
    }
  }
}

/**
 * High-precision pure JS AST parser via Acorn.
 */
function parseWithAcorn(code, filePath) {
  const ast = acorn.parse(code, {
    ecmaVersion: 'latest',
    sourceType: 'module',
    locations: true,
    allowReturnOutsideFunction: true,
    allowImportExportEverywhere: true,
    allowAwaitOutsideFunction: true
  });

  const imports = [];
  const exportsList = new Set();
  const functions = [];
  const classes = [];
  const callSites = new Set();

  walk.simple(ast, {
    ImportDeclaration(node) { extractAcornImports(node, filePath, imports); },
    VariableDeclaration(node) {
      extractAcornImports(node, filePath, imports);
      extractAcornDeclarations(node, functions, classes, callSites);
    },
    FunctionDeclaration(node) { extractAcornDeclarations(node, functions, classes, callSites); },
    ClassDeclaration(node) { extractAcornDeclarations(node, functions, classes, callSites); },
    ExportNamedDeclaration(node) {
      extractAcornExports(node, exportsList);
      if (node.source && typeof node.source.value === 'string') {
        const source = node.source.value;
        const resolvedPath = resolveModulePath(source, filePath);
        imports.push({ type: 'esm', source, resolvedPath, defaultName: null, named: null });
      }
    },
    ExportAllDeclaration(node) {
      if (node.source && typeof node.source.value === 'string') {
        const source = node.source.value;
        const resolvedPath = resolveModulePath(source, filePath);
        imports.push({ type: 'esm', source, resolvedPath, defaultName: null, named: ['*'] });
      }
    },
    ExportDefaultDeclaration(node) { extractAcornExports(node, exportsList); },
    AssignmentExpression(node) { extractAcornExports(node, exportsList); },
    CallExpression(node) {
      extractAcornCallSites(node, callSites);
      extractObjectAssignExports(node, exportsList);
    }
  });

  return {
    filePath,
    imports,
    exports: Array.from(exportsList),
    functions,
    classes,
    callSites: Array.from(callSites)
  };
}

/**
 * Parse source code string and extract symbols, dependencies, and exports.
 * Attempts pure JS Acorn AST parsing first; falls back smoothly to regex parser on syntax errors.
 */
function parseSource(code, filePath) {
  if (!code || typeof code !== 'string') {
    return {
      filePath,
      imports: [],
      exports: [],
      functions: [],
      classes: [],
      callSites: [],
      isDegraded: false,
      parserType: 'empty'
    };
  }

  try {
    const res = parseWithAcorn(code, filePath);
    res.isDegraded = false;
    res.parserType = 'ast-acorn';
    return res;
  } catch (err) {
    const res = parseWithRegexFallback(code, filePath);
    res.isDegraded = true;
    res.parserType = 'regex-fallback';
    return res;
  }
}

/**
 * Find parsed nodes (functions, classes) overlapping with specified line numbers.
 * @param {object} parsedSource - Output from parseSource
 * @param {number[]} lineNumbers - Array of 1-based modified line numbers
 * @returns {{ matchedNodes: Array<object>, coveredLines: number[], touchesTopLevel: boolean, touchesExports: boolean }}
 */
function findNodesAtLines(parsedSource, lineNumbers) {
  if (!parsedSource || !Array.isArray(lineNumbers) || lineNumbers.length === 0) {
    return { matchedNodes: [], coveredLines: [], touchesTopLevel: false, touchesExports: false };
  }

  const lineSet = new Set(lineNumbers);
  const matchedNodes = [];
  const coveredLines = new Set();

  const allNodes = [
    ...(parsedSource.functions || []).map(f => ({ ...f, type: 'function' })),
    ...(parsedSource.classes || []).map(c => ({ ...c, type: 'class' }))
  ];

  for (const node of allNodes) {
    if (typeof node.startLine !== 'number' || typeof node.endLine !== 'number') continue;
    let nodeMatched = false;
    for (let l = node.startLine; l <= node.endLine; l++) {
      if (lineSet.has(l)) {
        nodeMatched = true;
        coveredLines.add(l);
      }
    }
    if (nodeMatched) {
      matchedNodes.push(node);
    }
  }

  const exportNames = new Set(parsedSource.exports || []);
  const touchesExports = matchedNodes.some(n => exportNames.has(n.name));
  let touchesTopLevel = false;

  for (const l of lineSet) {
    if (!coveredLines.has(l)) {
      touchesTopLevel = true;
      break;
    }
  }

  return {
    matchedNodes,
    coveredLines: Array.from(coveredLines),
    touchesTopLevel,
    touchesExports
  };
}

module.exports = {
  resolveModulePath,
  parseSource,
  stripComments,
  findNodesAtLines
};
