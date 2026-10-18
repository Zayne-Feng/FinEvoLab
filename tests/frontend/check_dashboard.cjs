const path = require('node:path');
process.chdir(path.resolve(__dirname, '../..'));
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');

const html = fs.readFileSync('finevolab/templates/dashboard.html', 'utf8');
const saved = JSON.parse(fs.readFileSync('tests/frontend/fixtures/task.json', 'utf8'));
const reports = Array.from({length: 6}, (_, i) => ({
    id: String(i + 1).repeat(64), company: 'MSFT', report_year: 2024, status: 'text_extracted'
}));
const calls = [];
const queue = [];
const errors = [];
const dom = new JSDOM(html, {
    url: 'http://127.0.0.1:8003/finevolab',
    runScripts: 'dangerously',
    beforeParse(window) {
        window.HTMLElement.prototype.scrollIntoView = function () {};
        window.fetch = async (url, options) => {
            calls.push({url, options});
            if (url === '/api/uploads' && !options?.method) return {ok: true, json: async () => reports};
            const response = queue.shift();
            if (!response) throw new Error('Unexpected request: ' + url);
            if (response instanceof Error) throw response;
            return {ok: true, status: 200, json: async () => response.data, ...response};
        };
        window.addEventListener('error', event => errors.push(event.message));
    }
});
const window = dom.window;
const doc = window.document;
const el = id => doc.getElementById(id);
const event = {preventDefault() {}};
const checks = [];
const check = (name, callback) => { callback(); checks.push(name); };
const flush = () => new Promise(resolve => setImmediate(resolve));

