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
import urllib.parse
import webbrowser
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JST = timezone(timedelta(hours=9))

# フィールド定義（単一の出所）: (キー, 整数として丸めるか)
# office-env-base.yaml が送る9フィールド全てを網羅すること（tests/test_dashboard_template.py で検証）
FIELDS = [("co2", True), ("temp", False), ("rh", True),
          ("voc", True), ("nox", True), ("laeq", False), ("lamax", False),
          ("rssi", True), ("mcu_temp", False)]
METRIC_KEYS = [k for k, _ in FIELDS]


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
    # 読み取り用トークンを優先（無ければ device write トークンにフォールバック）
    token = (os.environ.get("INFLUX_TOKEN")
             or secrets.get("influx_read_token")
             or secrets.get("influx_auth_header", "")).replace("Token ", "").strip()
    query_url = os.environ.get("INFLUX_QUERY_URL")

    if not query_url:
        write_url = secrets.get("influx_write_url", "")
        if write_url:
            # write URL の host と org から query URL を組み立てる
            u = urllib.parse.urlsplit(write_url)
            org = urllib.parse.parse_qs(u.query).get("org", [""])[0]
            if u.netloc and org:
                query_url = f"{u.scheme}://{u.netloc}/api/v2/query?org={org}"

    if not token or not query_url:
        sys.exit("認証情報が見つからない。secrets.yaml を用意するか INFLUX_TOKEN / INFLUX_QUERY_URL を設定して。")
    return query_url, token


def fetch(query_url, token, device, rng):
    flux = (
        f'from(bucket:"office_env") |> range(start:-{rng})\n'
        f'  |> filter(fn:(r)=> r.device=="{device}")\n'
        f'  |> filter(fn:(r)=> ' + " or ".join(f'r._field=="{k}"' for k in METRIC_KEYS) + ')\n'
        f'  |> pivot(rowKey:["_time","room"], columnKey:["_field"], valueColumn:"_value")\n'
        f'  |> keep(columns:["_time","room",' + ",".join(f'"{k}"' for k in METRIC_KEYS) + '])\n'
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
    """CSV を行データに変換し (rows, room) を返す。"""
    rows, room = [], ""
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
        jst = datetime.fromisoformat(iso).astimezone(JST)
        room = rec.get("room", "") or room

        row = {"t": jst.strftime("%H:%M")}
        for k, intish in FIELDS:
            v = rec.get(k, "").strip()
            row[k] = (int(round(float(v))) if intish else round(float(v), 2)) if v else None
        rows.append(row)
    return rows, room


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
            .replace("__RANGE__", rng)
            .replace("__DEVLABEL__", device)
            .replace("__ROOMLABEL__", f"room: {room}")
            .replace("__META_LINE__", f"InfluxDB office_env · {device}")
            .replace("__FOOTER__", footer))
    return html


def main():
    ap = argparse.ArgumentParser(description="InfluxDBから環境モニタHTMLダッシュボードを生成")
    ap.add_argument("--device", default="env-1", help="対象デバイス（既定 env-1）")
    ap.add_argument("--range", default="6h", help="取得期間 例 3h/24h/7d（既定 6h）")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "dashboard.html"), help="出力先HTML")
    ap.add_argument("--no-open", action="store_true", help="ブラウザで開かない")
    args = ap.parse_args()

    secrets = load_secrets(os.path.join(ROOT, "config", "secrets.yaml"))
    query_url, token = resolve_conn(secrets)

    print(f"取得中: device={args.device} range=-{args.range} ...")
    csv_text = fetch(query_url, token, args.device, args.range)
    rows, room = parse_csv(csv_text)
    if not rows:
        sys.exit(f"データなし（device={args.device}, range=-{args.range}）。デバイス名か期間を確認して。")

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
