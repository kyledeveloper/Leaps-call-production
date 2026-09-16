const assert = require('assert');
const { detectLanguage } = require('../src/i18n');

console.log('Running i18n language detection tests...');

assert.strictEqual(detectLanguage({ LANG: 'zh_CN.UTF-8' }), 'zh-CN');
assert.strictEqual(detectLanguage({ LANG: 'zh-TW' }), 'zh-CN');
assert.strictEqual(detectLanguage({ LANGUAGE: 'zh' }), 'zh-CN');
assert.strictEqual(detectLanguage({ LANG: 'en_US.UTF-8' }), 'en-US');
assert.strictEqual(detectLanguage({ LANG: 'C' }), 'en-US');
assert.strictEqual(detectLanguage({}), 'en-US');
console.log('✓ Test 1 Passed: detectLanguage follows LANG/LANGUAGE instead of a hardcoded zh-CN default.');

console.log('\nAll i18n tests passed successfully! 🎉');
