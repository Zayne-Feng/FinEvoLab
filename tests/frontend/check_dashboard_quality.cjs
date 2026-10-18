const path = require('node:path');
process.chdir(path.resolve(__dirname, '../..'));
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const html = fs.readFileSync('finevolab/templates/dashboard.html', 'utf8');
const saved = JSON.parse(fs.readFileSync('tests/frontend/fixtures/task.json', 'utf8'));
const checks = [];
const failures = [];
const pending = [];
const calls = [];
const reports = Array.from({length: 6}, (_, i) => ({
    id: String(i + 1).repeat(64), company: 'MSFT', report_year: 2024, status: 'text_extracted'
}));
const dom = new JSDOM(html, {
    url: 'http://127.0.0.1:8003/finevolab', runScripts: 'dangerously',
    beforeParse(window) {
        window.HTMLElement.prototype.scrollIntoView = function () {};
        window.fetch = async (url, options) => {
            calls.push({url, options});
            if (url === '/api/uploads' && !options?.method) return {ok: true, status: 200, json: async () => reports};
            if (url === '/api/corpus') return {ok: true, status: 200, json: async () => ({reports: [
                {company: 'MSFT', fiscal_year: 2024}, {company: 'MSFT', fiscal_year: 2023},
                {company: 'AAPL', fiscal_year: 2024}
            ]})};
            const response = pending.shift();
            if (response instanceof Error) throw response;
            if (typeof response === 'function') return response(url, options);
            if (!response) throw new Error('Unexpected request: ' + url);
            return {ok: true, status: 200, json: async () => response.data, ...response};
        };
    }
});
const window = dom.window;
const el = id => window.document.getElementById(id);
const event = {preventDefault() {}};
const check = async (name, callback) => {
    try { await callback(); checks.push({name, status: 'passed'}); }
    catch (error) { checks.push({name, status: 'failed', message: error.message}); failures.push(name); }
};
const flush = () => new Promise(resolve => setImmediate(resolve));
function reset() {
    pending.length = 0;
    for (const option of el('question-documents').options) option.selected = false;
    el('question-documents').setCustomValidity('');
    el('question-text').setCustomValidity('');
    el('query').setCustomValidity('');
}
async function submitQuestion(data) {
    reset();
    el('question-text').value = 'Compare Microsoft and Apple FY2024 revenue.';
    for (const option of el('question-documents').options) option.selected = false;
    pending.push({data});
    await el('question-form').onsubmit(event);
}

