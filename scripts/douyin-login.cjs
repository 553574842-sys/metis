// Project-owned browser session. Does not read the user's browser profile.
const {createRequire} = require('node:module');
const {mkdirSync, writeFileSync, renameSync} = require('node:fs');
const {resolve, dirname} = require('node:path');
const root = resolve(__dirname, '..');
const {chromium} = createRequire(resolve(root, 'frontend/package.json'))('playwright');
const statePath = resolve(root, 'runtime/douyin-session.json');
(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: false});
  try {
    const context = await browser.newContext({locale: 'zh-CN', timezoneId: 'Asia/Hong_Kong'});
    const page = await context.newPage();
    await page.goto('https://www.douyin.com/', {waitUntil: 'domcontentloaded', timeout: 45000});
    console.log(JSON.stringify({state: 'waiting_for_user_login', timeout_seconds: 900}));
    const until = Date.now() + 900000;
    while (Date.now() < until && !page.isClosed()) {
      const cookies = await context.cookies();
      if (cookies.some(cookie => ['sessionid', 'sessionid_ss'].includes(cookie.name) && cookie.value)) {
        mkdirSync(dirname(statePath), {recursive: true});
        const temporary = statePath + '.' + process.pid + '.tmp';
        writeFileSync(temporary, JSON.stringify({cookies, origins: []}), {mode: 0o600});
        renameSync(temporary, statePath);
        console.log(JSON.stringify({state: 'login_saved', credentials_printed: false}));
        return;
      }
      await page.waitForTimeout(1500);
    }
    console.log(JSON.stringify({state: 'login_not_completed'}));
  } finally {await browser.close();}
})().catch(error => {console.log(JSON.stringify({state: 'failed', error_type: error.name})); process.exitCode=1;});
