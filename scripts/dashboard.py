#!/usr/bin/env python3
"""InfluxDB から最新データを取得し、ローカルで環境モニタHTMLダッシュボードを生成・プレビューする。

依存なし（標準ライブラリのみ）。認証情報は secrets.yaml から読む（環境変数で上書き可）。

使い方:
    python3 scripts/dashboard.py                 # env-1, 直近6h, 生成してブラウザで開く
    python3 scripts/dashboard.py --device env-2  # 別の個体
    python3 scripts/dashboard.py --range 24h     # 期間指定
    python3 scripts/dashboard.py --no-open        # 開かずに生成のみ
    python3 scripts/dashboard.py --out /tmp/x.html

環境変数（secrets.yaml より優先）:
    INFLUX_QUERY_URL  例: https://us-east-1-1.aws.cloud2.influxdata.com/api/v2/query?org=<ORG_ID>
    INFLUX_TOKEN      例: FFAHc...==  （"Token " プレフィックスは付けても付けなくてもよい）
"""
import argparse
import csv
import io
import json
import os
import re
import sys
import urllib.request
import urllib.error
import webbrowser
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JST = timezone(timedelta(hours=9))

METRIC_KEYS = ["co2", "temp", "rh", "voc", "laeq", "rssi"]


def load_secrets(path):
    """secrets.yaml から必要キーを素朴に抽出（YAMLパーサ不要の単純 key: "value" 形式前提）。"""
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


def resolve_conn(secrets):
    """クエリURLとトークンを解決（環境変数 > secrets.yaml）。"""
    token = os.environ.get("INFLUX_TOKEN")
    query_url = os.environ.get("INFLUX_QUERY_URL")

    if not token:
        auth = secrets.get("influx_auth_header", "")
        token = auth.replace("Token ", "").strip()
    token = token.replace("Token ", "").strip()

    if not query_url:
        write_url = secrets.get("influx_write_url", "")
        # write?...&org=ID... から query?org=ID を組み立てる
        m_host = re.match(r"(https?://[^/]+)/api/v2/write", write_url)
        m_org = re.search(r"[?&]org=([^&]+)", write_url)
        if m_host and m_org:
            query_url = f"{m_host.group(1)}/api/v2/query?org={m_org.group(1)}"

    if not token or not query_url:
        sys.exit("認証情報が見つからない。secrets.yaml を用意するか INFLUX_TOKEN / INFLUX_QUERY_URL を設定して。")
    return query_url, token


def fetch(query_url, token, device, rng):
    flux = (
        f'from(bucket:"office_env") |> range(start:-{rng})\n'
        f'  |> filter(fn:(r)=> r.device=="{device}")\n'
        f'  |> filter(fn:(r)=> ' + " or ".join(f'r._field=="{k}"' for k in METRIC_KEYS) + ')\n'
        f'  |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")\n'
        f'  |> keep(columns:["_time",' + ",".join(f'"{k}"' for k in METRIC_KEYS) + '])\n'
        f'  |> sort(columns:["_time"])'
    )
    req = urllib.request.Request(
        query_url,
        data=flux.encode("utf-8"),
        headers={
            "Authorization": f"Token {token}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        sys.exit(f"InfluxDB クエリ失敗: HTTP {e.code}\n{e.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"InfluxDB 接続失敗: {e.reason}")


def parse_csv(text):
    rows = []
    reader = csv.reader(io.StringIO(text))
    header = None
    for line in reader:
        if not line or len(line) < 4:
            continue
        if line[3] == "_time":
            header = line
            continue
        if header is None:
            continue
        rec = dict(zip(header, line))
        t = rec.get("_time", "")
        if not t:
            continue
        # 例: 2026-07-24T03:38:24.777Z → 小数秒を除去しつつ UTC のまま aware 化
        iso = re.sub(r"\.\d+", "", t).replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        jst = dt.astimezone(JST)

        def num(k, intish):
            v = rec.get(k, "").strip()
            if v == "":
                return None
            f = float(v)
            return int(round(f)) if intish else round(f, 2)

        rows.append({
            "t": jst.strftime("%H:%M"),
            "co2": num("co2", True),
            "temp": num("temp", False),
            "rh": num("rh", True),
            "voc": num("voc", True),
            "laeq": num("laeq", False),
            "rssi": num("rssi", True),
        })
    return rows


def build_html(rows, device, room, rng):
    tpl_path = os.path.join(ROOT, "scripts", "dashboard_template.html")
    tpl = open(tpl_path, encoding="utf-8").read()
    generated = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")
    span = f"{rows[0]['t']}〜{rows[-1]['t']}" if rows else "データなし"
    footer = (
        f"データソース: InfluxDB <code>office_env</code> / device=<code>{device}</code>。"
        f"取得範囲 直近{rng}（{span} JST・{len(rows)}点）。生成 {generated} JST。"
        "CO2ステータス閾値: 良好&lt;800 / 注意800–1500 / 高≥1500 ppm。騒音はZ特性・1点校正済の参考値。"
        "<br>再生成: <code>python3 scripts/dashboard.py</code>"
    )
    head = ('<!doctype html><html lang="ja"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"></head><body>\n')
    tpl = head + tpl + "\n</body></html>\n"
    html = (tpl
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
            .replace("__CALIB__", "null")
            .replace("__DEVLABEL__", f'<span class="chip"><span class="dot pulse" style="background:var(--good)"></span>{device}</span>')
            .replace("__ROOMLABEL__", f"room: {room}")
            .replace("__META_LINE__", f"InfluxDB office_env · {device}")
            .replace("__FOOTER__", footer))
    return html


def guess_room(rows_device, query_url, token, device, rng):
    """room タグを1点だけ引く（表示用）。取れなければ空。"""
    flux = (f'from(bucket:"office_env") |> range(start:-{rng}) '
            f'|> filter(fn:(r)=> r.device=="{device}") |> last() '
            f'|> keep(columns:["room"]) |> limit(n:1)')
    req = urllib.request.Request(query_url, data=flux.encode(), headers={
        "Authorization": f"Token {token}", "Content-Type": "application/vnd.flux",
        "Accept": "application/csv"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            for line in csv.reader(io.StringIO(resp.read().decode())):
                if line and line[-1] and line[-1] not in ("room", "_result"):
                    return line[-1]
    except Exception:
        pass
    return ""


def main():
    ap = argparse.ArgumentParser(description="InfluxDBから環境モニタHTMLダッシュボードを生成")
    ap.add_argument("--device", default="env-1", help="対象デバイス（既定 env-1）")
    ap.add_argument("--range", default="6h", help="取得期間 例 3h/24h/7d（既定 6h）")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "dashboard.html"), help="出力先HTML")
    ap.add_argument("--no-open", action="store_true", help="ブラウザで開かない")
    args = ap.parse_args()

    secrets = load_secrets(os.path.join(ROOT, "secrets.yaml"))
    query_url, token = resolve_conn(secrets)

    print(f"取得中: device={args.device} range=-{args.range} ...")
    csv_text = fetch(query_url, token, args.device, args.range)
    rows = parse_csv(csv_text)
    if not rows:
        sys.exit(f"データなし（device={args.device}, range=-{args.range}）。デバイス名か期間を確認して。")
    room = guess_room(rows, query_url, token, args.device, args.range)

    html = build_html(rows, args.device, room or "?", args.range)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"生成: {args.out}（{len(rows)}点, {rows[0]['t']}〜{rows[-1]['t']} JST）")

    if not args.no_open:
        webbrowser.open("file://" + os.path.abspath(args.out))
        print("ブラウザで開いた。")


if __name__ == "__main__":
    main()
