#!/usr/bin/env python3
"""Serveur dashboard workflows + API GeoJSON pour cartes Leaflet."""
import http.server
import json
import subprocess
import os

PG_CONTAINER = "etl_postgres"
PORT = 8080

def query_pg(sql: str):
    try:
        proc = subprocess.run(
            ["docker", "exec", PG_CONTAINER, "psql", "-U", "etl_admin", "-d", "analytics", "-t", "-A", "-c", sql],
            capture_output=True, text=True, timeout=20
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return None

def get_all_stats():
    s = {}
    sul = query_pg("SELECT COUNT(*) FROM etl.stock_unite_legale;")
    sul_actives = query_pg("SELECT COUNT(*) FROM etl.stock_unite_legale WHERE etatadministratifunitelegale = 'A';")
    sul_cessees = query_pg("SELECT COUNT(*) FROM etl.stock_unite_legale WHERE etatadministratifunitelegale = 'C';")
    sul_size = query_pg("SELECT pg_size_pretty(pg_total_relation_size('etl.stock_unite_legale'));")
    sul_cat = query_pg("SELECT categoriejuridiqueunitelegale, COUNT(*) AS nb FROM etl.stock_unite_legale GROUP BY 1 ORDER BY nb DESC LIMIT 1;")
    s["sirene"] = {"total": sul or "0", "actives": sul_actives or "0", "cessees": sul_cessees or "0", "size": sul_size or "—", "top_cat": sul_cat.split("|")[1] if sul_cat and "|" in sul_cat else "—"}

    fs = query_pg("SELECT COUNT(*) FROM etl.france_services;")
    fs_depts = query_pg("SELECT COUNT(DISTINCT LEFT(code_postal, 2)) FROM etl.france_services WHERE code_postal IS NOT NULL AND LENGTH(code_postal) > 0;")
    fs_ville = query_pg("SELECT ville, COUNT(*) AS nb FROM etl.france_services WHERE ville IS NOT NULL AND LENGTH(ville) > 0 GROUP BY ville ORDER BY nb DESC LIMIT 1;")
    fs_email = query_pg("SELECT COUNT(*) FROM etl.france_services WHERE email IS NOT NULL AND LENGTH(email) > 0;")
    fs_tel = query_pg("SELECT COUNT(*) FROM etl.france_services WHERE numero_telephone IS NOT NULL AND LENGTH(numero_telephone) > 0;")
    s["france_services"] = {"total": fs or "0", "depts": fs_depts or "0", "top_ville": (fs_ville.split("|")[0] if fs_ville and "|" in fs_ville else "—"), "top_ville_nb": (fs_ville.split("|")[1] if fs_ville and "|" in fs_ville else "0"), "email": fs_email or "0", "tel": fs_tel or "0"}

    an = query_pg("SELECT COUNT(*) FROM etl.annuaire_personne_activite;")
    an_tel = query_pg("SELECT COUNT(*) FROM etl.annuaire_personne_activite WHERE telephone IS NOT NULL AND LENGTH(telephone) > 0;")
    an_email = query_pg("SELECT COUNT(*) FROM etl.annuaire_personne_activite WHERE email IS NOT NULL AND LENGTH(email) > 0;")
    an_siret = query_pg("SELECT COUNT(*) FROM etl.annuaire_personne_activite WHERE siret_site IS NOT NULL AND LENGTH(siret_site) > 0;")
    an_commune = query_pg("SELECT libelle_commune, COUNT(*) AS nb FROM etl.annuaire_personne_activite WHERE libelle_commune IS NOT NULL AND LENGTH(libelle_commune) > 0 GROUP BY libelle_commune ORDER BY nb DESC LIMIT 1;")
    an_pro = query_pg("SELECT libelle_profession, COUNT(*) AS nb FROM etl.annuaire_personne_activite WHERE libelle_profession IS NOT NULL AND LENGTH(libelle_profession) > 0 GROUP BY libelle_profession ORDER BY nb DESC LIMIT 1;")
    an_depts = query_pg("SELECT COUNT(DISTINCT LEFT(code_postal, 2)) FROM etl.annuaire_personne_activite WHERE code_postal IS NOT NULL AND LENGTH(code_postal) > 0;")
    an_tables = query_pg("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'etl' AND table_name LIKE 'annuaire_%';")
    s["annuaire_sante"] = {"total": an or "0", "depts": an_depts or "0", "tel": an_tel or "0", "email": an_email or "0", "siret": an_siret or "0", "top_commune": (an_commune.split("|")[0] if an_commune and "|" in an_commune else "—"), "top_commune_nb": (an_commune.split("|")[1] if an_commune and "|" in an_commune else "0"), "top_pro": (an_pro.split("|")[0] if an_pro and "|" in an_pro else "—"), "top_pro_nb": (an_pro.split("|")[1] if an_pro and "|" in an_pro else "0"), "nb_tables": an_tables or "0"}

    cross_fs_sirene = query_pg("SELECT COUNT(*) FROM etl.stock_unite_legale s INNER JOIN etl.france_services f ON s.siren = LEFT(f.siret, 9);")
    cross_an_sirene = query_pg("SELECT COUNT(*) FROM etl.stock_unite_legale s INNER JOIN etl.annuaire_personne_activite a ON s.siren = a.siren_site;")
    depts_communs = query_pg("SELECT COUNT(*) FROM (SELECT DISTINCT LEFT(code_postal, 2) AS dep FROM etl.france_services WHERE code_postal IS NOT NULL AND LENGTH(code_postal) > 0 INTERSECT SELECT DISTINCT LEFT(code_postal, 2) AS dep FROM etl.annuaire_personne_activite WHERE code_postal IS NOT NULL AND LENGTH(code_postal) > 0) t;")
    s["croisements"] = {"fs_sirene": cross_fs_sirene or "0", "an_sirene": cross_an_sirene or "0", "depts_communs_fs_an": depts_communs or "0"}

    # Adresses BAN
    adr_total = query_pg("SELECT COUNT(*) FROM etl.adresses_france;")
    adr_size = query_pg("SELECT pg_size_pretty(pg_total_relation_size('etl.adresses_france'));")
    adr_depts = query_pg("SELECT COUNT(DISTINCT LEFT(code_postal, 2)) FROM etl.adresses_france WHERE code_postal IS NOT NULL AND LENGTH(code_postal) > 0;")
    adr_communes = query_pg("SELECT COUNT(DISTINCT code_insee) FROM etl.adresses_france WHERE code_insee IS NOT NULL AND LENGTH(code_insee) > 0;")
    s["adresses_france"] = {"total": adr_total or "0", "size": adr_size or "—", "depts": adr_depts or "0", "communes": adr_communes or "0"}

    # Nomenclatures INSEE (référentiel géo officiel)
    s["insee"] = {
        "communes": query_pg("SELECT COUNT(*) FROM etl.insee_communes;") or "0",
        "departements": query_pg("SELECT COUNT(*) FROM etl.insee_departements;") or "0",
        "regions": query_pg("SELECT COUNT(*) FROM etl.insee_regions;") or "0",
    }

    # Couche 1.2 - Établissements (StockEtablissement)
    etab = query_pg("SELECT COUNT(*) FROM etl.stock_etablissement;")
    etab_actifs = query_pg("SELECT COUNT(*) FROM etl.stock_etablissement WHERE etatAdministratifEtablissement = 'A';")
    etab_fermes = query_pg("SELECT COUNT(*) FROM etl.stock_etablissement WHERE etatAdministratifEtablissement = 'F';")
    etab_size = query_pg("SELECT pg_size_pretty(pg_total_relation_size('etl.stock_etablissement'));")
    etab_sieges = query_pg("SELECT COUNT(*) FROM etl.stock_etablissement WHERE etablissementSiege = 'true';")
    s["etablissements"] = {"total": etab or "0", "actifs": etab_actifs or "0", "fermes": etab_fermes or "0", "size": etab_size or "—", "sieges": etab_sieges or "0"}
    return s

def get_mapdata(layer):
    """Retourne des données par département pour les cartes."""
    queries = {
        "france_services": "SELECT LPAD(SPLIT_PART(code_departement, '.', 1), 2, '0') AS dept, COUNT(*) AS val FROM etl.france_services WHERE code_departement IS NOT NULL AND LENGTH(code_departement) > 0 GROUP BY code_departement",
        "annuaire_sante": "SELECT LPAD(LEFT(code_postal, 2), 2, '0') AS dept, COUNT(*) AS val FROM etl.annuaire_personne_activite WHERE code_postal IS NOT NULL AND LENGTH(code_postal) > 0 GROUP BY LEFT(code_postal, 2)",
        "open_data": "SELECT LPAD(LEFT(code_postal, 2), 2, '0') AS dept, COUNT(*) AS val FROM etl.ps_infospratiques WHERE code_postal IS NOT NULL AND LENGTH(code_postal) > 0 GROUP BY LEFT(code_postal, 2)",
        "adresses": "SELECT LPAD(LEFT(code_postal, 2), 2, '0') AS dept, COUNT(*) AS val FROM etl.adresses_france WHERE code_postal IS NOT NULL AND LENGTH(code_postal) > 0 GROUP BY LEFT(code_postal, 2)",
        "sirene": "SELECT LPAD(SPLIT_PART(f.code_departement, '.', 1), 2, '0') AS dept, COUNT(DISTINCT s.siren) AS val FROM etl.stock_unite_legale s INNER JOIN etl.france_services f ON s.siren = LEFT(f.siret, 9) WHERE f.code_departement IS NOT NULL GROUP BY f.code_departement"
    }
    sql = queries.get(layer, queries["france_services"])
    rows = []
    result = query_pg(sql)
    if result:
        for line in result.split('\n'):
            parts = line.split('|')
            if len(parts) >= 2 and parts[0].strip():
                rows.append({"dept": parts[0].strip(), "val": int(parts[1].strip())})
    return {"layer": layer, "data": rows}


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.path = "/workflows_visualization.html"
            return http.server.SimpleHTTPRequestHandler.do_GET(self)

        if self.path == "/cartes" or self.path == "/cartes.html":
            self.path = "/cartes.html"
            return http.server.SimpleHTTPRequestHandler.do_GET(self)

        if self.path == "/api/stats":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(json.dumps(get_all_stats()).encode())
            return

        if self.path.startswith("/api/mapdata/"):
            layer = self.path.split("/")[-1]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "max-age=300")
            self.end_headers()
            self.wfile.write(json.dumps(get_mapdata(layer)).encode())
            return

        return http.server.SimpleHTTPRequestHandler.do_GET(self)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    os.chdir("/root/projects/sirene/viz")
    server = http.server.HTTPServer(("0.0.0.0", PORT), DashboardHandler)
    print(f"Serving workflows dashboard on http://0.0.0.0:{PORT}")
    server.serve_forever()
