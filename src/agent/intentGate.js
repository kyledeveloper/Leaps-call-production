/**
 * Ambiguous-intent (grill-me) gate and on-demand Ponytail detector.
 * Deterministic: no network, no LLM.
 */

const MACRO_PRODUCT_EN =
  /\b(platform|saas|dashboard|portal|website|web\s*app|mobile\s*app|ai\s*app|social\s*(network|app))\b/i;
const MACRO_PRODUCT_ZH = /操作系统|平台|官网|门户|社交网络/;

const BUILD_MACRO_EN =
  /(?:^|\s)(?:please\s+)?(?:make|build|create|develop|design)\b[\s\S]{0,80}\b(app|application|platform|system|website|site)\b/i;
const BUILD_MACRO_ZH = /(?:帮我|做一个|写一个|开发|打造)[\s\S]{0,40}(平台|系统|应用|网站|官网|app)/;

const CONCRETE_MARKER =
  /(?:^|[\s/])(?:src|test|tests|lib|scripts)\/|\S+\.(?:js|ts|mjs|cjs|jsx|tsx)\b/i;

const CONCRETE_VERB =
  /\b(install|audit|refactor|test|commit|fix|scan|query|seed|search|reflect|track|synthesize|parse|index|clone|lint|format|patch|revert|merge|diff|debug|trace|profile|benchmark)\b|安装|审计|重构|测试|修复|扫描|检索|克隆/i;

const PONYTAIL_RE =
  /\b(ponytail|yagni|lazy mode)\b|\/ponytail|精简|最简方案|不要过度设计/i;

const PONYTAIL_RUNGS = [
  'Does this need to exist at all? (YAGNI)',
  'Already in this codebase? Reuse it.',
  'Stdlib does it? Use it.',
  'Native platform feature covers it?',
  'Already-installed dependency solves it?',
  'Can it be one line?',
  'Only then: the minimum code that works.'
];

function normalizeIntent(intent) {
  return String(intent || '').trim();
}

function detectPonytail(intent) {
  const text = normalizeIntent(intent);
  if (!text || !PONYTAIL_RE.test(text)) {
    return { active: false, intensity: null, rungs: [] };
  }
  let intensity = 'lite';
  if (/\bultra\b/i.test(text) || /极限/.test(text)) intensity = 'ultra';
  else if (/\bfull\b/i.test(text) || /完整/.test(text)) intensity = 'full';
  return { active: true, intensity, rungs: PONYTAIL_RUNGS.slice() };
}

function grillQuestions(intent) {
  const snippet = normalizeIntent(intent).slice(0, 80) || 'this request';
  return [
    `What is the v0.1 slice of "${snippet}" that must ship, and what is explicitly out of scope?`,
    'Who is the user, and what is the single success check we can run locally?',
    'Where does data live (files, sqlite, none), and what must never be invented?',
    'Name the first concrete file or command that would change if we start now.'
  ];
}

function isMacroRequest(text) {
  return BUILD_MACRO_EN.test(text) || BUILD_MACRO_ZH.test(text)
    || MACRO_PRODUCT_EN.test(text) || MACRO_PRODUCT_ZH.test(text);
}

/**
 * Hard-stop coding when the intent is a macro product request.
 * --target is blast-radius only; it does not clarify the spec. Use --force after grill-me.
 */
function evaluateIntentGate(intent, { force = false } = {}) {
  const text = normalizeIntent(intent);
  if (force) {
    return { blocked: false, reason: 'forced', questions: [], gate: null };
  }
  if (!text) {
    return {
      blocked: true,
      reason: 'empty-intent',
      questions: grillQuestions(''),
      gate: 'grill-me'
    };
  }
  if (CONCRETE_MARKER.test(text)) {
    return { blocked: false, reason: 'has-path', questions: [], gate: null };
  }
  if (CONCRETE_VERB.test(text) && !BUILD_MACRO_EN.test(text) && !BUILD_MACRO_ZH.test(text)) {
    return { blocked: false, reason: 'concrete-verb', questions: [], gate: null };
  }
  if (isMacroRequest(text)) {
    return {
      blocked: true,
      reason: 'ambiguous-macro',
      questions: grillQuestions(text),
      gate: 'grill-me'
    };
  }
  return { blocked: false, reason: 'specific', questions: [], gate: null };
}

module.exports = {
  evaluateIntentGate,
  detectPonytail,
  grillQuestions,
  PONYTAIL_RUNGS
};
