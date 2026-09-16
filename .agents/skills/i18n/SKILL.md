---
name: i18n
description: Project CLI locale strings in locales/{en-US,zh-CN}/common.json and src/i18n.js. Use when adding or changing user-facing CLI text, translation keys, interpolation, or running audit-locales.
user-invocable: false
---

# Project i18n

This repo is **not** LobeHub / react-i18next / bun. CLI scripts under `.agents/scripts/` load `src/i18n.js` (i18next + JSON dictionaries). `$LANG` or `--lang` selects `zh-CN` vs `en-US`.

## Files

| Path | Role |
| :--- | :--- |
| `src/i18n.js` | Detects language, inits i18next, defaultNS `common` |
| `locales/en-US/common.json` | English keys (source of truth for new keys) |
| `locales/zh-CN/common.json` | Chinese keys — same key set, 100% in sync |
| `.agents/scripts/audit-locales.js` | Diff the two dictionaries |

Do **not** edit `packages/locales/`, `locales/` generated trees, or call `bun run i18n`. Those paths do not exist here.

## Key rules

- Flat keys with dots: `cli.blast.target`, `action.save`
- Interpolation: `{{name}}` — `i18n.t('cli.audit.file', { file: 'x.json' })`
- Add the key to **both** JSON files in the same change
- Keep `en-US` and `zh-CN` key sets identical; run the auditor after edits

```bash
node .agents/scripts/runner.js audit-locales
node .agents/scripts/runner.js audit-locales --json
```

## Usage in CLI scripts

```javascript
const i18n = require('../../src/i18n');
i18n.changeLanguage(lang); // 'en-US' | 'zh-CN'
console.log(i18n.t('cli.blast.header'));
```

Host-agent chat language follows `AGENTS.md` (match the user). Code identifiers stay English.
