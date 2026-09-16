const i18next = require('i18next');
const zhCNCommon = require('../locales/zh-CN/common.json');
const enUSCommon = require('../locales/en-US/common.json');

function detectLanguage(env = process.env) {
  const raw = env.LANG || env.LANGUAGE || env.LC_ALL || env.LC_MESSAGES || '';
  return /^zh\b/i.test(raw) || raw.toLowerCase().startsWith('zh') ? 'zh-CN' : 'en-US';
}

i18next.init({
  lng: detectLanguage(),
  fallbackLng: 'en-US',
  resources: {
    'zh-CN': {
      common: zhCNCommon
    },
    'en-US': {
      common: enUSCommon
    }
  },
  defaultNS: 'common',
  interpolation: {
    escapeValue: false
  }
});

i18next.detectLanguage = detectLanguage;
module.exports = i18next;
