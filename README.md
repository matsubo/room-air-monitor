# office-env — オフィス環境モニタ

ESP32-C3 + ESPHome によるオフィス環境モニタ。CO2・温度・湿度・VOC・NOx・騒音を計測し、毎分 InfluxDB へ送信する。手元に現在値を表示する OLED 付き。3台同構成で運用する前提。

## 計測項目

| 項目 | センサ | 単位 |
|------|--------|------|
| CO2 | SCD41 | ppm |
| 温度 / 湿度 | SCD41 | ℃ / % |
| VOC Index / NOx Index | SGP41 | index |
| 騒音（Z特性RMS音圧, 1点校正済） | INMP441 | dB |
| WiFi RSSI | ESP32-C3 | dBm |

ハードウェア詳細（配線・ピンアサイン・BOM）は [`docs/hardware.md`](docs/hardware.md) を参照。

## 構成ファイル

| ファイル | 役割 |
|---------|------|
| `office-env-base.yaml` | 3台共通のベース設定（センサ・表示・送信ロジック） |
| `env-1.yaml` | 個体別ラッパー。`substitutions` 3行を変えて `env-2` / `env-3` を作る |
| `secrets.yaml` | WiFi / OTA / InfluxDB の秘匿値（**コミットしない**。`.gitignore` 済） |
| `secrets.yaml.example` | secrets のサンプル（実値なし） |

個体を増やす場合は `env-1.yaml` をコピーし、`device_name` / `friendly_name` / `room` の3つを変更するだけ。

## セットアップ

### 1. 前提

- Python + [ESPHome](https://esphome.io/)（最新版。`sound_level` コンポーネントを使うため）
- ESP32-C3 を USB-C で接続

```bash
pip install esphome        # もしくは pipx / docker
esphome version            # 動作確認
```

### 2. secrets を用意

```bash
cp secrets.yaml.example secrets.yaml
# secrets.yaml を編集して各値を埋める
```

### 3. ビルド & 書き込み

```bash
# USBシリアルポートを確認（macOSの例）
ls /dev/cu.usbmodem*

esphome run env-1.yaml --device /dev/cu.usbmodem2101
```

- 初回は USB 経由、以降は OTA（無線）で更新可能。
- 起動ログの I2C scan に `0x3C` `0x59` `0x62` が並べば配線OK。

### 4. 動作確認

WiFi 接続後、60秒ごとに InfluxDB へ書き込む。OLED 右上に `tx ok` が出れば送信成功。

## データの確認（InfluxDB）

InfluxDB Cloud (v2) の Data Explorer、または API で確認する。

```bash
curl -s 'https://<region>.aws.cloud2.influxdata.com/api/v2/query?org=<ORG_ID>' \
  -H "Authorization: Token <READ_TOKEN>" \
  -H 'Content-Type: application/vnd.flux' \
  -H 'Accept: application/csv' \
  --data 'from(bucket:"office_env") |> range(start:-15m)
          |> filter(fn:(r)=> r.device=="env-1") |> tail(n:20)'
```

- measurement: `env` / tags: `room`, `device` / fields: `co2` `temp` `rh` `voc` `nox` `laeq` `lamax` `rssi`
- 常設ダッシュボードは Grafana を推奨。

## 校正

- **騒音**: 設置場所でスマホ騒音計と1点校正する。`office-env-base.yaml` の `sound_level` → `offset` を調整。
  `新offset = 現offset + (基準計の値 − 現在の表示値)`
- **温度**: `temperature_offset`（既定4.0）を室温計と比較して調整。

詳細は [`docs/hardware.md`](docs/hardware.md) の「センサ校正メモ」参照。

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| `Probe Request Unsuccessful` で接続失敗を繰り返す | C3 SuperMini のブラウンアウト。`wifi` → `output_power: 8.5dB` を設定済み。まだ不安定なら 8.5→7dB 等さらに下げる |
| WiFi に繋がらない | SSID が 2.4GHz か確認（C3 は 5GHz 非対応） |
| 騒音値が高すぎ／平坦 | 未校正。上記「校正」を実施。中身は Z特性のため dBA より高く出る |
| マイクが無音 | INMP441 の L/R 結線の個体差。`channel: left` → `right` に変更 |
| OLED に何も出ない | I2C scan に `0x3C` が出るか確認。深夜(23–6時)は焼き付き対策で消灯する仕様 |