async function main() {
    await flush();
    check('unique IDs and named form fields', () => {
        const ids = [...doc.querySelectorAll('[id]')].map(element => element.id);
        assert.equal(ids.length, new Set(ids).size);
        for (const field of doc.querySelectorAll('input,select,textarea')) {
            assert.ok(doc.querySelector('label[for="' + field.id + '"]'), field.id);
        }
    });
    check('uploaded source list populated', () => assert.equal(el('question-documents').options.length, 7));
    check('suggestion changes question without sending a request', () => {
        const before = calls.length;
        doc.querySelectorAll('[data-question]')[1].click();
        assert.match(el('question-text').value, /revenue growth/);
        assert.equal(calls.length, before);
    });
    check('six report selections rejected', () => {
        for (const option of el('question-documents').options) option.selected = Boolean(option.value);
        el('question-documents').dispatchEvent(new window.Event('change'));
        assert.equal(el('question-documents').validity.valid, false);
        const before = calls.length;
        el('question-form').onsubmit(event);
        assert.equal(calls.length, before);
    });
    for (const option of el('question-documents').options) option.selected = option.value === reports[0].id;
    queue.push({data: saved});
    await el('question-form').onsubmit(event);
    check('question uses selected reports and renders saved hybrid result', () => {
        const call = calls.at(-1);
        assert.equal(call.url, '/api/questions');
        assert.deepEqual(JSON.parse(call.options.body).upload_ids, [reports[0].id]);
        assert.equal(el('result').hidden, false);
        assert.equal(el('result-status').dataset.state, 'complete');
        assert.equal(el('answer').querySelectorAll('.claim').length, 2);
        assert.equal(el('evidence').querySelectorAll('details').length, 4);
        assert.equal(el('reports').querySelectorAll('a').length, 2);
        assert.match(el('answer').textContent, /125\.85%/);
        assert.equal(el('question-submit').disabled, false);
    });
    check('claim links open the matching evidence', () => {
        const button = el('answer').querySelector('.text-button');
        assert.ok(button);
        button.click();
        assert.equal(el('evidence').querySelectorAll('details[open]').length, 1);
    });
    check('partial business result is distinct from completed workflow', () => {
        const state = structuredClone(saved);
        state.phases.analyze.result.overall_status = 'partial';
        window.show(state);
        assert.equal(el('result-status').dataset.state, 'partial');
        assert.equal(el('result-status').textContent, 'Partially complete');
    });
    check('dynamic text is escaped and unsafe source URLs are not linked', () => {
        const state = structuredClone(saved);
        state.phases.analyze.result.answer.answer = '<img src=x onerror=alert(1)>';
        state.phases.analyze.result.evidence[0].source_url = 'javascript:alert(1)';
        window.show(state);
        assert.equal(el('answer').querySelector('img'), null);
        assert.match(el('answer').textContent, /<img/);
        assert.equal(el('evidence').querySelector('a[href^="javascript:"]'), null);
    });
    queue.push({data: saved});
    await el('analysis').onsubmit(event);
    check('quick analysis retains the existing API contract', () => {
        assert.equal(calls.at(-1).url, '/api/tasks');
        assert.deepEqual(JSON.parse(calls.at(-1).options.body), {company: 'MSFT', fiscal_year: 2024, mode: 'offline'});
    });
    el('search-company').value = 'AAPL';
    el('search-year').value = '2023';
    queue.push({data: [{company: 'NVDA', report_year: 2023, section: 'item-7', text: 'A source passage.', source_url: 'https://example.com/report'}]});
    await el('search').onsubmit(event);
    check('report search uses independent filters', () => {
        const target = new URL(calls.at(-1).url, window.location.origin);
        assert.equal(target.searchParams.get('company'), 'AAPL');
        assert.equal(target.searchParams.get('year'), '2023');
        assert.equal(el('search-results').querySelectorAll('.search-result').length, 1);
    });
    queue.push({data: []});
    await el('search').onsubmit(event);
    check('empty search has a useful message', () => assert.match(el('search-status').textContent, /No passages found/));
    queue.push(new Error('Offline'));
    await el('search').onsubmit(event);
    check('search connection failure restores the action', () => {
        assert.equal(el('search-submit').disabled, false);
        assert.equal(el('search-status').dataset.state, 'failed');
    });
    check('large report rejected before upload', () => {
        Object.defineProperty(el('upload-file'), 'files', {configurable: true, value: [{name: 'large.pdf', size: 33 * 1024 * 1024}]});
        el('upload-file').onchange();
        assert.equal(el('upload-file').validity.customError, true);
    });
    const file = new window.File(['<html>Report</html>'], 'report.html', {type: 'text/html'});
    Object.defineProperty(el('upload-file'), 'files', {configurable: true, value: [file]});
    el('upload-file').onchange();
    el('upload').reportValidity = () => true;
    queue.push({data: {id: 'a'.repeat(64), company: 'NVDA', report_year: 2024, status: 'text_extracted', chunk_count: 5}});
    await el('upload').onsubmit(event);
    check('new upload selected and ready for the next question', () => {
        assert.equal(calls.at(-1).url, '/api/uploads');
        assert.equal(calls.at(-1).options.body.get('file').name, 'report.html');
        assert.equal(el('question-documents').selectedOptions.length, 1);
        assert.equal(el('question-documents').value, 'a'.repeat(64));
        assert.equal(el('upload-status').dataset.state, 'complete');
        assert.equal(el('upload-submit').disabled, false);
    });
    queue.push({data: {id: 'b'.repeat(64), company: 'NVDA', report_year: 2024, status: 'needs_ocr', chunk_count: 0}});
    await el('upload').onsubmit(event);
    check('image-only report is not selected as a usable source', () => {
        assert.equal([...el('question-documents').options].find(option => option.value === 'b'.repeat(64)).disabled, true);
        assert.equal(el('question-documents').value, 'a'.repeat(64));
        assert.equal(el('upload-status').dataset.state, 'partial');
    });
    const failed = structuredClone(saved);
    failed.status = 'failed';
    window.show(failed);
    queue.push({data: saved});
    await el('reports').querySelector('button').onclick();
    check('resume keeps the saved session endpoint', () => {
        assert.equal(calls.at(-1).url, '/api/tasks/' + saved.task_id + '/resume');
        assert.equal(calls.at(-1).options.method, 'POST');
        assert.equal(calls.at(-1).options.body, undefined);
    });
    queue.push(new Error('Offline'));
    await el('question-form').onsubmit(event);
    check('analysis failure clears busy state', () => {
        assert.equal(el('question-submit').disabled, false);
        assert.equal(el('question-submit').hasAttribute('aria-busy'), false);
        assert.equal(el('question-status').dataset.state, 'failed');
    });
    check('script has no uncaught errors or unused mock responses', () => {
        assert.deepEqual(errors, []);
        assert.equal(queue.length, 0);
    });
    const result = {run_id: 'planner-frontend-main', status: 'passed', checks, count: checks.length,
        saved_session: saved.task_id, method: 'jsdom with mocked API responses; no model calls'};
    fs.writeFileSync('project_notes/run_logs/planner_frontend_main.json', JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result));
    window.close();
}
main().catch(error => { console.error(error); window.close(); process.exitCode = 1; });
