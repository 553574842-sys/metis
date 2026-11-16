// Locally implemented collector using the website's ordinary browser session.
// Protocol reference: Evil0ctal/Douyin_TikTok_Download_API, commit 4f0bed8.
// No upstream signing, identity-pool or fingerprint-modification code is used.
const {createRequire} = require('node:module');
const {existsSync, mkdirSync, writeFileSync, renameSync} = require('node:fs');
const {resolve, dirname} = require('node:path');

class CollectorError extends Error {
  constructor(code, retryable = false, retryAfter = 0) {super(code); Object.assign(this, {code, retryable, retryAfter});}
}
const fail = (code, retryable = false, delay = 0) => {throw new CollectorError(code, retryable, delay);};
const idPattern = /^\d{10,25}$/;
const creatorPattern = /^[\w-]{8,200}$/;
const platformHost = host => host === 'douyin.com' || host.endsWith('.douyin.com');
function validateInput(input) {
  if (!input || !['detail', 'list'].includes(input.action)) fail('COLLECTOR_INPUT_INVALID');
  if (input.action === 'detail' && !idPattern.test(input.id || '')) fail('COLLECTOR_INPUT_INVALID');
  if (input.action === 'list' && !creatorPattern.test(input.id || '')) fail('COLLECTOR_INPUT_INVALID');
  if (input.cursor != null && !/^\d{1,20}$/.test(input.cursor)) fail('COLLECTOR_CURSOR_INVALID');
  if (!['msedge', 'chrome', 'chromium'].includes(input.channel)) fail('COLLECTOR_INPUT_INVALID');
  if (!Number.isInteger(input.timeout) || input.timeout < 5 || input.timeout > 120) fail('COLLECTOR_INPUT_INVALID');
}
function urls(node) {
  return Array.isArray(node?.url_list) ? [...new Set(node.url_list.filter(url => typeof url === 'string' && url.startsWith('https://')))] : [];
}
function normalizeItem(item) {
  if (!item || typeof item !== 'object' || !idPattern.test(item.aweme_id || '')) fail('PLATFORM_SCHEMA_CHANGED');
  if (item.status?.is_private || item.status?.private_status > 0) fail('PLATFORM_CONTENT_PRIVATE');
  const body = typeof item.desc === 'string' ? item.desc : '';
  const images = (Array.isArray(item.images) ? item.images : item.image_post_info?.images || []).map(urls);
  const video = item.video || {};
  const media = [...new Set([...urls(video.play_addr), ...urls(video.download_addr)])];
  if (images.length > 100) fail('PLATFORM_MEDIA_LIMIT');
  return {
    platform_work_id: item.aweme_id, title: body.split('\n').find(line => line.trim()) || '抖音作品 ' + item.aweme_id,
    body_text: body, hashtags: (item.text_extra || []).filter(tag => typeof tag.hashtag_name === 'string').map(tag => tag.hashtag_name),
    published_at: Number.isInteger(item.create_time) ? item.create_time : null,
    author: {id: item.author?.sec_uid || '', name: item.author?.nickname || ''},
    content_type: images.length ? 'image' : 'video',
    media: images.length ? images.map((candidates, order) => ({asset_type: 'image', order, urls: candidates})) : [{asset_type: 'video', order: 0, urls: media}],
  };
}
function inspectEnvelope(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) fail('PLATFORM_SCHEMA_CHANGED');
  if (payload.captcha || payload.verify_page || payload.verify_center_decision_conf || [8, 2154, 2156, 10000, 10001].includes(payload.status_code)) fail('PLATFORM_ACCESS_RESTRICTED');
  if (payload.status_code != null && payload.status_code !== 0) fail('PLATFORM_CONTENT_UNAVAILABLE');
}
function normalizePage(payload, inputCursor) {
  inspectEnvelope(payload);
  if (!Array.isArray(payload.aweme_list) || ![0, 1, false, true].includes(payload.has_more)) fail('PLATFORM_SCHEMA_CHANGED');
  const hasMore = payload.has_more === 1 || payload.has_more === true;
  const cursor = hasMore && payload.max_cursor != null ? String(payload.max_cursor) : null;
  if (hasMore && (!cursor || !/^\d{1,20}$/.test(cursor) || cursor === String(inputCursor))) fail('PLATFORM_CURSOR_STALLED');
  return {works: payload.aweme_list.map(normalizeItem), next_cursor: cursor,
    coverage_state: hasMore ? 'more_available' : 'platform_returned_end_not_completeness_proof'};
}
function statusGuard(status, retryAfter) {
  const seconds = Number(retryAfter);
  const delay = Number.isFinite(seconds) ? seconds : Math.ceil((Date.parse(retryAfter) - Date.now()) / 1000);
  if (status === 429) fail('PLATFORM_RATE_LIMITED', true, Math.max(600, Number.isFinite(delay) ? delay : 0));
  if ([401, 403].includes(status)) fail('PLATFORM_ACCESS_RESTRICTED');
  if (status === 404) fail('PLATFORM_CONTENT_UNAVAILABLE');
  if (status >= 500) fail('PLATFORM_TEMPORARY_ERROR', true);
  if (status >= 400) fail('PLATFORM_REQUEST_REJECTED');
}

