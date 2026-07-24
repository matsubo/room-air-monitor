#!/usr/bin/env python3
"""各デバイスが直近にデータを送っているか InfluxDB で確認する死活監視。

一定時間データが来ていない（=停止/切断の疑い）デバイスを検出し、
標準出力に報告する。問題があれば終了コード1（cron/CIで拾える）。
Slack互換 webhook を渡すと通知も送る。

依存なし（標準ライブラリのみ）。認証は dashboard.py と同じ仕組み
（secrets.yaml の influx_read_token / 環境変数 INFLUX_TOKEN・INFLUX_QUERY_URL）。

使い方:
    python3 scripts/liveness_check.py
    python3 scripts/liveness_check.py --devices env-1,env-2,env-3 --max-age 5
    python3 scripts/liveness_check.py --webhook https://hooks.slack.com/services/XXX
    # cron 例（5分毎）: */5 * * * * python3 /path/scripts/liveness_check.py --webhook ...

環境変数:
    ALERT_WEBHOOK   --webhook 未指定時のフォールバック（Slack Incoming Webhook 等）
"""
import argparse
import csv
import io
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# dashboard.py の認証解決を再利用（DRY）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dashboard import load_secrets, resolve_conn  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def last_seen(query_url, token):
    """device ごとの最終データ時刻(UTC aware)を返す dict。"""
    flux = ('from(bucket:"office_env") |> range(start:-24h)\n'
            '  |> filter(fn:(r)=> r._field=="co2")\n'
            '  |> group(columns:["device"]) |> last()\n'
            '  |> keep(columns:["device","_time"])')
    req = urllib.request.Request(query_url, data=flux.encode("utf-8"), headers={
        "Authorization": f"Token {token}", "Content-Type": "application/vnd.flux",
        "Accept": "application/csv"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        sys.exit(f"InfluxDB クエリ失敗: HTTP {e.code}\n{e.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as e:
        sys.exit(f"InfluxDB 接続失敗: {e.reason}")

    seen = {}
    reader = csv.reader(io.StringIO(text))
    header = None
    for line in reader:
        if not line or len(line) < 4:
            continue
        if "_time" in line and "device" in line:
            header = line
            continue
        if header is None:
            continue
        rec = dict(zip(header, line))
        dev, t = rec.get("device", ""), rec.get("_time", "")
        if dev and t:
            iso = t.split(".")[0].replace("Z", "") + "+00:00"
            seen[dev] = datetime.fromisoformat(iso)
    return seen


def notify(webhook, text):
    req = urllib.request.Request(webhook, data=json.dumps({"text": text}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        print(f"webhook送信失敗: {e}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description="InfluxDBでデバイス死活監視")
    ap.add_argument("--devices", default="env-1,env-2,env-3", help="監視対象（カンマ区切り）")
    ap.add_argument("--max-age", type=float, default=5.0, help="許容する最終送信からの経過分（既定5分）")
    ap.add_argument("--webhook", default=os.environ.get("ALERT_WEBHOOK", ""), help="Slack互換Webhook URL")
    args = ap.parse_args()

    devices = [d.strip() for d in args.devices.split(",") if d.strip()]
    secrets = load_secrets(os.path.join(ROOT, "secrets.yaml"))
    query_url, token = resolve_conn(secrets)

    seen = last_seen(query_url, token)
    now = datetime.now(timezone.utc)

    problems = []
    for dev in devices:
        if dev not in seen:
            print(f"[MISSING] {dev}: 直近24hにデータなし")
            problems.append(f"{dev}: データなし(24h)")
            continue
        age_min = (now - seen[dev]).total_seconds() / 60
        if age_min > args.max_age:
            print(f"[STALE]   {dev}: 最終送信 {age_min:.1f}分前（許容{args.max_age:.0f}分）")
            problems.append(f"{dev}: {age_min:.0f}分前")
        else:
            print(f"[OK]      {dev}: 最終送信 {age_min:.1f}分前")

    if problems:
        if args.webhook:
            notify(args.webhook, "⚠️ office-env 死活監視: " + " / ".join(problems))
        sys.exit(1)
    print("全デバイス正常")


if __name__ == "__main__":
    main()
