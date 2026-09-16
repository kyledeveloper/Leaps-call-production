-- Tool Candidates Table: Tracks repetitive task patterns and frequency
CREATE TABLE IF NOT EXISTS tool_candidates (
  pattern_hash TEXT PRIMARY KEY,
  name_slug TEXT NOT NULL,
  intent_summary TEXT NOT NULL,
  command_template TEXT,
  occurrences INTEGER DEFAULT 1,
  status TEXT CHECK(status IN ('tracking', 'synthesized', 'rejected')) DEFAULT 'tracking',
  synthesized_script_path TEXT,
  created_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tool_candidates_status ON tool_candidates(status);
CREATE INDEX IF NOT EXISTS idx_tool_candidates_occurrences ON tool_candidates(occurrences);
