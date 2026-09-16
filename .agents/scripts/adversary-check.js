#!/usr/bin/env node
/**
 * Adversary Code Review Gatekeeper (Red-Team Auditor)
 *
 * Scans git diff / files for the Four Cold-Eye Vulnerabilities:
 * 1. Multi-step DB writes missing db.transaction()
 * 2. Tests touching real .git instead of os.tmpdir()
 * 3. Aggressive regex without word boundary defenses
 * 4. Staging tracked runtime artifacts (*.html, *.db, *.log)
 *
 * Usage:
 *   node .agents/scripts/adversary-check.js [--staged] [--json]
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

class AdversaryAuditor {
  constructor(options = {}) {
    this.repoRoot = options.repoRoot || path.resolve(__dirname, '../../');
    this.staged = options.staged || false;
  }

  getDiff() {
    try {
      const cmd = this.staged
        ? 'git diff --cached --unified=3'
        : 'git diff HEAD --unified=3';
      return execSync(cmd, { cwd: this.repoRoot, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
    } catch (e) {
      return '';
    }
  }

  parseDiffFiles(diffText) {
    if (!diffText || typeof diffText !== 'string') return [];
    const chunks = diffText.split(/(?=^diff --git )/m);
    const files = [];

    for (const chunk of chunks) {
      if (!chunk.trim()) continue;
      const headerMatch = chunk.match(/^diff --git\s+(?:a\/\S+|"(?:\\.|[^"])+")\s+(?:b\/(\S+)|"b\/((?:\\.|[^"])+)")/m);
      if (headerMatch) {
        const filePath = headerMatch[1] || headerMatch[2];
        files.push({ filePath, content: chunk });
      } else {
        files.push({ filePath: '', content: chunk });
      }
    }
    return files;
  }

  auditDiff(diffText) {
    const findings = [];
    const fileChunks = this.parseDiffFiles(diffText);

    for (const { filePath, content } of fileChunks) {
      // 0. Skip self-referential auditor test files/fixtures
      if (filePath && (filePath.includes('adversary.test.js') || filePath.includes('adversary-check.test.js'))) {
        continue;
      }

      const isTest = filePath ? (filePath.startsWith('test/') || filePath.includes('.test.') || filePath.includes('.spec.')) : true;
      const isSrc = filePath ? filePath.startsWith('src/') : true;
      const isScriptOrSrc = filePath ? (filePath.startsWith('src/') || filePath.startsWith('.agents/scripts/')) : true;

      // 1. Check for tests mutating real .git without os.tmpdir (only in test files)
      if (isTest) {
        const testGitMutation = /\+\s*const\s+\w+\s*=\s*path\.resolve\(__dirname,\s*['"]\.\.\/\.git/m;
        if (testGitMutation.test(content)) {
          findings.push({
            severity: 'CRITICAL',
            lens: 'Hermetic Test Isolation',
            message: `Direct reference to real .git/ in test path detected in ${filePath || 'diff'}. Must use os.tmpdir() + fs.mkdtempSync() instead.`
          });
        }
      }

      // 2. Check for multi-table database write operations missing transaction (only in production code)
      if (isSrc) {
        const added = content
          .split('\n')
          .filter(l => l.startsWith('+') && !l.startsWith('+++'))
          .join('\n');
        const hasMultipleInserts = (added.match(/\.prepare\(['"]\s*INSERT INTO/gi) || []).length > 1;
        const hasTransaction = /transaction\s*\(/i.test(added);
        if (hasMultipleInserts && !hasTransaction) {
          findings.push({
            severity: 'WARNING',
            lens: 'ACID Transaction Completeness',
            message: `Multiple INSERT statements found without obvious transaction() wrapping in ${filePath || 'diff'}. Ensure atomic rollback.`
          });
        }
      }

      // 3. Check for overly greedy secret regex without word/token boundary (only in src/scripts)
      if (isScriptOrSrc) {
        const greedySecretRegex = /\+\s*.*regex:\s*\/(?:sk|ghp|gho)-\[a-zA-Z0-9\]/m;
        if (greedySecretRegex.test(content)) {
          findings.push({
            severity: 'CRITICAL',
            lens: 'Regex Boundary & False-Positive Defense',
            message: `Greedy token regex lacking prefix boundary in ${filePath || 'diff'} (e.g. (?<![A-Za-z0-9_-])). May cause false positives.`
          });
        }
      }

      // 4. Check for staged runtime artifacts (.db, .html in diff headers)
      const isTrackedArtifact = filePath
        ? /(?:\.db|\.sqlite|\.html|memory\.db)$/i.test(filePath)
        : /^diff --git a\/(?:.*(?:\.db|\.sqlite|\.html|memory\.db))/im.test(content);

      if (isTrackedArtifact) {
        findings.push({
          severity: 'WARNING',
          lens: 'VCS & Artifact Hygiene',
          message: `Tracked runtime database or generated HTML file detected: ${filePath || 'diff header'}. Verify .gitignore.`
        });
      }
    }

    return {
      passed: findings.filter(f => f.severity === 'CRITICAL').length === 0,
      findings
    };
  }

  run() {
    const diff = this.getDiff();
    return this.auditDiff(diff);
  }
}

if (require.main === module) {
  const args = process.argv.slice(2);
  const isJson = args.includes('--json');
  const isStaged = args.includes('--staged');

  const auditor = new AdversaryAuditor({ staged: isStaged });
  const result = auditor.run();

  if (isJson) {
    console.log(JSON.stringify(result, null, 2));
  } else {
    console.log('=== 🔴 Adversarial Red-Team Code Auditor ===');
    if (result.findings.length === 0) {
      console.log('✅ Clean Pass: No critical architectural or isolation blindspots detected.');
    } else {
      for (const f of result.findings) {
        const icon = f.severity === 'CRITICAL' ? '🚨' : '⚠️';
        console.log(`${icon} [${f.severity}] ${f.lens}: ${f.message}`);
      }
      if (!result.passed) {
        console.log('\n❌ Audit failed: Critical architectural flaws must be resolved before commit.');
        process.exit(1);
      }
    }
  }
}

module.exports = { AdversaryAuditor };
