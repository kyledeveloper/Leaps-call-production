-- Episodes table: Records each significant task / trajectory
CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY,
  intent TEXT NOT NULL,
  context_summary TEXT,
  domain_tags TEXT,
  status TEXT CHECK(status IN ('success', 'failure', 'recovered')) NOT NULL,
  created_at INTEGER NOT NULL
);

-- Reflections table: Distilled cause-and-effect lessons learned (Reflexion)
CREATE TABLE IF NOT EXISTS reflections (
  id TEXT PRIMARY KEY,
  episode_id TEXT REFERENCES episodes(id) ON DELETE CASCADE,
  trigger_pattern TEXT NOT NULL,
  failure_mode TEXT,
  root_cause TEXT NOT NULL,
  corrective_heuristic TEXT NOT NULL,
  confidence_score REAL DEFAULT 1.0,
  importance_score REAL DEFAULT 0.8,
  hit_count INTEGER DEFAULT 0,
  created_at INTEGER NOT NULL,
  last_accessed_at INTEGER
);

-- FTS5 full-text search table for sub-millisecond keyword & heuristic matching
CREATE VIRTUAL TABLE IF NOT EXISTS experience_fts USING fts5(
  reflection_id UNINDEXED,
  intent,
  trigger_pattern,
  failure_mode,
  root_cause,
  corrective_heuristic,
  domain_tags
);

CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status);
CREATE INDEX IF NOT EXISTS idx_reflections_hit_count ON reflections(hit_count);
