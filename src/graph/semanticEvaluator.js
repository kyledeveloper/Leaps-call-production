/**
 * Semantic Evaluator for Code Symbol Graph & Blast-Radius
 *
 * Stage 2 is local AST contract inference only:
 * - Deterministic structural compatibility analysis
 * - Hermetic call-site AST slicing (<= 20 lines)
 * - Sync-to-async unhandled promise trap detection
 * - Deep recursive object destructuring parameter inspection
 */

const fs = require('fs');
const path = require('path');
const acorn = require('acorn');
const walk = require('acorn-walk');

/**
 * Recursively extract flattened property keys from an ObjectPattern AST node.
 * E.g. { db: { host, port } } -> ['db.host', 'db.port']
 */
function extractDestructuredKeys(patternNode, prefix = '') {
  const keys = [];
  if (!patternNode || patternNode.type !== 'ObjectPattern') return keys;
  for (const prop of patternNode.properties || []) {
    if (!prop.key) continue;
    const keyName = prop.key.name || prop.key.value;
    const fullPath = prefix ? `${prefix}.${keyName}` : keyName;
    const val = prop.value;
    if (val && val.type === 'ObjectPattern') {
      keys.push(...extractDestructuredKeys(val, fullPath));
    } else if (val && val.type === 'AssignmentPattern' && val.left?.type === 'ObjectPattern') {
      keys.push(...extractDestructuredKeys(val.left, fullPath));
    } else {
      keys.push(fullPath);
    }
  }
  return keys;
}

/**
 * Extract parameter contract from an Acorn function parameter AST node.
 * @param {object} paramNode
 * @returns {object} { name, hasDefault, isRest, isDestructured, keys: string[] }
 */
function analyzeSingleParam(paramNode) {
  if (!paramNode) {
    return { name: 'unknown', hasDefault: false, isRest: false, isDestructured: false, keys: [] };
  }

  if (paramNode.type === 'Identifier') {
    return { name: paramNode.name, hasDefault: false, isRest: false, isDestructured: false, keys: [] };
  }

  if (paramNode.type === 'AssignmentPattern') {
    if (paramNode.left.type === 'Identifier') {
      return { name: paramNode.left.name, hasDefault: true, isRest: false, isDestructured: false, keys: [] };
    }
    if (paramNode.left.type === 'ObjectPattern') {
      const keys = extractDestructuredKeys(paramNode.left);
      return { name: 'destructuredObject', hasDefault: true, isRest: false, isDestructured: true, keys };
    }
    return { name: 'defaultParam', hasDefault: true, isRest: false, isDestructured: false, keys: [] };
  }

  if (paramNode.type === 'RestElement') {
    const name = paramNode.argument?.name || 'rest';
    return { name, hasDefault: false, isRest: true, isDestructured: false, keys: [] };
  }

  if (paramNode.type === 'ObjectPattern') {
    const keys = extractDestructuredKeys(paramNode);
    return { name: 'destructuredObject', hasDefault: false, isRest: false, isDestructured: true, keys };
  }

  return { name: 'param', hasDefault: false, isRest: false, isDestructured: false, keys: [] };
}

/**
 * Analyze a function declaration or expression node to extract its formal signature contract.
 * @param {object} funcNode - Acorn FunctionDeclaration / FunctionExpression node
 * @returns {object} Signature contract
 */
function analyzeSignature(funcNode) {
  if (!funcNode) return null;

  const isAsync = Boolean(funcNode.async);
  const isGenerator = Boolean(funcNode.generator);
  const rawParams = funcNode.params || [];
  const params = rawParams.map(analyzeSingleParam);

  const requiredCount = params.filter(p => !p.hasDefault && !p.isRest).length;
  const hasRest = params.some(p => p.isRest);

  return {
    name: funcNode.id?.name || 'anonymous',
    isAsync,
    isGenerator,
    params,
    paramCount: params.length,
    requiredCount,
    hasRest
  };
}

/**
 * Split parameter string by comma, respecting quotes and nested brackets/braces.
 */
