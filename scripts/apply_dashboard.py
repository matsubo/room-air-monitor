#!/usr/bin/env python3
"""InfluxDB ダッシュボードテンプレートを Templates API で適用する。

依存なし（標準ライブラリのみ）。認証情報は secrets.yaml から読む（環境変数で上書き可）。
本適用の前に必ず --dry-run で差分を確認すること。

使い方:
    python3 scripts/apply_dashboard.py --dry-run          # 検証のみ（作成しない）
    python3 scripts/apply_dashboard.py                    # 本適用（実ダッシュボード作成）
    python3 scripts/apply_dashboard.py --stack-id <ID>    # 既存スタックを更新
    python3 scripts/apply_dashboard.py --template path.json

環境変数（secrets.yaml より優先）:
    INFLUX_DASHBOARD_TOKEN   ダッシュボード読み書き権限のトークン
"""
import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TEMPLATE = os.path.join(ROOT, "influx", "dashboard-office-env.json")


def load_secrets(path):
    """secrets.yaml から key: "value" 形式を素朴に抽出（dashboard.py と同方式）。"""
    secrets = {}
    if not os.path.exists(path):
        return secrets
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^([A-Za-z0-9_]+)\s*:\s*"?(.*?)"?\s*(?:#.*)?$', line)
            if m:
                secrets[m.group(1)] = m.group(2)
    return secrets


def extract_host_org(write_url):
    """influx_write_url から (host, org_id) を抽出。取れなければ ("","")。"""
    m_host = re.match(r"(https?://[^/]+)", write_url or "")
    m_org = re.search(r"[?&]org=([^&]+)", write_url or "")
    host = m_host.group(1) if m_host else ""
    org = m_org.group(1) if m_org else ""
    if not host or not org:
        return "", ""
    return host, org


def resolve_conn(secrets, env):
    """(base_url, token, org_id) を解決（環境変数 > secrets.yaml）。不足時は SystemExit。"""
    token = env.get("INFLUX_DASHBOARD_TOKEN") or secrets.get("influx_dashboard_token", "")
    token = token.replace("Token ", "").strip()

    host, org = extract_host_org(secrets.get("influx_write_url", ""))

    if not token:
        sys.exit("トークンが見つからない。INFLUX_DASHBOARD_TOKEN か secrets.yaml の influx_dashboard_token を設定して。")
    if not host or not org:
        sys.exit("host/org が解決できない。secrets.yaml の influx_write_url を確認して。")
    return host, token, org


def load_template(path):
    """テンプレ JSON を list として返す。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        sys.exit(f"テンプレートが見つからない: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"テンプレート JSON パース失敗: {path}: {e}")
    if not isinstance(data, list):
        sys.exit("テンプレートはリソースの配列である必要がある。")
    return data


def build_payload(org_id, contents, dry_run, stack_id):
    """Templates API のリクエストボディを構築。"""
    payload = {
        "dryRun": bool(dry_run),
        "orgID": org_id,
        "template": {"contents": contents},
    }
    if stack_id:
        payload["stackID"] = stack_id
    return payload


def apply_template(base_url, token, payload):
    """POST /api/v2/templates/apply を実行し、レスポンス dict を返す。"""
    url = f"{base_url}/api/v2/templates/apply"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        sys.exit(f"Templates API 失敗: HTTP {e.code}\n{e.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"InfluxDB 接続失敗: {e.reason}")


def summarize(result, dry_run):
    """dry-run/本適用の結果を人間可読で表示。"""
    summary = result.get("summary", {})
    dashboards = summary.get("dashboards", [])
    print(f"{'[dry-run] ' if dry_run else ''}ダッシュボード: {len(dashboards)} 件")
    for d in dashboards:
        name = d.get("name", "?")
        charts = d.get("charts", [])
        print(f"  - {name}（セル {len(charts)} 個）")
    if dry_run:
        diff = result.get("diff", {})
        diff_dash = diff.get("dashboards", [])
        print(f"[dry-run] 差分ダッシュボード: {len(diff_dash)} 件（実体は作成していない）")


def main():
    ap = argparse.ArgumentParser(description="InfluxDBダッシュボードテンプレートを適用")
    ap.add_argument("--template", default=DEFAULT_TEMPLATE, help="テンプレJSONパス")
    ap.add_argument("--dry-run", action="store_true", help="検証のみ（作成しない）")
    ap.add_argument("--stack-id", default=None, help="更新対象スタックID（任意）")
    args = ap.parse_args()

    secrets = load_secrets(os.path.join(ROOT, "secrets.yaml"))
    base_url, token, org = resolve_conn(secrets, os.environ)
    contents = load_template(args.template)
    payload = build_payload(org, contents, args.dry_run, args.stack_id)

    print(f"{'検証中' if args.dry_run else '適用中'}: org={org} template={os.path.basename(args.template)} ...")
    result = apply_template(base_url, token, payload)
    summarize(result, args.dry_run)

    if not args.dry_run:
        stack = result.get("stackID", "")
        print(f"適用完了。stackID={stack}")
        print(f"ダッシュボード確認: {base_url} にログインして Dashboards を開く。")
    else:
        print("dry-run 完了。本適用は --dry-run なしで実行する。")


if __name__ == "__main__":
    main()
