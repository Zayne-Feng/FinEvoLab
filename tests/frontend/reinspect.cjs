const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const drivers = ['check_dashboard.cjs', 'check_dashboard_quality.cjs', 'check_dashboard_language.cjs',
    'check_planner_web.cjs', 'check_dashboard_live.cjs', 'check_dashboard_language_live.cjs'];
const directory = path.join(root, 'output/verification_deps/closure_reinspection');
fs.mkdirSync(directory, {recursive:true});

if (process.argv[2] === '--worker') {
    const driver = process.argv[3];
    if (!drivers.includes(driver)) throw new Error('Unknown frontend driver');
    const write = fs.writeFileSync;
    fs.writeFileSync = function(filename, ...args) {
        const resolved = path.resolve(root, filename);
        if (resolved.startsWith(path.join(root, 'project_notes/run_logs') + path.sep))
            filename = path.join(directory, path.basename(filename));
        return write.call(this, filename, ...args);
    };
    require(path.join(__dirname, driver));
} else {
    const results = [];
    for (const driver of drivers) {
        const result = spawnSync(process.execPath, [__filename, '--worker', driver], {cwd:root, encoding:'utf8'});
        fs.writeFileSync(path.join(directory, driver + '.log'), result.stdout + result.stderr);
        if (result.status !== 0) throw new Error(driver + ' failed: ' + result.stderr);
        const lines = result.stdout.trim().split(/\r?\n/);
        const receipt = JSON.parse(lines.at(-1));
        if (receipt.status !== 'passed') throw new Error(driver + ' did not pass');
        results.push({driver, status:'passed', count:receipt.count || receipt.checks?.length || 0});
        console.log(JSON.stringify(results.at(-1)));
    }
    const record = {status:'passed', checks:results, model_requests:0, gpu_requests:0, fmp_requests:0,
        historical_receipts_preserved:true, finished_at:new Date().toISOString()};
    const receiptPath = process.env.FINEVOLAB_FRONTEND_RECEIPT || path.join(root, 'project_notes/run_logs/project_closure_frontend.json');
    fs.writeFileSync(receiptPath, JSON.stringify(record,null,2)+'\n');
}
