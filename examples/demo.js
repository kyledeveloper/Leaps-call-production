const i18n = require('../src/i18n');

i18n.changeLanguage('zh-CN');
console.log('=== 🇨🇳 Chinese (zh-CN) Output ===');
console.log(i18n.t('app.title'));
console.log(i18n.t('common.welcome', { name: 'Kyle' }));
console.log('Button samples:', i18n.t('action.save'), '/', i18n.t('action.cancel'));

// Switch to English
i18n.changeLanguage('en-US');

console.log('\n=== 🇺🇸 English (en-US) Output ===');
console.log(i18n.t('app.title'));
console.log(i18n.t('common.welcome', { name: 'Kyle' }));
console.log('Buttons:', i18n.t('action.save'), '/', i18n.t('action.cancel'));
