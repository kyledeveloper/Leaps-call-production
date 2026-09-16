const STOP_WORDS = new Set([
  'to', 'in', 'on', 'at', 'for', 'of', 'with', 'by', 'is', 'it', 'as', 'an', 'the', 'and', 'or', 'a',
  'how', 'can', 'you', 'please', 'make', 'do', 'get', 'set', 'this', 'that', 'from', 'into', 'what',
  'build', 'create', 'write', 'want', 'need', 'using', 'use', 'like', 'help'
]);

class ExperienceRetriever {
  constructor(database) {
    this.db = database;
  }

  /**
   * Tokenize input into search tokens supporting English words, numbers, and CJK (Chinese/Japanese/Korean)
   * bigrams and unigrams for accurate full-text matching without external segmenters.
   * Filters out high-frequency stop words to prevent broad accidental over-triggering.
   */
  extractTokens(text) {
    if (!text || typeof text !== 'string') return [];
    const clean = text.replace(/[^\p{L}\p{N}_\s-]/gu, ' ').trim();
    const latin = (clean.match(/[a-zA-Z0-9_-]{2,}/g) || [])
      .filter(w => !STOP_WORDS.has(w.toLowerCase()));
    const cjkChars = clean.match(/[\u4e00-\u9fa5]/g) || [];
    const cjkBigrams = [];
    for (let i = 0; i < cjkChars.length - 1; i++) {
      cjkBigrams.push(cjkChars[i] + cjkChars[i + 1]);
    }
    // Return high-value tokens (latin words + cjk bigrams + standalone cjk if short)
    const combined = [...latin, ...cjkBigrams];
    if (combined.length === 0 && cjkChars.length > 0) {
      combined.push(...cjkChars);
    }
    return Array.from(new Set(combined)).filter(t => t.length >= 2);
  }

  /**
   * Clean and prepare user query into FTS5 safe search query
   */
  sanitizeFtsQuery(query) {
    const tokens = this.extractTokens(query);
    if (tokens.length === 0) return '';
    // Format tokens as FTS5 OR match
    return tokens.map(t => `"${t}"*`).join(' OR ');
  }

  /**
   * Calculate normalized Relevance Score S_rel in [0, 1]
   * Combining token overlap, BM25 rank, and domain tags.
   */
  calcRelevanceScore(row, tokens, domainTags = [], bm25Relative = 0.5) {
    if (!tokens || tokens.length === 0) return 0.0;

    const targetText = [
      row.intent,
      row.trigger_pattern,
      row.failure_mode,
      row.root_cause,
      row.corrective_heuristic
    ].filter(Boolean).join(' ').toLowerCase();

    let matchedCount = 0;
    for (const t of tokens) {
      if (targetText.includes(t.toLowerCase())) {
        matchedCount++;
      }
    }
    const tokenRatio = matchedCount / tokens.length;

    // Check domain tag overlap bonus
    let tagBonus = 0.0;
    if (domainTags && domainTags.length > 0 && row.domain_tags) {
      try {
        const rowTags = typeof row.domain_tags === 'string' ? JSON.parse(row.domain_tags) : row.domain_tags;
        const overlap = domainTags.filter(t => Array.isArray(rowTags) && rowTags.includes(t)).length;
        if (overlap > 0) {
          tagBonus = Math.min(0.2, overlap * 0.1);
        }
      } catch (e) {}
    }

    if (tokenRatio === 0 && tagBonus === 0) {
      return 0.0;
    }

    // Weighted combination of token coverage, BM25 rank relative position, and tag bonus
    const rel = (0.6 * tokenRatio) + (0.2 * bm25Relative) + tagBonus;
    return Math.min(1.0, Math.max(0.0, rel));
  }

  /**
   * Calculate Recency Score S_rec in [0, 1] using exponential decay
   * S_rec = exp(-lambda * delta_t_hours)
   */
  calcRecencyScore(row, now = Date.now(), halfLifeHours = 336) {
    const lastActive = row.last_accessed_at || row.created_at || now;
    const deltaMs = Math.max(0, now - lastActive);
    const deltaHours = deltaMs / (3600 * 1000);

    const lambda = Math.LN2 / Math.max(1, halfLifeHours);
    const rec = Math.exp(-lambda * deltaHours);
    return Math.min(1.0, Math.max(0.01, rec));
  }

  /**
   * Calculate Importance Score S_imp in [0, 1]
   * S_imp = base_importance * confidence * log_scaled(hit_count)
   */
  calcImportanceScore(row) {
    const baseImp = Math.min(1.0, Math.max(0.1, row.importance_score ?? 0.8));
    const confidence = Math.min(1.0, Math.max(0.1, row.confidence_score ?? 1.0));
    const hits = Math.max(0, row.hit_count || 0);

    // Hit count logarithmically boosts importance up to +30%
    const hitBoost = 0.7 + 0.3 * (Math.log(1 + hits) / Math.log(1 + 10));
    const imp = baseImp * confidence * hitBoost;
    return Math.min(1.0, Math.max(0.05, imp));
  }

