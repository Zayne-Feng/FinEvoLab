const path = require('node:path');
process.chdir(path.resolve(__dirname, '../..'));
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const origin = 'http://127.0.0.1:8003';
const checks = [];
const modelRuns = [];
const taskIds = [];
const domErrors = [];
const startedAt = new Date().toISOString();
let dom;

async function get(path) {
    const response = await fetch(origin + path, {signal: AbortSignal.timeout(90000)});
    assert.equal(response.status, 200, path);
    return response;
}
async function check(name, callback) {
    const result = await callback();
    checks.push({name, status: 'passed', ...(result || {})});
    console.log(JSON.stringify(checks.at(-1)));
}
async function main() {
    const seed = new FormData();
    seed.append('file', new Blob([fs.readFileSync('data/raw/sec_msft_2024.html')]), 'msft_2024.html');
    seed.append('company', 'MSFT');
    seed.append('fiscal_year', '2024');
    assert.equal((await fetch(origin + '/api/uploads', {method: 'POST', body: seed})).status, 200);
    const page = await get('/finevolab');
    dom = new JSDOM(await page.text(), {
        url: origin + '/finevolab', runScripts: 'dangerously',
        beforeParse(window) {
            window.HTMLElement.prototype.scrollIntoView = function () {};
            window.AbortController = globalThis.AbortController;
            window.AbortSignal = globalThis.AbortSignal;
            window.fetch = (url, options) => fetch(new URL(url, origin), options);
            window.addEventListener('error', event => domErrors.push(event.message));
            window.addEventListener('unhandledrejection', event => domErrors.push(String(event.reason)));
        }
    });
    const window = dom.window;
    const el = id => window.document.getElementById(id);
    const event = {preventDefault() {}};
    const uploads = await (await get('/api/uploads')).json();
    for (let attempt = 0; attempt < 40 && el('question-documents').options.length < uploads.length + 1; attempt++) {
        await new Promise(resolve => setTimeout(resolve, 50));
    }
    await check('live page, source list, navigation anchors and favicon', () => {
        assert.equal(el('question-documents').options.length, uploads.length + 1,
            el('source-hint').textContent + ' | Script errors: ' + JSON.stringify(domErrors));
        for (const anchor of window.document.querySelectorAll('nav a')) {
            assert.ok(el(anchor.hash.slice(1)));
        }
        assert.ok(window.document.querySelector('link[rel=icon]').href.startsWith('data:'));
        assert.equal(el('question-documents').options.length, uploads.length + 1);
        return {uploaded_reports: uploads.length};
    });
    for (const company of ['MSFT', 'AAPL', 'GOOGL', 'AMZN', 'META']) {
        for (const year of [2024, 2023]) {
            await check('offline analysis and reports · ' + company + ' FY' + year, async () => {
                el('company').value = company;
                el('year').value = String(year);
                el('mode').value = 'offline';
                await el('analysis').onsubmit(event);
                assert.equal(el('status').dataset.state, 'complete', el('status').textContent);
                assert.equal(el('result-status').dataset.state, 'complete');
                const state = JSON.parse(el('trace').textContent);
                taskIds.push(state.task_id);
                assert.equal(state.versions.company, company);
                assert.equal(state.versions.fiscal_year, year);
                assert.equal(el('answer').querySelectorAll('.figure').length, 1);
                assert.equal(el('answer').querySelectorAll('tbody tr').length, 2);
                assert.doesNotMatch(el('answer').textContent, /undefined|NaN/);
                const html = await get('/api/tasks/' + state.task_id + '/report/html');
                assert.ok((await html.text()).includes(company));
                const pdf = await get('/api/tasks/' + state.task_id + '/report/pdf');
                assert.equal(Buffer.from(await pdf.arrayBuffer()).subarray(0, 5).toString(), '%PDF-');
                const resumed = await fetch(origin + '/api/tasks/' + state.task_id + '/resume', {method: 'POST'});
                assert.equal(resumed.status, 200);
                assert.equal((await resumed.json()).status, 'complete');
                return {task_id: state.task_id};
            });
        }
    }
    const companies = [...el('search-company').options].map(option => option.value);
    for (const company of companies) {
        el('search-company').value = company;
        el('search-company').onchange();
        for (const option of [...el('search-year').options]) {
            const year = option.value;
            await check('live report search · ' + company + ' ' + year, async () => {
                el('search-year').value = year;
                el('query').value = 'revenue annual growth';
                await el('search').onsubmit(event);
                assert.equal(el('search-status').dataset.state, 'complete', el('search-status').textContent);
                assert.ok(el('search-results').querySelectorAll('.search-result').length);
                assert.equal(el('search-submit').disabled, false);
            });
        }
    }
    await check('existing report upload and extracted-values links', async () => {
        const metadata = JSON.parse(fs.readFileSync('data/uploads/' + uploads[0].id + '.json', 'utf8'));
        const raw = fs.readFileSync('data/uploads/' + metadata.path);
        const form = new FormData();
        form.append('file', new Blob([raw]), metadata.path);
        form.append('company', metadata.company);
        form.append('fiscal_year', String(metadata.report_year));
        const response = await fetch(origin + '/api/uploads', {method: 'POST', body: form, signal: AbortSignal.timeout(90000)});
        assert.equal(response.status, 200);
        const data = await response.json();
        assert.equal(data.id, metadata.id);
        assert.equal(data.status, 'text_extracted');
        assert.ok(data.chunk_count > 0);
        assert.ok((await get('/api/uploads/' + data.id + '/original')).ok);
        assert.ok((await get('/api/uploads/' + data.id + '/metrics')).ok);
        return {document_id: data.id, passages: data.chunk_count};
    });
    await check('invalid upload and task parameters return handled errors', async () => {
        const form = new FormData();
        form.append('file', new Blob(['Invalid format']), 'invalid.txt');
        form.append('company', 'MSFT');
        form.append('fiscal_year', '2024');
        assert.equal((await fetch(origin + '/api/uploads', {method: 'POST', body: form})).status, 422);
        assert.equal((await fetch(origin + '/api/tasks', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({company: '../config'})})).status, 422);
    });
    if (process.argv.includes('--models')) {
        await check('live financial question with model, figures and source links', async () => {
            el('question-text').value = 'What was Microsoft FY2024 annual revenue? Include its fiscal dates.';
            for (const option of el('question-documents').options) option.selected = false;
            await el('question-form').onsubmit(event);
            assert.equal(el('question-status').dataset.state, 'complete', el('question-status').textContent);
            const state = JSON.parse(el('trace').textContent);
            const analysis = state.phases.analyze.result;
            assert.equal(analysis.answer.status, 'complete');
            assert.ok(analysis.answer.metrics.length);
            assert.ok(el('evidence').children.length);
            assert.equal(el('reports').querySelectorAll('a').length, 2);
            modelRuns.push({task_id: state.task_id, mode: 'question', usage: analysis.usage});
            return {task_id: state.task_id, status: analysis.answer.status};
        });
        await check('live Agent-assisted revenue review', async () => {
            el('company').value = 'MSFT';
            el('year').value = '2024';
            el('mode').value = 'agent';
            await el('analysis').onsubmit(event);
            assert.equal(el('status').dataset.state, 'complete', el('status').textContent);
            const state = JSON.parse(el('trace').textContent);
            assert.ok(state.phases.analyze.result.answer.answer);
            assert.ok(el('evidence').children.length);
            modelRuns.push({task_id: state.task_id, mode: 'agent', usage: state.phases.analyze.result.usage});
            return {task_id: state.task_id, status: state.status};
        });
    }
    await check('no uncaught script errors', () => assert.deepEqual(domErrors, []));
    const result = {run_id: 'planner-frontend-http', status: 'passed', started_at: startedAt,
        finished_at: new Date().toISOString(), checks, count: checks.length, model_runs: modelRuns, task_ids: taskIds,
        method: 'Real local HTTP APIs driven through jsdom; not a rendered-browser inspection'};
    fs.writeFileSync('project_notes/run_logs/planner_frontend_http.json', JSON.stringify(result, null, 2) + '\n');
    dom.window.close();
    console.log(JSON.stringify({status: 'passed', count: checks.length, model_runs: modelRuns}));
}
main().catch(error => {
    console.error(error);
    fs.writeFileSync('project_notes/run_logs/planner_frontend_http_failed.json', JSON.stringify({
        status: 'failed', error: error.message, completed_checks: checks, model_runs: modelRuns, task_ids: taskIds
    }, null, 2) + '\n');
    dom?.window.close();
    process.exitCode = 1;
});
