const path = require('node:path');
process.chdir(path.resolve(__dirname, '../..'));
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync('finevolab/templates/dashboard.html', 'utf8');
const saved = JSON.parse(fs.readFileSync('tests/frontend/fixtures/task.json', 'utf8'));
const checks = [];
const reports = [{id: '1'.repeat(64), company: 'MSFT', report_year: 2024, status: 'text_extracted'},
    {id: '2'.repeat(64), company: 'NVDA', report_year: 2023, status: 'needs_ocr'}];
const flush = () => new Promise(resolve => setImmediate(resolve));
const environments = [];
const event = {preventDefault() {}};
function create({locale = 'en-US', stored = null, blocked = false} = {}) {
    const calls = [];
    const queue = [];
    const errors = [];
    const dom = new JSDOM(html, {
        url: 'http://127.0.0.1:8003/finevolab', runScripts: 'dangerously',
        beforeParse(window) {
            Object.defineProperty(window.navigator, 'language', {value: locale});
            window.HTMLElement.prototype.scrollIntoView = function () {};
            if (stored) window.localStorage.setItem('finevolab.language', stored);
            if (blocked) Object.defineProperty(window, 'localStorage', {get() { throw new Error('Storage blocked'); }});
            window.fetch = async (url, options) => {
                calls.push({url, options});
                if (url === '/api/uploads' && !options?.method) return {ok: true, json: async () => reports};
                const next = queue.shift();
                if (typeof next === 'function') return next(url, options);
                if (next instanceof Error) throw next;
                if (!next) throw new Error('Unexpected request');
                return {ok: true, status: 200, json: async () => next.data, ...next};
            };
            window.addEventListener('error', event => errors.push(event.message));
        }
    });
    const context = {dom, window: dom.window, el: id => dom.window.document.getElementById(id), calls, queue, errors};
    environments.push(context);
    return context;
}
async function check(name, callback) { await callback(); checks.push({name, status: 'passed'}); }
async function main() {
    const c = create();
    const {window, el, calls, queue} = c;
    await flush();
    await check('language button switches labels, title, placeholders and accessibility names', () => {
        el('language-toggle').click();
        assert.equal(window.document.documentElement.lang, 'zh-CN');
        assert.match(window.document.title, /财务研究工作台/);
        assert.equal(el('question-submit').textContent.trim(), '开始分析');
        assert.equal(el('language-toggle').textContent, 'English');
        assert.match(el('query').placeholder, /收入增长/);
        assert.equal(window.document.querySelector('nav').getAttribute('aria-label'), '主导航');
    });
    await check('Chinese static interface has no untranslated catalog messages', () => {
        const catalog = JSON.parse(el('interface-translations').textContent);
        const walker = window.document.createTreeWalker(window.document.body, window.NodeFilter.SHOW_TEXT);
        const missing = [];
        while (walker.nextNode()) {
            const node = walker.currentNode;
            if (node.parentElement.closest('script,style,#question-text')) continue;
            const text = node.nodeValue.replace(/\s+/g, ' ').trim();
            if (Object.hasOwn(catalog, text) && catalog[text] !== text) missing.push(text);
        }
        assert.deepEqual(missing, []);
    });
    await check('Chinese locale defaults to Chinese and saved preference takes priority', async () => {
        const zh = create({locale: 'zh-CN'});
        const en = create({locale: 'zh-CN', stored: 'en'});
        const restored = create({stored: window.localStorage.getItem('finevolab.language')});
        await flush();
        assert.equal(zh.window.document.documentElement.lang, 'zh-CN');
        assert.equal(en.window.document.documentElement.lang, 'en');
        assert.equal(restored.window.document.documentElement.lang, 'zh-CN');
    });
    await check('blocked storage does not prevent switching', async () => {
        const blocked = create({blocked: true});
        await flush();
        blocked.el('language-toggle').click();
        assert.equal(blocked.window.document.documentElement.lang, 'zh-CN');
        assert.deepEqual(blocked.errors, []);
    });
    await check('custom question and source selections survive switching without API requests', () => {
        el('question-text').value = 'My own financial question <keep>';
        el('question-text').dispatchEvent(new window.Event('input'));
        el('question-documents').options[1].selected = true;
        el('question-documents').dispatchEvent(new window.Event('change'));
        const before = calls.length;
        el('language-toggle').click();
        el('language-toggle').click();
        assert.equal(el('question-text').value, 'My own financial question <keep>');
        assert.equal(el('question-documents').value, reports[0].id);
        assert.equal(calls.length, before);
        assert.match(el('source-summary').textContent, /已选择 1 份财报/);
        assert.equal(el('question-documents').options[2].disabled, true);
        assert.match(el('question-documents').options[2].textContent, /文字识别/);
    });
    await check('example questions switch languages while edited questions remain unchanged', () => {
        window.document.querySelector('[data-question]').click();
        assert.match(el('question-text').value, /比较/);
        el('language-toggle').click();
        assert.match(el('question-text').value, /^Compare MSFT/);
        assert.equal(el('question-documents').value, reports[0].id);
    });
    await check('existing results translate labels without changing numbers, quotes, links or run data', () => {
        window.show(saved);
        const original = {
            amount: el('answer').querySelector('.figure-value').textContent,
            quote: el('answer').querySelector('blockquote').textContent,
            statement: el('answer').querySelector('.claim p').textContent,
            answer: el('answer').querySelector('.answer-copy').textContent,
            evidence: el('evidence').querySelector('details p').textContent,
            trace: el('trace').textContent,
            links: [...el('reports').querySelectorAll('a')].map(link => link.href)
        };
        el('language-toggle').click();
        assert.equal(el('result-status').textContent, '分析完成');
        assert.match(el('answer').querySelector('.figure-label').textContent, /营业收入/);
        assert.match(el('answer').querySelector('th').textContent, /公司/);
        assert.match(el('reports').querySelector('a').textContent, /完整报告/);
        assert.match(el('answer').querySelector('.text-button').textContent, /查看来源/);
        assert.equal(el('answer').querySelector('.figure-value').textContent, original.amount);
        assert.equal(el('answer').querySelector('blockquote').textContent, original.quote);
        assert.equal(el('answer').querySelector('.claim p').textContent, original.statement);
        assert.equal(el('answer').querySelector('.answer-copy').textContent, original.answer);
        assert.equal(el('evidence').querySelector('details p').textContent, original.evidence);
        assert.equal(el('trace').textContent, original.trace);
        assert.deepEqual([...el('reports').querySelectorAll('a')].map(link => link.href), original.links);
    });
    await check('localized input validation changes language without losing invalid state', async () => {
        el('question-text').value = ' ';
        el('question-text').dispatchEvent(new window.Event('input'));
        await el('question-form').onsubmit(event);
        assert.match(el('question-text').validationMessage, /请输入/);
        el('language-toggle').click();
        assert.match(el('question-text').validationMessage, /Enter a financial question/);
        el('question-text').value = 'A valid question';
        el('question-text').dispatchEvent(new window.Event('input'));
        el('language-toggle').click();
        assert.equal(el('question-text').validationMessage, '');
    });
    await check('switching during an analysis preserves the busy state and sends no duplicate request', async () => {
        let resolve;
        queue.push(() => new Promise(done => { resolve = done; }));
        const pending = el('question-form').onsubmit(event);
        await flush();
        assert.equal(el('question-submit').disabled, true);
        assert.match(el('question-status').textContent, /正在核对/);
        const before = calls.length;
        el('language-toggle').click();
        assert.match(el('question-status').textContent, /Following the data/);
        assert.equal(el('question-submit').disabled, true);
        assert.equal(calls.length, before);
        resolve({ok: true, status: 200, json: async () => saved});
        await pending;
        assert.equal(el('question-submit').disabled, false);
        assert.match(el('question-status').textContent, /Analysis complete/);
        el('language-toggle').click();
        assert.match(el('question-status').textContent, /分析完成/);
    });
    await check('new search results and errors use the active language and preserve source text', async () => {
        queue.push({data: [{company: 'MSFT', report_year: 2024, text: 'Revenue',
            source_url: 'https://example.com/report', section: 'item-7'}]});
        await el('search').onsubmit(event);
        assert.match(el('search-status').textContent, /找到 1 条/);
        assert.equal(el('search-results').querySelector('p').textContent, 'Revenue');
        assert.match(el('search-results').querySelector('a').textContent, /原始财报/);
        queue.push(new Error('Offline'));
        await el('search').onsubmit(event);
        assert.match(el('search-status').textContent, /暂时不可用/);
        el('language-toggle').click();
        assert.match(el('search-status').textContent, /unavailable/);
    });
    await check('upload status and appended inspection links switch together', async () => {
        const file = new window.File(['<html>Report</html>'], 'source.html');
        Object.defineProperty(el('upload-file'), 'files', {configurable: true, value: [file]});
        el('upload').reportValidity = () => true;
        queue.push({data: {id: '3'.repeat(64), company: 'NVDA', report_year: 2024,
            status: 'text_extracted', chunk_count: 5}});
        await el('upload').onsubmit(event);
        const link = el('upload-status').querySelector('a').href;
        el('language-toggle').click();
        assert.match(el('upload-status').textContent, /共 5 条/);
        assert.match(el('upload-status').querySelector('a').textContent, /提取数值/);
        assert.equal(el('upload-status').querySelector('a').href, link);
        assert.equal(el('file-name').textContent, 'source.html');
        assert.equal(el('question-documents').value, '3'.repeat(64));
    });
    await check('switching repeatedly leaves the interface error-free', () => {
        for (let i = 0; i < 10; i++) el('language-toggle').click();
        assert.deepEqual(c.errors, []);
        assert.equal(el('question-submit').disabled, false);
    });
    const result = {run_id: 'planner-frontend-language', status: 'passed', checks, count: checks.length,
        method: 'jsdom with mocked responses; no model calls'};
    fs.writeFileSync('project_notes/run_logs/planner_frontend_language.json', JSON.stringify(result, null, 2) + '\n');
    environments.forEach(context => context.window.close());
    console.log(JSON.stringify(result));
}
main().catch(error => {
    console.error(error);
    environments.forEach(context => context.window.close());
    process.exitCode = 1;
});