  /**
   * Search for relevant past reflections given a task intent and optional domain tags
   * using multi-dimensional weighted scoring: Final Score = α * S_rel + β * S_rec + γ * S_imp
   */
  searchLessons(query, {
    domainTags = [],
    limit = 3,
    autoIncrementHit = false,
    weights = { alpha: 0.5, beta: 0.2, gamma: 0.3 },
    halfLifeHours = 336,
    now = Date.now()
  } = {}) {
    const tokens = this.extractTokens(query);
    const ftsQuery = this.sanitizeFtsQuery(query);
    let rawResults = [];

    if (ftsQuery) {
      try {
        const sql = `
          SELECT r.id, r.episode_id, r.trigger_pattern, r.failure_mode, r.root_cause, r.corrective_heuristic,
                 r.confidence_score, r.importance_score, r.hit_count, r.created_at, r.last_accessed_at,
                 e.intent, e.domain_tags, rank
          FROM experience_fts
          JOIN reflections r ON r.id = experience_fts.reflection_id
          LEFT JOIN episodes e ON e.id = r.episode_id
          WHERE experience_fts MATCH ?
          ORDER BY rank
          LIMIT ?
        `;
        rawResults = this.db.db.prepare(sql).all(ftsQuery, limit * 4);
      } catch (err) {
        rawResults = [];
      }
    }

    // Comprehensive LIKE query fallback if FTS has no hits
    if (rawResults.length === 0 && tokens.length > 0) {
      const targetTokens = tokens.slice(0, 5);
      const clauses = targetTokens.map(() => `(
        (e.intent IS NOT NULL AND e.intent LIKE ?) OR
        r.trigger_pattern LIKE ? OR
        r.failure_mode LIKE ? OR
        r.root_cause LIKE ? OR
        r.corrective_heuristic LIKE ?
      )`).join(' OR ');

      const params = [];
      for (const tok of targetTokens) {
        params.push(`%${tok}%`, `%${tok}%`, `%${tok}%`, `%${tok}%`, `%${tok}%`);
      }
      params.push(limit * 4);

      const fallbackSql = `
        SELECT r.id, r.episode_id, r.trigger_pattern, r.failure_mode, r.root_cause, r.corrective_heuristic,
               r.confidence_score, r.importance_score, r.hit_count, r.created_at, r.last_accessed_at,
               e.intent, e.domain_tags, 0 as rank
        FROM reflections r
        LEFT JOIN episodes e ON e.id = r.episode_id
        WHERE ${clauses}
        ORDER BY r.hit_count DESC
        LIMIT ?
      `;

      try {
        rawResults = this.db.db.prepare(fallbackSql).all(...params);
      } catch (e) {
        rawResults = [];
      }
    }

    if (rawResults.length === 0) {
      return [];
    }

    // Normalize weights so alpha + beta + gamma = 1.0
    const rawAlpha = Number.isFinite(weights.alpha) ? weights.alpha : 0.5;
    const rawBeta = Number.isFinite(weights.beta) ? weights.beta : 0.2;
    const rawGamma = Number.isFinite(weights.gamma) ? weights.gamma : 0.3;
    const weightSum = (rawAlpha + rawBeta + rawGamma) || 1.0;
    const alpha = rawAlpha / weightSum;
    const beta = rawBeta / weightSum;
    const gamma = rawGamma / weightSum;

    // Calculate relative BM25 rank for candidates
    const ranks = rawResults.map(r => r.rank || 0);
    const minRank = Math.min(...ranks);
    const maxRank = Math.max(...ranks);

    const scored = rawResults.map(row => {
      let bm25Relative = 0.5;
      if (minRank !== maxRank) {
        // More negative rank is better in FTS5 BM25
        bm25Relative = (maxRank - (row.rank || 0)) / (maxRank - minRank);
      } else if ((row.rank || 0) < 0) {
        bm25Relative = 1.0;
      }

      const sRel = this.calcRelevanceScore(row, tokens, domainTags, bm25Relative);
      const sRec = this.calcRecencyScore(row, now, halfLifeHours);
      const sImp = this.calcImportanceScore(row);

      const finalScore = alpha * sRel + beta * sRec + gamma * sImp;

      return {
        id: row.id,
        episode_id: row.episode_id,
        intent: row.intent,
        trigger_pattern: row.trigger_pattern,
        failure_mode: row.failure_mode,
        root_cause: row.root_cause,
        corrective_heuristic: row.corrective_heuristic,
        confidence_score: row.confidence_score,
        importance_score: row.importance_score,
        hit_count: row.hit_count,
        created_at: row.created_at,
        last_accessed_at: row.last_accessed_at,
        score: Number(finalScore.toFixed(4)),
        relScore: Number(sRel.toFixed(4)),
        recScore: Number(sRec.toFixed(4)),
        impScore: Number(sImp.toFixed(4))
      };
    });

    scored.sort((a, b) => b.score - a.score);
    const topResults = scored
      .filter(r => r.relScore >= 0.40)
      .slice(0, limit);

    if (autoIncrementHit) {
      for (const item of topResults) {
        this.db.updateLastAccessed(item.id, now);
        item.last_accessed_at = now;
      }
    }

    return topResults;
  }

  /**
   * Format retrieved lessons into high-density, low-token Markdown for prompt injection.
   */
  formatForPrompt(lessons, { showScores = false } = {}) {
    if (!lessons || lessons.length === 0) return '';

    const lines = [
      '> [!IMPORTANT]',
      '> **Historical Reflexion Guidance**:'
    ];

    for (const l of lessons) {
      const scoreBadge = showScores
        ? ` *(Score: ${l.score} [Relevance: ${l.relScore} | Recency: ${l.recScore} | Importance: ${l.impScore}])*`
        : '';
      const causeText = l.root_cause ? ` *(Root Cause: ${l.root_cause})*` : '';
      lines.push(`> • **Scenario**: ${l.trigger_pattern}`);
      lines.push(`>   **Heuristic Advice**: ${l.corrective_heuristic}${causeText}${scoreBadge}`);
    }

    return lines.join('\n');
  }
}

module.exports = { ExperienceRetriever };