function splitParamParts(raw) {
  const parts = [];
  let current = '';
  let depth = 0;
  let inQuote = false;
  let quoteChar = '';
  let isEscaped = false;

  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    if (isEscaped) {
      current += ch;
      isEscaped = false;
      continue;
    }
    if (ch === '\\') {
      isEscaped = true;
      current += ch;
      continue;
    }
    if (inQuote) {
      current += ch;
      if (ch === quoteChar) {
        inQuote = false;
      }
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') {
      inQuote = true;
      quoteChar = ch;
      current += ch;
      continue;
    }
    if (ch === '{' || ch === '(' || ch === '[') {
      depth++;
      current += ch;
    } else if (ch === '}' || ch === ')' || ch === ']') {
      depth = Math.max(0, depth - 1);
      current += ch;
    } else if (ch === ',' && depth === 0) {
      if (current.trim()) parts.push(current.trim());
      current = '';
    } else {
      current += ch;
    }
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

/**
 * Extract argument string inside balanced parentheses starting after openParenIdx.
 */
function extractBalancedParenContent(text, openParenIdx) {
  let depth = 1;
  let inQuote = false;
  let quoteChar = '';
  let isEscaped = false;
  let content = '';

  for (let i = openParenIdx + 1; i < text.length; i++) {
    const ch = text[i];
    if (isEscaped) {
      content += ch;
      isEscaped = false;
      continue;
    }
    if (ch === '\\') {
      isEscaped = true;
      content += ch;
      continue;
    }
    if (inQuote) {
      content += ch;
      if (ch === quoteChar) inQuote = false;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') {
      inQuote = true;
      quoteChar = ch;
      content += ch;
      continue;
    }
    if (ch === '(') {
      depth++;
      content += ch;
    } else if (ch === ')') {
      depth--;
      if (depth === 0) {
        return content;
      }
      content += ch;
    } else {
      content += ch;
    }
  }
  return content;
}

/**
 * Resilient regex-based signature extractor for raw hunk lines or partial diff text.
 * Guarantees zero SyntaxError crashes when receiving partial unified diff hunks.
 * @param {string} text - Raw code or diff line
 * @param {string} symbolName - Target function name
 * @param {'both'|'new'|'old'} [side='both'] - Which diff side to prioritize
 * @returns {object|null}
 */
function extractSignatureFromHunk(text, symbolName, side = 'both') {
  if (!text || typeof text !== 'string' || !symbolName || typeof symbolName !== 'string') return null;

  let candidateText = text;
  if (side === 'new') {
    const lines = text.split('\n').filter(l => l.startsWith('+') && !l.startsWith('+++'));
    if (lines.length > 0) {
      candidateText = lines.map(l => l.slice(1)).join('\n');
    }
  } else if (side === 'old') {
    const lines = text.split('\n').filter(l => l.startsWith('-') && !l.startsWith('---'));
    if (lines.length > 0) {
      candidateText = lines.map(l => l.slice(1)).join('\n');
    }
  }

  const escaped = symbolName.replace(/[$^*()+[\]{}|\\.?]/g, '\\$&');
  const fnRegex = new RegExp(`(?:async\\s+)?function(?:\\s+\\*)?\\s+${escaped}\\s*\\(`, 'm');
  const arrowRegex = new RegExp(`(?:const|let|var|exports\\.|module\\.exports\\.)?\\s*${escaped}\\s*=\\s*(?:async\\s*)?\\(`, 'm');
  const methodRegex = new RegExp(`(?:async\\s+)?${escaped}\\s*\\(`, 'm');

  const match = candidateText.match(fnRegex) || candidateText.match(arrowRegex) || candidateText.match(methodRegex);
  if (!match) return null;

  const matchIdx = match.index;
  const matchStr = match[0];
  const openParenIdx = matchIdx + matchStr.lastIndexOf('(');

  const isAsync = /async\s+function|async\s*\(/.test(matchStr);
  const rawArgs = extractBalancedParenContent(candidateText, openParenIdx).trim();
  if (!rawArgs) {
    return { name: symbolName, isAsync, isGenerator: false, params: [], paramCount: 0, requiredCount: 0, hasRest: false };
  }

  const parts = splitParamParts(rawArgs);
  const params = parts.map(part => {
    const isRest = part.startsWith('...');
    const hasDefault = part.includes('=');
    const cleanName = part.replace(/^\.\.\./, '').split('=')[0].trim();
    const isDestructured = cleanName.startsWith('{') || cleanName.startsWith('[');
    let keys = [];
    if (isDestructured && cleanName.startsWith('{')) {
      const inner = cleanName.replace(/^\{/, '').replace(/\}$/, '');
      keys = splitParamParts(inner).map(k => k.split('=')[0].trim()).filter(Boolean);
    }
    return { name: cleanName, hasDefault, isRest, isDestructured, keys };
  });

  const requiredCount = params.filter(p => !p.hasDefault && !p.isRest).length;
  return {
    name: symbolName,
    isAsync,
    isGenerator: false,
    params,
    paramCount: params.length,
    requiredCount,
    hasRest: params.some(p => p.isRest)
  };
}

/**
 * Slice caller source around a call site AST node (maximum <= 20 lines).
 */
function sliceCallSite(code, node) {
  if (!code || !node || !node.loc) return '';
  const lines = code.split('\n');
  const startLine = Math.max(1, node.loc.start.line - 2);
  const endLine = Math.min(lines.length, node.loc.end.line + 2);

  // Hard limit: never exceed 20 lines
  const sliced = lines.slice(startLine - 1, Math.min(endLine, startLine + 18));
  return sliced.join('\n');
}

/**
 * Resolve local variable bindings for an imported symbol in a file.
 * Distinguishes direct bindings (e.g. const { login } = require('./auth'))
 * from namespace bindings (e.g. const auth = require('./auth'); auth.login()).
 */
function resolveLocalBindings(callerAST, symbolName, targetFile = null) {
  const directBindings = new Set();
  const namespaceBindings = new Set();
  if (!callerAST) return { directBindings, namespaceBindings };

  const targetBasename = targetFile ? path.basename(targetFile, path.extname(targetFile)) : null;

  function isTargetSource(src) {
    if (!targetFile || !src) return true;
    const cleanSrc = src.replace(/\.(js|mjs|cjs|ts)$/, '');
    const srcBase = path.basename(cleanSrc);
    return srcBase === targetBasename || src.endsWith('/' + targetBasename);
  }

  walk.simple(callerAST, {
    VariableDeclaration(node) {
      for (const decl of node.declarations) {
        if (!decl.init || decl.init.type !== 'CallExpression' || decl.init.callee?.name !== 'require') continue;
        const arg = decl.init.arguments?.[0];
        const reqPath = arg?.value || (arg?.type === 'Literal' ? arg.value : null);
        if (!isTargetSource(reqPath)) continue;

        if (decl.id.type === 'Identifier') {
          // const auth = require('./authService'); -> namespace import
          namespaceBindings.add(decl.id.name);
        } else if (decl.id.type === 'ObjectPattern') {
          // const { login, doTask: myTask } = require('./authService');
          for (const prop of decl.id.properties || []) {
            const keyName = prop.key?.name || prop.key?.value;
            const valName = prop.value?.name;
            if (keyName === symbolName && valName) {
              directBindings.add(valName);
            }
          }
        }
      }
    },
    ImportDeclaration(node) {
      const impPath = node.source?.value;
      if (!isTargetSource(impPath)) return;

      for (const spec of node.specifiers || []) {
        if (spec.type === 'ImportSpecifier') {
          const importedName = spec.imported?.name || spec.imported?.value;
          if (importedName === symbolName && spec.local?.name) {
            directBindings.add(spec.local.name);
          }
        } else if (spec.type === 'ImportNamespaceSpecifier' || spec.type === 'ImportDefaultSpecifier') {
          if (spec.local?.name) {
            namespaceBindings.add(spec.local.name);
          }
        }
      }
    }
  });

  // If caller does not use require/import (e.g. test fixture or inline function),
  // fallback to direct symbol name
  if (directBindings.size === 0 && namespaceBindings.size === 0) {
    directBindings.add(symbolName);
  }

  return { directBindings, namespaceBindings };
}

/**
 * Locate all invocation call sites of a symbol in a caller file.
 */
function extractCallSitesInFile(callerCode, callerAST, symbolName, targetFile = null) {
  const callSites = [];
  if (!callerCode || !callerAST) return callSites;

  const { directBindings, namespaceBindings } = resolveLocalBindings(callerAST, symbolName, targetFile);

  walk.ancestor(callerAST, {
    CallExpression(node, ancestors) {
      let isTargetCall = false;
      let matchedBinding = symbolName;

      if (node.callee.type === 'Identifier' && directBindings.has(node.callee.name)) {
        isTargetCall = true;
        matchedBinding = node.callee.name;
      } else if (node.callee.type === 'MemberExpression') {
        const prop = node.callee.property?.name || node.callee.property?.value;
        const obj = node.callee.object;
        // Red-Team [CRITICAL-03]: Must verify callee.object belongs to target namespace
        if (prop === symbolName && obj && obj.type === 'Identifier' && namespaceBindings.has(obj.name)) {
          isTargetCall = true;
          matchedBinding = `${obj.name}.${prop}`;
        }
      }

      if (!isTargetCall) return;

      // Check if call is awaited or wrapped in .then(), .catch(), .finally()
      const parent = ancestors[ancestors.length - 2];
      const isAwaited = Boolean(parent && parent.type === 'AwaitExpression');
      const hasThenChain = Boolean(
        parent && parent.type === 'MemberExpression' &&
        ['then', 'catch', 'finally'].includes(parent.property?.name || parent.property?.value)
      );

      // Red-Team [WARNING-06]: Direct return in async function properly propagates Promise
      const isReturnedInAsync = Boolean(
        parent && parent.type === 'ReturnStatement' &&
        ancestors.some(a => (a.type === 'FunctionDeclaration' || a.type === 'FunctionExpression' || a.type === 'ArrowFunctionExpression') && a.async)
      );

      const argCount = node.arguments ? node.arguments.length : 0;
      const snippet = sliceCallSite(callerCode, node);

      callSites.push({
        binding: matchedBinding,
        line: node.loc ? node.loc.start.line : 1,
        argCount,
        isAwaited: isAwaited || isReturnedInAsync,
        hasThenChain,
        snippet
      });
    }
  });

  return callSites;
}

/**
 * Check object destructuring parameter compatibility.
 */
function evaluateDestructuredParam(oldParam, newParam) {
  if (!oldParam.isDestructured || !newParam.isDestructured) return null;
  const oldKeys = new Set(oldParam.keys || []);
  const newKeys = new Set(newParam.keys || []);

  // If new keys are missing keys from old parameters without default
  for (const oldKey of oldKeys) {
    if (!newKeys.has(oldKey) && !newParam.hasDefault) {
      return {
        isBreaking: true,
        reason: `Destructured property '${oldKey}' was removed from parameter contract.`
      };
    }
  }
  return null;
}

/**
 * Deterministic AST Structural Contract Evaluator.
 * Evaluates parameter arity, defaults, async transitions, and destructuring changes.
 * @param {object} oldSig - Old signature
 * @param {object} newSig - New signature
 * @param {Array<object>} callSites - Call sites in downstream caller
 * @returns {object} { isBreaking: boolean, verdict: 'BREAKING'|'COMPATIBLE'|'UNKNOWN', reason: string, suggestedRemediation?: string }
 */
function evaluateStructuralContract(oldSig, newSig, callSites = []) {
  if (!oldSig || !newSig) {
    return { isBreaking: false, verdict: 'UNKNOWN', reason: 'Insufficient signature metadata; not assuming compatible.' };
  }

  // 1. Check Sync -> Async Transition Trap
  if (!oldSig.isAsync && newSig.isAsync) {
    const unhandledCalls = callSites.filter(cs => !cs.isAwaited && !cs.hasThenChain);
    if (unhandledCalls.length > 0) {
      const lines = unhandledCalls.map(c => `line ${c.line}`).join(', ');
      return {
        isBreaking: true,
        verdict: 'BREAKING',
        reason: `Function converted from sync to async, but caller invoked without 'await' at ${lines}. Downstream receives unhandled Promise.`,
        suggestedRemediation: `Add 'await' or '.then()' to handle asynchronous Promise at ${lines}.`
      };
    }
  }

  // 2. Check Object Destructuring Parameter Mutations
  for (let i = 0; i < Math.min(oldSig.params.length, newSig.params.length); i++) {
    const destBreak = evaluateDestructuredParam(oldSig.params[i], newSig.params[i]);
    if (destBreak) {
      return {
        isBreaking: true,
        verdict: 'BREAKING',
        reason: destBreak.reason,
        suggestedRemediation: 'Ensure all expected destructured keys are preserved or have default values.'
      };
    }
  }

  // 3. Check Added Mandatory Parameters (Required Count Increased)
  if (newSig.requiredCount > oldSig.requiredCount) {
    const minRequired = newSig.requiredCount;
    const underArityCalls = callSites.filter(cs => cs.argCount < minRequired);
    const missingParam = newSig.params[oldSig.requiredCount]?.name || 'newParam';
    const lines = underArityCalls.length > 0
      ? underArityCalls.map(c => `line ${c.line}`).join(', ')
      : 'no discovered call sites';
    return {
      isBreaking: true,
      verdict: 'BREAKING',
      reason: `Added required parameter '${missingParam}' without default value. Caller arity ${underArityCalls.length > 0 ? `(${underArityCalls[0].argCount} < ${minRequired}) at ${lines}` : 'cannot be proven safe (0 call sites)'}.`,
      suggestedRemediation: `Provide default value for '${missingParam}' or update caller arguments at ${lines}.`
    };
  }

  // 4. Check Removed Parameters
  if (newSig.paramCount < oldSig.requiredCount) {
    return {
      isBreaking: true,
      verdict: 'BREAKING',
      reason: `Parameter count decreased from ${oldSig.requiredCount} to ${newSig.paramCount}. Essential parameters were removed.`,
      suggestedRemediation: 'Restore removed parameters or check downstream usages.'
    };
  }

  // 5. Compatible: Added parameters all have defaults
  if (newSig.paramCount > oldSig.paramCount && newSig.requiredCount <= oldSig.requiredCount) {
    const addedDefaults = newSig.params.slice(oldSig.paramCount).map(p => p.name).join(', ');
    return {
      isBreaking: false,
      verdict: 'COMPATIBLE',
      reason: `New parameter(s) (${addedDefaults}) provide default values. Existing caller arity is preserved without modification.`
    };
  }

  // 6. Identical or fully compatible arity
  return {
    isBreaking: false,
    verdict: 'COMPATIBLE',
    reason: 'Function signature and invocation arity are fully compatible.'
  };
}

/**
 * Execute Stage 2 Semantic Blast Radius Evaluation.
 * Local AST structural contract only — no model, no network.
 */
function evaluateSemanticBlastRadius(targetFile, changedSymbols = [], directFiles = [], graph = null, options = {}) {
  const evaluations = [];
  let hasBreaking = false;
  let hasUnknown = false;

  const targetSource = (options.targetSource || (fs.existsSync(targetFile) ? fs.readFileSync(targetFile, 'utf8') : ''));

  for (const callerFile of directFiles) {
    const callerSource = fs.existsSync(callerFile) ? fs.readFileSync(callerFile, 'utf8') : '';
    let callerAST = null;
    try {
      callerAST = acorn.parse(callerSource, { ecmaVersion: 'latest', sourceType: 'module', locations: true });
    } catch (e) {
      // Fallback if caller syntax is non-standard
    }

    for (const symbol of changedSymbols) {
      let oldSig = options.oldSignatures ? options.oldSignatures[symbol] : null;
      let newSig = options.newSignatures ? options.newSignatures[symbol] : null;

      if (!oldSig && options.diff) {
        oldSig = extractSignatureFromHunk(options.diff, symbol, 'old');
      }
      if (!newSig && options.diff) {
        newSig = extractSignatureFromHunk(options.diff, symbol, 'new');
      }
      if (!newSig && targetSource) {
        newSig = extractSignatureFromHunk(targetSource, symbol);
      }

      const callSites = extractCallSitesInFile(callerSource, callerAST, symbol, targetFile);
      const finalResult = evaluateStructuralContract(oldSig, newSig, callSites);

      if (finalResult.verdict === 'BREAKING') {
        hasBreaking = true;
      } else if (finalResult.verdict === 'UNKNOWN') {
        hasUnknown = true;
      }

      evaluations.push({
        symbol,
        callerFile: path.basename(callerFile),
        callerPath: callerFile,
        callSiteCount: callSites.length,
        verdict: finalResult.verdict,
        isBreaking: finalResult.isBreaking,
        reason: finalResult.reason,
        suggestedRemediation: finalResult.suggestedRemediation || null
      });
    }
  }

  const overallVerdict = hasBreaking ? 'BREAKING' : (hasUnknown ? 'UNKNOWN' : 'COMPATIBLE');

  return {
    isSemanticAware: true,
    isDegraded: false,
    mode: 'ast-contract',
    overallVerdict,
    hasBreaking,
    evaluations
  };
}

module.exports = {
  analyzeSingleParam,
  analyzeSignature,
  extractSignatureFromHunk,
  sliceCallSite,
  resolveLocalBindings,
  extractCallSitesInFile,
  evaluateStructuralContract,
  evaluateSemanticBlastRadius
};