async function collect(input) {
  validateInput(input);
  const root = resolve(__dirname, '..');
  const {chromium} = createRequire(resolve(root, 'frontend/package.json'))('playwright');
  const browser = await chromium.launch({headless: true, ...(input.channel === 'chromium' ? {} : {channel: input.channel})});
  const deadline = Date.now() + input.timeout * 1000;
  const timer = setTimeout(() => browser.close().catch(() => {}), input.timeout * 1000 + 5000);
  let context;
  try {
    context = await browser.newContext({locale: 'zh-CN', timezoneId: 'Asia/Hong_Kong',
      ...(input.session && existsSync(input.session) ? {storageState: input.session} : {})});
    const page = await context.newPage();
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (platformHost(url.hostname) && /\/comment\/|\/danmaku\//.test(url.pathname)) return route.abort();
      return route.continue();
    });
    const records = new Map();
    let stopped;
    const tasks = new Set();
    page.on('response', response => {
      const url = new URL(response.url());
      const expectedPath = input.action === 'detail' ? '/aweme/v1/web/aweme/detail/' : '/aweme/v1/web/aweme/post/';
      const keyName = input.action === 'detail' ? 'aweme_id' : 'sec_user_id';
      if (!platformHost(url.hostname) || url.pathname !== expectedPath || url.searchParams.get(keyName) !== input.id) return;
      const task = (async () => {
        statusGuard(response.status(), response.headers()['retry-after']);
        const text = await response.text();
        if (!text.trim()) fail('PLATFORM_EMPTY_RESPONSE');
        if (text.length > 8 * 1024 * 1024) fail('PLATFORM_RESPONSE_TOO_LARGE');
        let payload;
        try {payload = JSON.parse(text);} catch {fail('PLATFORM_RESPONSE_INVALID');}
        inspectEnvelope(payload);
        if (input.action === 'detail') {
          const item = normalizeItem(payload.aweme_detail);
          if (item.platform_work_id !== input.id) fail('ADAPTER_IDENTITY_INVALID');
          records.set(input.id, item);
        } else {
          const cursor = url.searchParams.get('max_cursor') || '0';
          const normalized = normalizePage(payload, cursor);
          if (normalized.works.some(item => item.author.id && item.author.id !== input.id)) fail('ADAPTER_IDENTITY_INVALID');
          records.set(cursor, normalized);
        }
      })().catch(error => {stopped = error instanceof CollectorError ? error : new CollectorError('PLATFORM_RESPONSE_INVALID');});
      tasks.add(task);
      task.finally(() => tasks.delete(task));
    });
    const url = 'https://www.douyin.com/' + (input.action === 'detail' ? 'video/' : 'user/') + input.id;
    const response = await page.goto(url, {waitUntil: 'domcontentloaded', timeout: Math.min(30000, input.timeout * 1000)});
    if (response) statusGuard(response.status(), response.headers()['retry-after']);
    const desired = input.action === 'detail' ? input.id : String(input.cursor || '0');
    let lastScroll = 0;
    let firstPageAt = 0;
    while (Date.now() < deadline) {
      if (records.has(desired)) {
        if (input.session) {
          mkdirSync(dirname(input.session), {recursive: true});
          const temporary = input.session + '.' + process.pid + '.tmp';
          writeFileSync(temporary, JSON.stringify({cookies: await context.cookies(), origins: []}), {mode: 0o600});
          renameSync(temporary, input.session);
        }
        const value = records.get(desired);
        return input.action === 'detail' ? {...value, original_url: page.url()} : value;
      }
      if (stopped) throw stopped;
      if (await page.getByText(/请完成.*验证|拖动滑块|安全验证/).first().isVisible()) fail('PLATFORM_VERIFICATION_REQUIRED');
      if (input.action === 'list' && records.size) {
        if (!firstPageAt) firstPageAt = Date.now();
        if (await page.getByText('登录后查看更多').first().isVisible()) fail('PLATFORM_LOGIN_REQUIRED');
        // A response can arrive during the awaited visibility check. Prefer
        // that requested page before interpreting the final-page marker.
        if (records.has(desired)) continue;
        const pages = [...records.values()];
        if (!pages.at(-1).next_cursor) fail('PLATFORM_CURSOR_UNAVAILABLE');
        if (records.size > 100) fail('PLATFORM_PAGE_LIMIT');
        if (Date.now() - firstPageAt > 3500 && Date.now() - lastScroll > 1800) {
          // Let the site's own normal scrolling issue the next page request.
          // Never replay signatures, invent a cursor, or call hidden RPC hooks.
          await page.evaluate(() => {
            const list = document.querySelector('[data-e2e="user-post-list"]');
            list?.lastElementChild?.scrollIntoView({block: 'end'});
          });
          const bounds = await page.locator('[data-e2e="user-post-list"]').boundingBox();
          if (bounds) await page.mouse.move(Math.max(1, Math.min(1000, bounds.x + bounds.width / 2)), 500);
          const links = page.locator('[data-e2e="user-post-list"] a[href*="/video/"], [data-e2e="user-post-list"] a[href*="/note/"]');
          if (await links.count()) {
            await links.last().scrollIntoViewIfNeeded({timeout: 3000}).catch(() => {});
            const last = await links.last().boundingBox();
            if (last) await page.mouse.move(last.x + last.width / 2, last.y + Math.min(50, last.height / 2));
          }
          await page.mouse.wheel(0, 2000);
          lastScroll = Date.now();
        }
      }
      await page.waitForTimeout(200);
    }
    fail('PLATFORM_BROWSER_TIMEOUT', true);
  } catch (error) {
    if (error instanceof CollectorError) throw error;
    if (error.name === 'TimeoutError') fail('PLATFORM_BROWSER_TIMEOUT', true);
    fail('PLATFORM_BROWSER_FAILED', true);
  } finally {
    clearTimeout(timer);
    if (context) await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
}

module.exports = {CollectorError, normalizeItem, normalizePage, statusGuard, validateInput, platformHost, collect};
if (require.main === module) {
  let text = '';
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', chunk => {text += chunk; if (text.length > 8192) process.exit(2);});
  process.stdin.on('end', async () => {
    try {const result = await collect(JSON.parse(text)); process.stdout.write(JSON.stringify({ok: true, result}));}
    catch (error) {process.stdout.write(JSON.stringify({ok: false, error: {code: error.code || 'BROWSER_RUNTIME_MISSING', retryable: !!error.retryable, retry_after: error.retryAfter || 0}})); process.exitCode = 1;}
  });
}
