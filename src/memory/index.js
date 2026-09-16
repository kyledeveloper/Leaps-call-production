const { MemoryDatabase, DEFAULT_DB_PATH } = require('./db');
const { ReflexionEngine } = require('./reflexion');
const { ExperienceRetriever } = require('./retriever');

class LongTermMemory {
  constructor(dbPath = DEFAULT_DB_PATH) {
    this.db = new MemoryDatabase(dbPath);
    this.reflexion = new ReflexionEngine(this.db);
    this.retriever = new ExperienceRetriever(this.db);
  }

  /**
   * Search for lessons learned relevant to the upcoming task intent
   */
  query(query, options = {}) {
    return this.retriever.searchLessons(query, options);
  }

  /**
   * Format lessons for direct LLM prompt injection (< 100 tokens typically)
   */
  formatPrompt(lessons, options = {}) {
    return this.retriever.formatForPrompt(lessons, options);
  }

  /**
   * Record a new failure reflection or successful lesson
   */
  recordExperience(data) {
    return this.reflexion.recordExperience(data);
  }

  /**
   * Check for high-frequency reflections that should be promoted to permanent rules
   */
  getCandidateRules(minHitCount = 3) {
    return this.reflexion.getCandidateRules(minHitCount);
  }

  /**
   * Get basic stats of the memory database
   */
  stats() {
    const episodeCount = this.db.db.prepare('SELECT COUNT(*) as count FROM episodes').get().count;
    const reflectionCount = this.db.db.prepare('SELECT COUNT(*) as count FROM reflections').get().count;
    return {
      episodeCount,
      reflectionCount,
      dbPath: this.db.dbPath
    };
  }

  /**
   * Seed cold-start vetted golden memories
   */
  seed() {
    const { seedDefaultMemories } = require('./seed');
    return seedDefaultMemories(this);
  }

  close() {
    this.db.close();
  }
}

// Global default singleton
let defaultInstance = null;
function getMemory(dbPath = DEFAULT_DB_PATH) {
  if (!defaultInstance || !defaultInstance.db?.db?.isOpen || (dbPath && defaultInstance.db.dbPath !== dbPath)) {
    defaultInstance = new LongTermMemory(dbPath);
  }
  return defaultInstance;
}

// CLI utility runner
if (require.main === module) {
  const args = process.argv.slice(2);
  const command = args[0] || 'stats';
  const mem = getMemory();

  if (command === 'stats') {
    console.log('=== Long-Term Memory Stats ===');
    console.log(mem.stats());
  } else if (command === 'search') {
    const query = args.slice(1).join(' ');
    console.log(`=== Querying Memory: "${query}" ===`);
    const lessons = mem.query(query);
    console.log(mem.formatPrompt(lessons, { showScores: true }) || 'No prior lessons found.');
  } else if (command === 'seed') {
    console.log('=== Seeding Golden Reflexion Memories ===');
    const seeded = mem.seed();
    console.log(`Successfully processed ${seeded.length} golden heuristics.`);
    console.log(mem.stats());
  } else {
    console.log('Usage: node src/memory/index.js [stats|search <query>|seed]');
  }
}

module.exports = {
  LongTermMemory,
  getMemory,
  DEFAULT_DB_PATH
};
