const path = require('node:path');
process.chdir(path.resolve(__dirname, '../..'));
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {JSDOM} = require('jsdom');
const live = process.argv.includes('--live');
const origin = 'http://127.0.0.1:8003';
const checks = [];
const requests = [];
const errors = [];
const taskIds = [];

async function main() {
    const html = live ? await (await fetch(origin + '/finevolab')).text()
        : fs.readFileSync('finevolab/templates/dashboard.html', 'utf8');
    const fixture = JSON.parse(fs.readFileSync('tests/frontend/fixtures/task.json', 'utf8'));
    let reachable = true;
    const dom = new JSDOM(html, {url: origin + '/finevolab', runScripts: 'dangerously', beforeParse(window) {
        window.HTMLElement.prototype.scrollIntoView = function () {};
        window.AbortController = globalThis.AbortController;
        window.localStorage.setItem('finevolab.language', 'en');
        window.fetch = async (url, options) => {
            requests.push({url, options});
            if (live) return fetch(new URL(url, origin), options);
            let data = [];
            let status = 200;
            if (url === '/api/planners') data = {lora_bf16:{available:reachable},lora_nf4:{available:reachable}};
            if (url === '/api/questions') {
                const input = JSON.parse(options.body);
                data = structuredClone(fixture);
                data.versions = {planner_backend:input.planner_backend || 'api'};
                data.phases.analyze.result.planner = {backend:input.planner_backend || 'api'};
                if (!reachable) { status = 503; data = {detail:{error_type:'PlannerServiceError'}}; }
            }
            return {ok:status === 200,status,json:async()=>data};
        };
        window.addEventListener('error', event => errors.push(event.message));
        window.addEventListener('unhandledrejection', event => errors.push(String(event.reason)));
    }});
    try {
        await new Promise(resolve => setTimeout(resolve, 30));
        const el = id => dom.window.document.getElementById(id);
        assert.deepEqual(Array.from(el('planner-backend').options, o=>o.value), ['api','lora_bf16','lora_nf4']);
        checks.push('explicit API, BF16 and NF4 choices');
        for (const backend of ['lora_bf16','lora_nf4']) {
            el('planner-backend').value = backend;
            await el('planner-backend').onchange();
            assert.match(el('planner-hint').textContent, /connected/);
            el('question-text').value = 'Give MSFT FY2024 revenue.';
            delete el('question-text').dataset.example;
            await el('question-form').onsubmit({preventDefault(){}});
            assert.equal(el('question-status').dataset.state, 'complete', el('question-status').textContent);
            const state = JSON.parse(el('trace').textContent);
            assert.equal(state.versions.planner_backend, backend);
            assert.equal(state.phases.analyze.result.planner.backend, backend);
            assert.equal(el('planner-backend').disabled, false);
            if (live) {
                taskIds.push(state.task_id);
                assert.equal(state.phases.analyze.result.answer.status, 'complete');
                assert.ok(state.phases.analyze.result.planner.service_request_id);
                const pdf = await fetch(new URL(el('reports').querySelector('a[href$="/pdf"]').href));
                assert.equal(pdf.status, 200);
                assert.equal(Buffer.from(await pdf.arrayBuffer()).subarray(0,5).toString(), '%PDF-');
            }
            checks.push(backend + ' selection reaches the selected backend and restores controls');
            const before = requests.length;
            el('language-toggle').click();
            assert.equal(el('planner-backend').value, backend);
            el('language-toggle').click();
            assert.equal(requests.length, before);
        }
        if (!live) {
            reachable = false;
            await el('planner-backend').onchange();
            assert.match(el('planner-hint').textContent, /unavailable/);
            await el('question-form').onsubmit({preventDefault(){}});
            const submitted = requests.filter(r=>r.url === '/api/questions').at(-1);
            assert.equal(JSON.parse(submitted.options.body).planner_backend, 'lora_nf4');
            assert.equal(el('planner-backend').value, 'lora_nf4');
            assert.equal(el('question-submit').disabled, false);
            checks.push('unavailable service is visible without an API fallback');
        }
        assert.deepEqual(errors, []);
        const receipt = {status:'passed',mode:live?'live':'mock',checks,errors,task_ids:taskIds,
            tasks:live?requests.filter(r=>r.url === '/api/questions').length:0,
            finished_at:new Date().toISOString()};
        fs.writeFileSync('project_notes/run_logs/planner_web_' + (live?'live':'mock') + '.json',JSON.stringify(receipt,null,2)+'\n');
        console.log(JSON.stringify(receipt));
    } finally { dom.window.close(); }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
