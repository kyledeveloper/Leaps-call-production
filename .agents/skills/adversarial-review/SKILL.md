---
name: adversarial-review
description: Adversarial Red-Blue dual-agent review process. Mandates a cold-eye, isolated auditor perspective before committing non-trivial changes, evaluating ACID transactions, hermetic sandbox test isolation, regex boundaries, and VCS cleanliness.
user-invocable: true
---

# Adversarial Code Review (Red-Blue Dual-Agent Protocol)

This skill operationalizes a **Red-Team Auditor** mindset to break the "Author Bias" trap where builders overlook secondary failure modes during delivery.

---

## 1. The Four Cold-Eye Lenses

Before any core commit, the diff must be evaluated against four strict criteria:

1. **ACID Transaction Completeness**:
   - Any multi-step database writes (e.g. inserting episodes + reflections + FTS5 index) must be wrapped inside atomic transactions (`db.transaction()`).
   - If an error occurs midway, state must cleanly roll back with zero orphaned rows or ghost index entries.
2. **Hermetic Test Isolation**:
   - Unit tests must NEVER mutate the real workspace, user `.git/` directory, or real configuration files.
   - Tests altering filesystem state must allocate a temporary sandbox directory via `os.tmpdir()` + `fs.mkdtempSync()`, and guarantee cleanup in `finally` or teardown.
3. **Regex Boundary & False-Positive Defense**:
   - Secret scanners and AST tokenizers must use exact word boundaries (`\b` or negative lookarounds/lookbehinds).
   - Verify that substrings in legitimate identifiers (e.g. `disk-sk-...`, `task-sk-...`, URLs like `https://`) do not trigger false positives.
4. **VCS & Artifact Hygiene**:
   - Ephemeral artifacts (e.g. generated HTML visualizations, temporary SQLite databases, `.env` copies) must never be staged into Git commits. Ensure `.gitignore` coverage.

---

## 2. Dual-Phase Adversarial Protocol

### Phase 1: Design-Phase RFC Inquest Gate (方案前置对抗门)
Before writing any code or requesting user sign-off for complex architectural features, the authoring Agent (Blue Team) must invoke an independent Red-Team reviewer to stress-test the implementation plan:

```javascript
invoke_subagent({
  Role: "Adversarial RFC Reviewer",
  TypeName: "research",
  Prompt: `You are the cold-eyed Adversarial RFC Reviewer (Red Team).
Critique the proposed technical implementation plan in implementation_plan.md and architecture artifacts:
1. Hallucination & False Confidence: Could an LLM or heuristic fail silently and approve breaking changes?
2. Boundary & Arity Traps: What parameter structures (destructuring, defaults, rest, async) could break?
3. Performance & Token Exhaustion: Could context snippets or large graphs cause runtime bloat or timeouts?
4. Offline & Sandbox Resilience: Does this break in offline CI without API keys or external network?
5. Overengineering & YAGNI: Is this introducing unwarranted complexity?

Deliver a formal 《方案红队质询函》 (Design Adversarial Inquest) categorized by [BLOCKER], [CONCERN], and [RECOMMENDATION].`
});
```

The Blue Team must incorporate specific defensive clauses and fail-safes into `implementation_plan.md` under `## Red-Team Adversarial Inquest & Defensive Clauses` before requesting user approval to code.

---

### Phase 2: Code-Phase Commit Audit Gate (代码后置对抗门)
After completing TDD implementation and all unit tests pass, the diff must undergo two-tier code inspection prior to any local `git commit`:

#### Tier 1: Automated Static Baseline Script
```bash
node .agents/scripts/runner.js adversary-check --staged
```

#### Tier 2: Dedicated Subagent Code Audit (Mandatory for Core Modules)
```javascript
invoke_subagent({
  Role: "Adversarial Code Auditor",
  TypeName: "research",
  Prompt: `You are the cold-eyed Adversarial Code Auditor (Red Team).
Inspect the pending changes and git diff across the four cold-eye lenses and edge-case resilience:
1. ACID Transaction Completeness: Are all multi-step mutations strictly transactional?
2. Hermetic Test Isolation: Are tests isolated via os.tmpdir() with 0 external pollution?
3. Regex & AST Boundary Defenses: Are there false positives, silent degradations, or unhandled syntax?
4. VCS & Artifact Hygiene: Are temporary files, databases, or sensitive configs properly ignored?
5. Edge Cases & Attack Scenarios: What runtime inputs, dynamic properties, or circular paths could break this code?

Deliver a formal 《代码红队裁决书》 (Code Adversarial Verdict) categorized by [CRITICAL], [WARNING], and [SUGGESTION].`
});
```

The authoring Agent (Blue Team) must publicly address every finding in the conversation, implement necessary defenses, and achieve formal resolution before any local git commit is allowed.
