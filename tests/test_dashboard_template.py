import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(ROOT, "influx", "dashboard-office-env.json")

# デバイスが送信する全フィールド（office-env-base.yaml の InfluxDB POST と一致させる）
FIELDS = ["co2", "temp", "rh", "voc", "nox", "laeq", "lamax", "rssi", "mcu_temp"]

# device ごとの固定色。キーは InfluxDB UI が生成する系列ID形式 "<device>-_result-"
# （columnKeys = [...fluxGroupKeyUnion, "result"] の値を "-" 連結）と一致する必要がある。
DEVICE_COLORS = {
    "env-1-_result-": "#3987e5",
    "env-2-_result-": "#d95926",
    "env-3-_result-": "#199e70",
}


def load_template():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return json.load(f)


def firmware_fields():
    """office-env-base.yaml の InfluxDB POST が送るフィールド名を抽出する。"""
    path = os.path.join(ROOT, "config", "office-env-base.yaml")
    with open(path, encoding="utf-8") as f:
        return set(re.findall(r'add_[if]\("(\w+)"', f.read()))


def charted_fields():
    """テンプレートの各セルがクエリしているフィールド名を抽出する。"""
    dash = next(r for r in load_template() if r.get("kind") == "Dashboard")
    found = set()
    for c in dash["spec"]["charts"]:
        m = re.search(r'r\._field == "(\w+)"', c["queries"][0]["query"])
        found.add(m.group(1))
    return found


class TemplateStructureTest(unittest.TestCase):
    def setUp(self):
        self.contents = load_template()

    def test_is_list_of_resources(self):
        self.assertIsInstance(self.contents, list)
        self.assertGreaterEqual(len(self.contents), 1)

    def test_has_single_dashboard(self):
        dashboards = [r for r in self.contents if r.get("kind") == "Dashboard"]
        self.assertEqual(len(dashboards), 1)

    def test_dashboard_apiversion(self):
        dash = next(r for r in self.contents if r.get("kind") == "Dashboard")
        self.assertEqual(dash.get("apiVersion"), "influxdata.com/v2alpha1")
        self.assertIn("name", dash.get("metadata", {}))

    def test_has_one_chart_per_field(self):
        dash = next(r for r in self.contents if r.get("kind") == "Dashboard")
        charts = dash["spec"]["charts"]
        self.assertEqual(len(charts), len(FIELDS))

    def test_each_chart_is_xy_line(self):
        dash = next(r for r in self.contents if r.get("kind") == "Dashboard")
        for c in dash["spec"]["charts"]:
            self.assertEqual(c["kind"], "Xy")
            self.assertEqual(c["geom"], "line")
            self.assertEqual(c["xCol"], "_time")
            self.assertEqual(c["yCol"], "_value")

    def test_covers_all_fields_once(self):
        dash = next(r for r in self.contents if r.get("kind") == "Dashboard")
        found = []
        for c in dash["spec"]["charts"]:
            q = c["queries"][0]["query"]
            for fld in FIELDS:
                if f'r._field == "{fld}"' in q:
                    found.append(fld)
        self.assertCountEqual(found, FIELDS)

    def test_queries_group_by_device_without_device_filter(self):
        dash = next(r for r in self.contents if r.get("kind") == "Dashboard")
        for c in dash["spec"]["charts"]:
            q = c["queries"][0]["query"]
            self.assertIn('group(columns: ["device"])', q)
            self.assertIn("v.windowPeriod", q)
            self.assertIn("v.timeRangeStart", q)
            self.assertNotIn('r.device ==', q)
            self.assertNotIn('r["device"] ==', q)

    def test_covers_every_field_the_firmware_sends(self):
        """ファームが InfluxDB へ送る全フィールドにセルがあること（表示漏れの再発防止）。"""
        self.assertEqual(charted_fields(), firmware_fields())

    def test_local_dashboard_covers_every_field(self):
        """ローカルHTMLダッシュボード(scripts/dashboard.py)も全フィールドを表示すること。"""
        path = os.path.join(ROOT, "scripts", "dashboard.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        block = re.search(r"FIELDS = \[(.*?)\]", src, re.S).group(1)
        local = {m for m in re.findall(r'\("(\w+)",', block)}
        self.assertEqual(local, firmware_fields())

    def test_device_colors_are_fixed_in_every_chart(self):
        """全セルで device→色 が同一。セル間で同じ device が違う色になるのを防ぐ。"""
        dash = next(r for r in self.contents if r.get("kind") == "Dashboard")
        for c in dash["spec"]["charts"]:
            self.assertEqual(c.get("colorMapping"), DEVICE_COLORS, c["name"])

    def test_color_scale_matches_device_color_order(self):
        """colorMapping が使えない場合（系列集合が変化した描画）でも同じ色になるよう
        colors の並び順を device 順と一致させる。"""
        dash = next(r for r in self.contents if r.get("kind") == "Dashboard")
        for c in dash["spec"]["charts"]:
            hexes = [x["hex"] for x in c["colors"]]
            self.assertEqual(hexes, list(DEVICE_COLORS.values()), c["name"])

    def test_cells_do_not_overlap(self):
        """12カラムグリッド上でセルが重ならないこと。"""
        dash = next(r for r in self.contents if r.get("kind") == "Dashboard")
        occupied = set()
        for c in dash["spec"]["charts"]:
            self.assertLessEqual(c["xPos"] + c["width"], 12, c["name"])
            for x in range(c["xPos"], c["xPos"] + c["width"]):
                for y in range(c["yPos"], c["yPos"] + c["height"]):
                    self.assertNotIn((x, y), occupied, f'{c["name"]} が ({x},{y}) で重複')
                    occupied.add((x, y))

    def test_measurement_and_bucket(self):
        dash = next(r for r in self.contents if r.get("kind") == "Dashboard")
        for c in dash["spec"]["charts"]:
            q = c["queries"][0]["query"]
            self.assertIn('from(bucket: "office_env")', q)
            self.assertIn('r._measurement == "env"', q)


if __name__ == "__main__":
    unittest.main()
