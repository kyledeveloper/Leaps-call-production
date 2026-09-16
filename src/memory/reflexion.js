const crypto = require('crypto');

class ReflexionEngine {
  constructor(database) {
    this.db = database;
  }

  /**
   * Distill and store a new reflection from a task experience.
   */
  recordExperience({
    intent,
    context_summary = '',
    domain_tags = [],
    status = 'failure',
    trigger_pattern,
    failure_mode = '',
    root_cause,
    corrective_heuristic,
    confidence_score = 1.0,
    importance_score = 0.8
  }) {
    if (!intent || !trigger_pattern || !root_cause || !corrective_heuristic) {
      throw new Error('Missing required fields for reflection (intent, trigger_pattern, root_cause, corrective_heuristic)');
    }

    const now = Date.now();

    // Check if an identical or near-identical trigger already exists to deduplicate
    const existing = this.db.db.prepare(`
      SELECT r.id, r.confidence_score, r.hit_count
      FROM reflections r
      WHERE r.trigger_pattern = ?
    `).get(trigger_pattern);

    if (existing) {
      // Reinforce existing reflection and refresh the diagnosis (do not freeze a stale heuristic).
      this.db.transaction(() => {
        this.db.db.prepare(`
          UPDATE reflections
          SET hit_count = hit_count + 1,
              confidence_score = MIN(1.0, confidence_score + 0.1),
              last_accessed_at = ?,
              failure_mode = ?,
              root_cause = ?,
              corrective_heuristic = ?
          WHERE id = ?
        `).run(now, failure_mode, root_cause, corrective_heuristic, existing.id);

        this.db.db.prepare(`
          UPDATE experience_fts
          SET intent = ?,
              trigger_pattern = ?,
              failure_mode = ?,
              root_cause = ?,
              corrective_heuristic = ?,
              domain_tags = ?
          WHERE reflection_id = ?
        `).run(
          intent,
          trigger_pattern,
          failure_mode,
          root_cause,
          corrective_heuristic,
          (domain_tags || []).join(' '),
          existing.id
        );
      });
      return { id: existing.id, reinforced: true };
    }

    const episodeId = `ep_${crypto.randomUUID()}`;
    const reflectionId = `ref_${crypto.randomUUID()}`;

    return this.db.transaction(() => {
      this.db.insertEpisode({
        id: episodeId,
        intent,
        context_summary,
        domain_tags,
        status,
        created_at: now
      });

      this.db.insertReflection({
        id: reflectionId,
        episode_id: episodeId,
        intent,
        trigger_pattern,
        failure_mode,
        root_cause,
        corrective_heuristic,
        confidence_score,
        importance_score,
        hit_count: 1,
        domain_tags,
        created_at: now,
        last_accessed_at: now
      });

      return { id: reflectionId, episodeId, reinforced: false };
    });
  }

  /**
   * Retrieve reflections that have been triggered frequently (e.g. >= 3 times)
   * to candidate them for promotion to permanent project rules.
   */
  getCandidateRules(minHitCount = 3) {
    const candidates = this.db.db.prepare(`
      SELECT id, trigger_pattern, failure_mode, root_cause, corrective_heuristic, hit_count
      FROM reflections
      WHERE hit_count >= ?
      ORDER BY hit_count DESC
    `).all(minHitCount);

    return candidates.map(c => ({
      ...c,
      ruleText: `- **[When: ${c.trigger_pattern}]**: ${c.corrective_heuristic} (Root cause: ${c.root_cause}, Bypass count: ${c.hit_count})`
    }));
  }
}

module.exports = { ReflexionEngine };
