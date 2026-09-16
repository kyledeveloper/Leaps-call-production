const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const { DatabaseSync } = require('node:sqlite');

const DEFAULT_DB_PATH = path.join(__dirname, '../../.agents/memory.db');

class PatternTracker {
  constructor(dbOrPath = DEFAULT_DB_PATH) {
    if (typeof dbOrPath === 'string') {
      if (dbOrPath !== ':memory:') {
        const dir = path.dirname(dbOrPath);
        if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
      }
      this.db = new DatabaseSync(dbOrPath);
    } else if (dbOrPath && dbOrPath.db) {
      // Re-use MemoryDatabase instance if passed
      this.db = dbOrPath.db;
    } else {
      this.db = dbOrPath;
    }
    this.init();
  }

  init() {
    if (this.db && typeof this.db.exec === 'function') {
      try {
        this.db.exec('PRAGMA foreign_keys = ON;');
        this.db.exec('PRAGMA busy_timeout = 5000;');
        this.db.exec('PRAGMA journal_mode = WAL;');
      } catch (e) {}
    }
    const schemaPath = path.join(__dirname, 'schema.sql');
    if (fs.existsSync(schemaPath) && this.db && typeof this.db.exec === 'function') {
      const schemaSql = fs.readFileSync(schemaPath, 'utf8');
      this.db.exec(schemaSql);
    }
  }

  computeHash(nameSlug, intentSummary) {
    const normSlug = (nameSlug || '').trim().toLowerCase();
    const normIntent = (intentSummary || '').trim().toLowerCase();
    return crypto.createHash('sha256').update(`${normSlug}::${normIntent}`).digest('hex').slice(0, 16);
  }

  /**
   * Track an execution pattern.
   * If occurrences reach threshold (default 3), trigger synthesis recommendation.
   */
  track({ nameSlug, intentSummary, commandTemplate = '', threshold = 3 }) {
    const patternHash = this.computeHash(nameSlug, intentSummary);
    const now = Date.now();

    const existing = this.db.prepare(`
      SELECT pattern_hash, name_slug, intent_summary, command_template, occurrences, status
      FROM tool_candidates
      WHERE pattern_hash = ?
    `).get(patternHash);

    if (existing) {
      const newCount = existing.occurrences + 1;
      this.db.prepare(`
        UPDATE tool_candidates
        SET occurrences = ?, last_seen_at = ?
        WHERE pattern_hash = ?
      `).run(newCount, now, patternHash);

      const updated = {
        ...existing,
        occurrences: newCount,
        last_seen_at: now
      };

      return {
        patternHash,
        occurrences: newCount,
        shouldSynthesize: newCount >= threshold && existing.status === 'tracking',
        candidate: updated
      };
    } else {
      this.db.prepare(`
        INSERT INTO tool_candidates (pattern_hash, name_slug, intent_summary, command_template, occurrences, status, created_at, last_seen_at)
        VALUES (?, ?, ?, ?, 1, 'tracking', ?, ?)
      `).run(patternHash, nameSlug, intentSummary, commandTemplate, now, now);

      const candidate = {
        pattern_hash: patternHash,
        name_slug: nameSlug,
        intent_summary: intentSummary,
        command_template: commandTemplate,
        occurrences: 1,
        status: 'tracking',
        created_at: now,
        last_seen_at: now
      };

      return {
        patternHash,
        occurrences: 1,
        shouldSynthesize: 1 >= threshold,
        candidate
      };
    }
  }

  markSynthesized(patternHash, scriptPath) {
    this.db.prepare(`
      UPDATE tool_candidates
      SET status = 'synthesized', synthesized_script_path = ?
      WHERE pattern_hash = ?
    `).run(scriptPath, patternHash);
  }

  getCandidates(minOccurrences = 3) {
    return this.db.prepare(`
      SELECT pattern_hash, name_slug, intent_summary, command_template, occurrences, status, synthesized_script_path
      FROM tool_candidates
      WHERE occurrences >= ?
      ORDER BY occurrences DESC
    `).all(minOccurrences);
  }

  close() {
    if (this.db && typeof this.db.close === 'function') {
      try {
        this.db.close();
      } catch (e) {}
    }
  }
}

module.exports = { PatternTracker };
