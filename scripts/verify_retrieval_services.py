"""Exercise exact ranking, TTL, corruption, outages and service restart recovery."""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from finevolab.retrieval import ReportIndex,load_index
from finevolab.retrieval_cache import cached_passages,cache_client
from finevolab.retrieval_settings import RedisSettings,MilvusSettings,retrieval_observations
from finevolab.vector_backends import MilvusVectorBackend,milvus_client,corpus_identity,import_vectors
from finevolab.scope_evaluation import task_store
from benchmark_retrieval_services import configuration
from service_experiment_utils import write


def main(args):
    gold=json.loads((ROOT/"project_notes/evaluations/scope-evidence-v1/gold.json").read_text(encoding="utf-8"))
    documents=json.loads((ROOT/"data/evaluation/scope_v3/documents.json").read_text(encoding="utf-8"))
    configuration("A","service-verification")
    local=load_index(ROOT,mode="hybrid",profile="contextual")
    configuration("C","service-verification")
    remote=load_index(ROOT,mode="hybrid",profile="contextual")
    scopes={}
    for document in documents:
        store=task_store(ROOT,{"upload_ids":[document["id"]]},documents)
        scopes[(document["company"],document["report_year"])]=ReportIndex(store.uploads[0]["chunks"])
    checked=0
    for case in gold:
        key=(case["company"],case["report_year"])
        uploaded=key[0] in {"NVDA","TSLA","ADBE"}
        for mode in (["bm25"] if uploaded else ["bm25","hybrid"]):
            left=scopes[key] if uploaded else local
            right=scopes[key] if uploaded else remote
            first=left.search(case["question"],*key,mode=mode,limit=5,max_chars=10000)
            second=right.search(case["question"],*key,mode=mode,limit=5,max_chars=10000)
            if first!=second:raise ValueError("Vector migration changed a frozen result")
            checked+=1
    index_path=ROOT/"data/indexes/contextual/structured.json"
    version=corpus_identity(index_path)
    scope=[r for r in local.chunks if r["company"]=="MSFT" and r["report_year"]==2024]
    vector=local.encoder.encode(["revenue growth"],query=True)[0]
    first_import=import_vectors(index_path,MilvusSettings())
    second_import=import_vectors(index_path,MilvusSettings())
    assert first_import==second_import
    before=remote.vector_backend.rank(vector,scope)
    time.sleep(args.idle_seconds)
    assert remote.vector_backend.rank(vector,scope)==before
    subprocess.run([sys.executable,str(ROOT/"scripts/manage_retrieval_services.py"),"stop"],check=True)
    events=[]
    backend=MilvusVectorBackend(MilvusSettings(timeout_seconds=1),version)
    try:
        backend.rank(vector,scope)
    except Exception:
        outage_detected=True
    else:outage_detected=False
    fallback=MilvusVectorBackend(MilvusSettings(timeout_seconds=1,fallback=True),version)
    probe=ReportIndex(local.chunks,local.vectors,local.encoder,vector_backend=fallback)
    with retrieval_observations(events):
        fallback_result=probe.search("revenue growth","MSFT",2024,mode="hybrid")
    assert fallback_result==local.search("revenue growth","MSFT",2024,mode="hybrid")
    assert outage_detected and any(e.get("actual_backend")=="numpy" and e.get("status")=="fallback" for e in events)
    milvus_client.cache_clear()
    subprocess.run([sys.executable,str(ROOT/"scripts/manage_retrieval_services.py"),"start"],check=True)
    after=MilvusVectorBackend(MilvusSettings(),version).rank(vector,scope)
    assert before==after
    server=shutil.which("redis-server")
    if not server:raise ValueError("A Redis server executable is required for isolated recovery verification")
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1",0));port=candidate.getsockname()[1]
    output=ROOT/"output/retrieval_services"
    with tempfile.TemporaryDirectory(prefix="redis-verification-",dir=output) as temporary:
        directory=Path(temporary).resolve()
        assert directory.is_relative_to(output.resolve())
        config=directory/"redis.conf"
        config.write_text(f'bind 127.0.0.1\nport {port}\nsave ""\nappendonly no\n',encoding="ascii")
        settings=RedisSettings(url=f"redis://127.0.0.1:{port}/0",ttl_seconds=1,timeout_seconds=.2,prefix="finevolab:verification")
        def start_redis():
            log=(directory/"server.log").open("ab")
            process=subprocess.Popen([server,config.name],cwd=directory,stdout=log,stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
            log.close()
            for _ in range(30):
                try:
                    if cache_client(settings).ping():return process
                except Exception:time.sleep(.1)
            if process.poll() is None:process.terminate();process.wait()
            (output/"redis_verification_failure.log").write_bytes((directory/"server.log").read_bytes())
            raise RuntimeError("Isolated Redis server did not start")
        process=start_redis()
        try:
            calls=[];chunks=[{"id":"source","text":"Financial evidence."}]
            def compute():calls.append(1);return [{**chunks[0],"score":1.0}]
            expected=cached_passages(settings,"v",chunks,compute)
            assert cached_passages(settings,"v",chunks,compute)==expected and len(calls)==1
            time.sleep(1.1)
            cached_passages(settings,"v",chunks,compute)
            assert len(calls)==2
            cache_client(settings).set(settings.prefix+":v","corrupted",ex=5)
            assert cached_passages(settings,"v",chunks,compute)==expected and len(calls)==3
            process.terminate();process.wait(timeout=10)
            outage_events=[]
            with retrieval_observations(outage_events):
                assert cached_passages(settings,"v",chunks,compute)==expected
            assert any(e["status"]=="error" for e in outage_events)
            process=start_redis()
            assert cached_passages(settings,"v",chunks,compute)==expected
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=10)
            cache_client(settings).close()
    import psutil
    rss={}
    for row in psutil.net_connections(kind="tcp"):
        if row.status=="LISTEN" and row.laddr.port in {6379,19530} and row.pid:
            rss[str(row.laddr.port)]=psutil.Process(row.pid).memory_info().rss
    result={"status":"passed","frozen_query_configurations":checked,"exact_result_equivalence":True,
        "milvus_outage_detected":outage_detected,"milvus_fallback_recorded":True,"milvus_restart_results_equal":True,
        "redis_ttl_expiry":True,"redis_corruption_recomputed":True,"redis_outage_fallback":True,
        "redis_restart_recovered":True,"redis_server_version":cache_client(RedisSettings()).info("server")["redis_version"],
        "milvus_deployment":"Milvus Lite gRPC","server_rss_bytes":rss,"milvus_reimport_idempotent":True,"milvus_idle_reconnect_seconds":args.idle_seconds,"model_requests":0}
    write(args.output,result)
    print(json.dumps(result))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--idle-seconds",type=float,default=65)
    main(parser.parse_args())
