#!/usr/bin/env python3
"""
Dashboard Workflows V2 — Pilotage + Visualisation de la stack ETL.
- Bandeau de contrôle : statut global, boutons d'action
- API /api/v2/status : état des couches (staging, DW, France Services)
- API /api/v2/trigger : déclenchement des traitements
- Conserve l'API /api/stats et /api/mapdata existantes

Utilisation : python3 serve_workflows.py  (port 8080)
"""

import http.server
import json
import subprocess
import os
import sys
from datetime import datetime

PG_CONTAINER = "etl_postgres"
PORT = 8080
PROJECT_DIR = "/root/agentic-etl-stack"


def query_pg(sql: str, timeout: int = 20) -> str | None:
    try:
        proc = subprocess.run(
            ["docker", "exec", PG_CONTAINER, "psql", "-U", "etl_admin", "-d", "analytics", "-t", "-A", "-c", sql],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return None


def get_all_stats():
    """Stats existantes (compatibles API v1)."""
    s = {}
    
    # SIRENE
    sul = query_pg("SELECT COUNT(*) FROM etl.stock_unite_legale;")
    sul_actives = query_pg("SELECT COUNT(*) FROM etl.stock_unite_legale WHERE etatadministratifunitelegale = 'A';")
    s["sirene"] = {"total": sul or "0", "actives": sul_actives or "0"}

    # France Services
    fs = query_pg("SELECT COUNT(*) FROM etl.france_services;")
    s["france_services"] = {"total": fs or "0"}

    # Annuaire Santé
    an = query_pg("SELECT COUNT(*) FROM etl.annuaire_personne_activite;")
    s["annuaire_sante"] = {"total": an or "0"}

    # Établissements
    etab = query_pg("SELECT COUNT(*) FROM etl.stock_etablissement;")
    s["etablissements"] = {"total": etab or "0"}

    return s


def get_v2_status():
    """Statut V2 : état des couches staging/DW/France Services enrichi."""
    status = {
        "timestamp": datetime.now().isoformat(),
        "layers": {},
        "global": "ok",
    }

    # Staging
    staging_tables = query_pg(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'etl_staging';"
    )
    status["layers"]["staging"] = {
        "tables": int(staging_tables or 0),
        "status": "ok" if staging_tables and int(staging_tables) > 0 else "empty",
    }

    # Datawarehouse
    dw_tables = query_pg(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'etl_dw';"
    )
    status["layers"]["dw"] = {
        "tables": int(dw_tables or 0),
        "status": "ok" if dw_tables and int(dw_tables) > 0 else "empty",
    }

    # France Services enrichi
    fse_count = query_pg("SELECT COUNT(*) FROM etl_dw.france_services_enriched;")
    status["layers"]["france_services_enriched"] = {
        "rows": int(fse_count or 0),
        "status": "ok" if fse_count and int(fse_count) > 0 else "empty",
    }

    # État global
    if not dw_tables or int(dw_tables) == 0:
        status["global"] = "degraded"

    return status


def get_logs(limit: int = 10):
    """Dernières actions agent (logs)."""
    result = query_pg(f"""
        SELECT agent_id, action_type, status, workflow, 
               rows_affect, duration_ms, created_at
        FROM etl.agent_action_log
        ORDER BY created_at DESC
        LIMIT {limit};
    """)
    logs = []
    if result:
        for line in result.split("\n"):
            parts = line.split("|")
            if len(parts) >= 3:
                logs.append({
                    "agent": parts[0].strip() if len(parts) > 0 else "",
                    "action": parts[1].strip() if len(parts) > 1 else "",
                    "status": parts[2].strip() if len(parts) > 2 else "",
                })
    return logs


def trigger_pipeline(stage: str) -> dict:
    """Déclenche un script ETL et retourne le résultat."""
    scripts = {
        "staging": f"cd {PROJECT_DIR} && python3 scripts/staging/load_sante_staging.py",
        "dw": f"cd {PROJECT_DIR} && python3 scripts/transform/build_dw.py",
    }

    if stage not in scripts:
        return {"error": f"Étage inconnu: {stage}. Options: staging, dw"}

    try:
        proc = subprocess.run(
            scripts[stage],
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=PROJECT_DIR,
        )
        return {
            "stage": stage,
            "success": proc.returncode == 0,
            "output": proc.stdout[-2000:] if proc.stdout else "",
            "error": proc.stderr[-500:] if proc.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"stage": stage, "success": False, "error": "Timeout (5 min)"}
    except Exception as e:
        return {"stage": stage, "success": False, "error": str(e)}


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # Routes API
        if self.path == "/api/stats":
            return self.json_response(get_all_stats())

        if self.path == "/api/v2/status":
            return self.json_response(get_v2_status())

        if self.path == "/api/v2/logs":
            return self.json_response(get_logs())

        if self.path.startswith("/api/mapdata/"):
            layer = self.path.split("/")[-1]
            return self.json_response(get_mapdata(layer))

        # Pages HTML
        if self.path == "/" or self.path == "/index.html":
            self.path = "/workflows_v2.html"
            return http.server.SimpleHTTPRequestHandler.do_GET(self)

        if self.path == "/cartes":
            self.path = "/cartes.html"
            return http.server.SimpleHTTPRequestHandler.do_GET(self)

        return http.server.SimpleHTTPRequestHandler.do_GET(self)

    def do_POST(self):
        if self.path == "/api/v2/trigger":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length else b"{}"
            try:
                data = json.loads(body)
            except json.JSONDecodeError:
                data = {}
            stage = data.get("stage", "")
            result = trigger_pipeline(stage)
            return self.json_response(result, status=200 if result.get("success") else 500)

        self.send_response(404)
        self.end_headers()

    def json_response(self, data, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        pass


def get_mapdata(layer: str):
    """Données par département pour les cartes Leaflet."""
    queries = {
        "france_services": """
            SELECT LPAD(SPLIT_PART(code_departement, '.', 1), 2, '0') AS dept, COUNT(*) AS val
            FROM etl.france_services
            WHERE code_departement IS NOT NULL AND LENGTH(code_departement) > 0
            GROUP BY code_departement
        """,
        "annuaire_sante": """
            SELECT LPAD(LEFT(code_postal, 2), 2, '0') AS dept, COUNT(*) AS val
            FROM etl.annuaire_personne_activite
            WHERE code_postal IS NOT NULL AND LENGTH(code_postal) > 0
            GROUP BY LEFT(code_postal, 2)
        """,
        "fse": """
            SELECT LPAD(REGEXP_REPLACE(code_departement, '\\.[0-9]+$', ''), 2, '0') AS dept, COUNT(*) AS val
            FROM etl_dw.france_services_enriched
            WHERE code_departement IS NOT NULL AND LENGTH(code_departement) > 0
              AND code_departement != ' '
            GROUP BY REGEXP_REPLACE(code_departement, '\\.[0-9]+$', '')
        """,
        "sirene": """
            SELECT LEFT(siren, 2) AS dept, COUNT(*) AS val
            FROM etl.stock_unite_legale
            WHERE siren IS NOT NULL AND etatadministratifunitelegale = 'A'
              AND LENGTH(siren) >= 2
            GROUP BY LEFT(siren, 2)
        """,
    }
    sql = queries.get(layer, queries["france_services"])
    rows = []
    result = query_pg(sql)
    if result:
        for line in result.split("\n"):
            parts = line.split("|")
            if len(parts) >= 2 and parts[0].strip():
                rows.append({"dept": parts[0].strip(), "val": int(parts[1].strip())})
    return {"layer": layer, "data": rows}


if __name__ == "__main__":
    os.chdir("/root/projects/sirene/viz")
    server = http.server.HTTPServer(("127.0.0.1", PORT), DashboardHandler)
    print(f"Workflows V2 — http://127.0.0.1:{PORT}")
    server.serve_forever()
