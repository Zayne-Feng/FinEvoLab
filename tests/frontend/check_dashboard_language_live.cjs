const path = require('node:path');
process.chdir(path.resolve(__dirname, '../..'));
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const origin = 'http://127.0.0.1:8003';
async function main() {
    const response = await fetch(origin + '/finevolab');
    assert.equal(response.status, 200);
    const html = await response.text();
    assert.ok(html.includes('interface-translations'));
    const calls = [];
    const dom = new JSDOM(html, {url: origin + '/finevolab', runScripts: 'dangerously',
        beforeParse(window) {
            window.localStorage.setItem('finevolab.language', 'zh');
            window.AbortController = globalThis.AbortController;
            window.HTMLElement.prototype.scrollIntoView = function () {};
            window.fetch = (url, options) => { calls.push({url, options}); return fetch(new URL(url, origin), options); };
        }});
    try {
        const window = dom.window;
        const el = id => window.document.getElementById(id);
        assert.equal(window.document.documentElement.lang, 'zh-CN');
        assert.equal(el('submit').textContent.trim(), '查看收入同比');
        await el('analysis').onsubmit({preventDefault() {}});
        assert.equal(el('status').dataset.state, 'complete', el('status').textContent);
        assert.match(el('status').textContent, /分析完成/);
        const state = JSON.parse(el('trace').textContent);
        assert.equal(state.versions.company, 'MSFT');
        assert.equal(state.versions.fiscal_year, 2024);
        assert.equal(state.versions.mode, 'offline');
        assert.match(el('answer').querySelector('.figure-label').textContent, /营业收入/);
        const pdfLink = el('reports').querySelector('a[href$="/pdf"]').href;
        const pdf = await fetch(pdfLink);
        assert.equal(pdf.status, 200);
        assert.equal(Buffer.from(await pdf.arrayBuffer()).subarray(0, 5).toString(), '%PDF-');
        const requestsBefore = calls.length;
        const trace = el('trace').textContent;
        el('language-toggle').click();
        assert.equal(window.document.documentElement.lang, 'en');
        assert.match(el('status').textContent, /Analysis complete/);
        assert.equal(el('trace').textContent, trace);
        assert.equal(calls.length, requestsBefore);
        el('language-toggle').click();
        assert.equal(window.document.documentElement.lang, 'zh-CN');
        const result = {run_id: 'planner-frontend-language-http', status: 'passed', task_id: state.task_id,
            checks: ['Live bilingual page returns HTTP 200', 'Stored Chinese preference applied',
                'Chinese controls preserve the offline API payload', 'Chinese result labels and status',
                'Real PDF report returns HTTP 200', 'Language switching preserves results without extra requests'],
            model_calls: 0, method: 'Real HTTP APIs through jsdom; no rendered browser'};
        fs.writeFileSync('project_notes/run_logs/planner_frontend_language_http.json', JSON.stringify(result, null, 2) + '\n');
        console.log(JSON.stringify(result));
    } finally { dom.window.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