async function main() {
    await flush();
    await check('ticker pattern compiles under browser Unicode Sets rules', () => {
        const pattern = new RegExp('^(?:' + el('upload-company').pattern + ')$', 'v');
        assert.ok(pattern.test('BRK.B'));
        assert.ok(pattern.test('BRK-B'));
        assert.ok(!pattern.test('../config'));
    });
    await check('blank question is blocked before an HTTP request', async () => {
        reset();
        el('question-text').value = '   ';
        const count = calls.length;
        pending.push({data: saved});
        await el('question-form').onsubmit(event);
        assert.equal(calls.length, count);
        assert.ok(!el('question-text').validity.valid);
    });
    await check('blank search is blocked before an HTTP request', async () => {
        reset();
        el('query').value = '   ';
        const count = calls.length;
        pending.push({data: []});
        await el('search').onsubmit(event);
        assert.equal(calls.length, count);
    });
    await check('a collapsed source picker opens when too many reports are selected', () => {
        const picker = window.document.querySelector('.source-picker');
        picker.open = false;
        for (const option of el('question-documents').options) option.selected = Boolean(option.value);
        el('question-text').value = 'Compare revenue.';
        el('question-documents').dispatchEvent(new window.Event('change'));
        el('question-form').onsubmit(event);
        assert.equal(picker.open, true);
        assert.equal(el('question-documents').validity.valid, false);
        for (const option of el('question-documents').options) option.selected = false;
        el('question-documents').dispatchEvent(new window.Event('change'));
    });
    await check('HTTP error with non-JSON body is not described as a connection failure', async () => {
        reset();
        el('question-text').value = 'Compare revenue.';
        pending.push({ok: false, status: 502, json: async () => { throw new SyntaxError('Unexpected HTML'); }});
        await el('question-form').onsubmit(event);
        assert.equal(el('question-status').dataset.state, 'failed');
        assert.doesNotMatch(el('question-status').textContent, /check the connection|Unable to reach/);
        assert.equal(el('question-submit').disabled, false);
    });
    await check('successful malformed response is described as a response error', async () => {
        reset();
        pending.push({data: {}});
        await el('question-form').onsubmit(event);
        assert.equal(el('question-status').dataset.state, 'failed');
        assert.match(el('question-status').textContent, /response/i);
    });
    await check('analysis requests include an abort signal', async () => {
        await submitQuestion(saved);
        assert.ok(calls.at(-1).options.signal);
    });
    await check('request timeout actually aborts a pending transport', async () => {
        reset();
        pending.push((url, options) => new Promise((resolve, reject) => {
            options.signal.addEventListener('abort', () => reject(new window.DOMException('Aborted', 'AbortError')));
        }));
        await assert.rejects(window.fetchJson('/probe', {}, 5), {name: 'AbortError'});
    });
    await check('request timeout also covers a stalled response body', async () => {
        reset();
        pending.push((url, options) => ({ok: true, status: 200, json: () => new Promise((resolve, reject) => {
            options.signal.addEventListener('abort', () => reject(new window.DOMException('Aborted', 'AbortError')));
        })}));
        await assert.rejects(window.fetchJson('/probe', {}, 5), {name: 'AbortError'});
    });
    await check('concurrent analysis submissions are prevented', async () => {
        reset();
        let resolve;
        pending.push(() => new Promise(done => { resolve = done; }));
        const first = el('analysis').onsubmit(event);
        await flush();
        const disabledDuringRun = el('question-submit').disabled;
        const count = calls.length;
        pending.push({data: saved});
        const second = el('question-form').onsubmit(event);
        resolve({ok: true, status: 200, json: async () => saved});
        await Promise.all([first, second]);
        assert.equal(disabledDuringRun, true);
        assert.equal(calls.length, count);
        assert.equal(el('question-submit').disabled, false);
    });
    await check('timeout has a specific message and releases the action', async () => {
        reset();
        const error = new Error('Aborted');
        error.name = 'AbortError';
        pending.push(error);
        await el('analysis').onsubmit(event);
        assert.match(el('status').textContent, /timed out|taking longer/i);
        assert.equal(el('submit').disabled, false);
    });
    await check('failed HTML extraction is not reported as a usable source', async () => {
        reset();
        el('upload').reportValidity = () => true;
        Object.defineProperty(el('upload-file'), 'files', {configurable: true, value: [
            new window.File(['<html></html>'], 'empty.html', {type: 'text/html'})
        ]});
        pending.push({data: {id: 'd'.repeat(64), company: 'MSFT', report_year: 2024,
            status: 'extraction_failed', chunk_count: 0}});
        await el('upload').onsubmit(event);
        assert.notEqual(el('upload-status').dataset.state, 'complete');
        const option = [...el('question-documents').options].find(item => item.value === 'd'.repeat(64));
        assert.ok(option.disabled);
        assert.equal(option.selected, false);
    });
    await check('unsupported file type is rejected before upload', () => {
        Object.defineProperty(el('upload-file'), 'files', {configurable: true, value: [
            new window.File(['data'], 'report.txt', {type: 'text/plain'})
        ]});
        el('upload-file').onchange();
        assert.equal(el('upload-file').validity.customError, true);
    });
    await check('resume status appears next to the saved result', async () => {
        reset();
        const failed = structuredClone(saved);
        failed.status = 'failed';
        window.show(failed);
        pending.push({data: saved});
        await el('reports').querySelector('button').onclick();
        assert.equal(el('result-message')?.dataset.state, 'complete');
    });
    await check('resume conflict remains visible after loading saved details', async () => {
        reset();
        const failed = structuredClone(saved);
        failed.status = 'failed';
        window.show(failed);
        pending.push({ok: false, status: 409, data: {detail: {task_id: saved.task_id}}}, {data: failed});
        await el('reports').querySelector('button').onclick();
        assert.equal(el('result-message').hidden, false);
        assert.equal(el('result-message').dataset.state, 'failed');
        assert.match(el('result-message').textContent, /Start a new analysis/);
        assert.equal(el('reports').querySelector('[data-resume]'), null);
    });
    await check('uploaded source choice explains its exclusive scope', () => {
        assert.match(el('source-hint').textContent, /only|exclusiv/i);
    });
    await check('favicon is provided without a missing-resource request', () => {
        assert.ok(window.document.querySelector('link[rel="icon"]')?.href.startsWith('data:'));
    });
    await check('upload metadata pattern and file hint describe accepted input', () => {
        assert.equal(el('upload-year').min, '2000');
        assert.match(el('file-hint').textContent, /PDF.*HTML.*32 MB/);
    });
    const result = {run_id: 'planner-frontend-quality', status: failures.length ? 'failed' : 'passed',
        checks, passed: checks.length - failures.length, total: checks.length, failed: failures,
        method: 'jsdom negative-path and API-contract checks'};
    const suffix = process.argv.includes('--baseline') ? '_baseline' : '';
    fs.writeFileSync('project_notes/run_logs/planner_frontend_quality' + suffix + '.json', JSON.stringify(result, null, 2) + '\n');
    console.log(JSON.stringify(result));
    window.close();
    if (failures.length) process.exitCode = 1;
}
main().catch(error => { console.error(error); window.close(); process.exitCode = 1; });
