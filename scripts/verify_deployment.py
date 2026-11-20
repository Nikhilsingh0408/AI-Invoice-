"""Exercise deployment durability in a disposable Compose project, never the live stack.

Requires Docker Compose v2, PyYAML, and the three locally built application images.
Synthetic audit/vector records test storage, not model or invoice accuracy.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--live", action="store_true", help="Use runtime AWS credentials for a synthetic German invoice and RAG query")
    parser.add_argument("--build", action="store_true", help="Build application images from current source using isolated tags")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    project = "group14-verify-" + uuid.uuid4().hex[:10]
    evidence = {"project": project, "scope": "Isolated deployment; synthetic persistence fixtures; no AWS calls", "checks": {}}
    with tempfile.TemporaryDirectory(prefix="group14-verify-") as folder:
        temporary = Path(folder)
        configuration = yaml.safe_load((root / "compose.yaml").read_text())
        configuration["name"] = project
        credentials = {"POSTGRES_USER": "verification", "POSTGRES_PASSWORD": "isolated-test-only",
                       "POSTGRES_DB": "invoice_auditor", "AWS_EC2_METADATA_DISABLED": "true"}
        for name, service in configuration["services"].items():
            service.pop("ports", None)
            build = service.pop("build", None)
            if args.build and build:
                service["build"] = str(root / build)
                service["image"] = f"{project}-{name}:verification"
            if "env_file" in service:
                service.pop("env_file")
                service["environment"] = {**credentials, **service.get("environment", {})}
        configuration["services"]["postgres"]["volumes"] = [
            "postgres_data:/var/lib/postgresql/data", f"{root / 'db/init'}:/docker-entrypoint-initdb.d:ro"]
        for name in ("data", "reports", "aws"):
            (temporary / name).mkdir()
        agent = configuration["services"]["agent"]
        agent["user"] = f"{os.getuid()}:{os.getgid()}"
        agent["volumes"] = [f"{temporary / 'data'}:/data", f"{temporary / 'reports'}:/reports",
                            f"{temporary / 'aws'}:/aws:ro", f"{root / 'config'}:/app/config:ro",
                            f"{root / 'agent/app'}:/app/app:ro", f"{root / 'tests'}:/app/tests:ro"]
        if args.live:
            agent["env_file"] = str(root / ".env")
            agent["volumes"][2] = f"{Path.home() / '.aws'}:/aws:ro"
            evidence["scope"] = "Isolated deployment plus live Bedrock processing of synthetic invoice"
        for service, source in (("mock-erp", "mock_erp"), ("streamlit-ui", "streamlit_ui")):
            configuration["services"][service]["volumes"] = [f"{root / source / 'app'}:/app/app:ro"]
        compose = temporary / "compose.yaml"
        compose.write_text(yaml.safe_dump(configuration, sort_keys=False))
        base = ["docker", "compose", "--project-name", project, "-f", str(compose)]

        def run(*command):
            result = subprocess.run([*base, *command], text=True, capture_output=True)
            if result.returncode:
                raise RuntimeError(result.stderr[-4000:] or result.stdout[-4000:])
            return result.stdout

        def python(code):
            return run("exec", "-T", "agent", "python", "-c", code).strip()

        def start():
            run("up", "-d", "--wait", "--wait-timeout", "180")

        try:
            print("Starting six isolated services", flush=True)
            if args.build:
                run("build")
            start()
            evidence["checks"]["startup"] = "PASS: six services ready; " + ("images built from current source" if args.build else "existing images with current source mounts")
            evidence["checks"]["dns"] = python(
                "import socket; [socket.getaddrinfo(host, None) for host in ('postgres','qdrant','redis','mock-erp')]; print('PASS')")
            evidence["checks"]["regressions"] = run(
                "exec", "-T", "-e", "RUN_DATABASE_TESTS=1", "agent", "python", "-m", "unittest", "discover", "-s", "tests", "-v")
            if args.live:
                print("Processing synthetic German invoice with live Bedrock", flush=True)
                live_output = run("exec", "-T", "agent", "python", "-m", "tests.live_invoice_check")
                evidence["checks"]["live_invoice"] = json.loads(live_output.strip().splitlines()[-1])
            python("from app import database; from app.rag_runtime import qdrant; "
                   "from qdrant_client.models import VectorParams, Distance, PointStruct; "
                   "database.open_pools(); "
                   "database.create_invoice('/data/verification.pdf','verification.pdf','pdf','durability-fixture','durability'); "
                   "qdrant.create_collection('verification_fixture', vectors_config=VectorParams(size=2,distance=Distance.COSINE)); "
                   "qdrant.upsert('verification_fixture',points=[PointStruct(id=1,vector=[1.0,0.0],payload={'fixture':True})],wait=True); "
                   "database.close_pools()")
            print("Recreating containers with volumes retained", flush=True)
            run("down")
            start()
            evidence["checks"]["persistence"] = python(
                "from app import database; from app.rag_runtime import qdrant; database.open_pools(); "
                "assert database.find_by_checksum('durability-fixture'); "
                "assert qdrant.count('verification_fixture').count == 1; database.close_pools(); print('PASS')")
            if args.live:
                invoice_id = str(uuid.UUID(evidence["checks"]["live_invoice"]["invoice_id"]))
                evidence["checks"]["live_persistence"] = python(
                    "import httpx; from app import database; from app.rag_runtime import qdrant; "
                    "database.open_pools(); "
                    f"row=database.get_invoice('{invoice_id}'); assert row['report_json'] and row['indexed_at']; "
                    "assert qdrant.count('invoice_chunks').count > 0; "
                    f"r=httpx.post('http://127.0.0.1:8000/query',json={{'question':'What is the total amount and currency on this invoice?','invoice_id':'{invoice_id}'}},timeout=180); "
                    "r.raise_for_status(); assert r.json()['accepted']; database.close_pools(); print('PASS')")
            print("Deleting only isolated verification volumes and recreating services", flush=True)
            run("down", "--volumes")
            start()
            evidence["checks"]["deliberate_loss"] = python(
                "from app import database; from app.rag_runtime import qdrant; database.open_pools(); "
                "assert database.fetch_one('SELECT count(*) AS n FROM audit.invoice_audit')['n']==0; "
                "assert not qdrant.get_collections().collections; database.close_pools(); print('PASS')")
        except Exception as error:
            evidence["error"] = str(error)
            raise
        finally:
            run("down", "--volumes", "--remove-orphans")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, indent=2) + "\n")
            print(f"Evidence: {args.output}", flush=True)


if __name__ == "__main__":
    main()
