const { DatabaseSync } = require('node:sqlite');
const fs = require('fs');
const path = require('path');

const DEFAULT_DB_PATH = path.join(__dirname, '../../.agents/memory.db');

class MemoryDatabase {
  constructor(dbPath = DEFAULT_DB_PATH) {
    this.dbPath = dbPath;
    if (dbPath !== ':memory:') {
      const dir = path.dirname(dbPath);
      if (!fs.existsSync(dir)) {
        fs.mkdirSync(dir, { recursive: true });
      }
    }
    this.db = new DatabaseSync(dbPath);
    this.init();
  }

  init() {
    this.db.exec('PRAGMA foreign_keys = ON;');
    this.db.exec('PRAGMA busy_timeout = 5000;');
    if (this.dbPath !== ':memory:') {
      this.db.exec('PRAGMA journal_mode = WAL;');
    }
    const schemaPath = path.join(__dirname, 'schema.sql');
    const schemaSql = fs.readFileSync(schemaPath, 'utf8');
    this.db.exec(schemaSql);

    // Gracefully handle column additions for existing databases
    try {
      this.db.exec('ALTER TABLE reflections ADD COLUMN importance_score REAL DEFAULT 0.8;');
    } catch (e) {
      // Column already exists
    }
    try {
      this.db.exec('ALTER TABLE reflections ADD COLUMN last_accessed_at INTEGER;');
    } catch (e) {
      // Column already exists
    }
  }

  insertEpisode({ id, intent, context_summary = '', domain_tags = [], status, created_at = Date.now() }) {
    const stmt = this.db.prepare(`
      INSERT INTO episodes (id, intent, context_summary, domain_tags, status, created_at)
      VALUES (?, ?, ?, ?, ?, ?)
    `);
    stmt.run(id, intent, context_summary, JSON.stringify(domain_tags), status, created_at);
  }

  insertReflection({
    id,
    episode_id,
    intent = '',
    trigger_pattern,
    failure_mode = '',
    root_cause,
    corrective_heuristic,
    confidence_score = 1.0,
    importance_score = 0.8,
    hit_count = 0,
    domain_tags = [],
    created_at = Date.now(),
    last_accessed_at = created_at
  }) {
    const stmt = this.db.prepare(`
      INSERT INTO reflections (id, episode_id, trigger_pattern, failure_mode, root_cause, corrective_heuristic, confidence_score, importance_score, hit_count, created_at, last_accessed_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    stmt.run(id, episode_id, trigger_pattern, failure_mode, root_cause, corrective_heuristic, confidence_score, importance_score, hit_count, created_at, last_accessed_at);

    // Sync into FTS5
    const ftsStmt = this.db.prepare(`
      INSERT INTO experience_fts (reflection_id, intent, trigger_pattern, failure_mode, root_cause, corrective_heuristic, domain_tags)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `);
    ftsStmt.run(id, intent, trigger_pattern, failure_mode, root_cause, corrective_heuristic, domain_tags.join(' '));
  }

  incrementHitCount(reflectionId, timestamp = Date.now()) {
    const stmt = this.db.prepare('UPDATE reflections SET hit_count = hit_count + 1, last_accessed_at = ? WHERE id = ?');
    stmt.run(timestamp, reflectionId);
  }

  updateLastAccessed(reflectionId, timestamp = Date.now()) {
    const stmt = this.db.prepare('UPDATE reflections SET last_accessed_at = ? WHERE id = ?');
    stmt.run(timestamp, reflectionId);
  }

  transaction(fn) {
    this.db.exec('BEGIN');
    try {
      const result = fn();
      this.db.exec('COMMIT');
      return result;
    } catch (err) {
      try { this.db.exec('ROLLBACK'); } catch (e) {}
      throw err;
    }
  }

  close() {
    this.db.close();
  }
}

module.exports = {
  MemoryDatabase,
  DEFAULT_DB_PATH
};
