import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_PATH = os.path.join(ROOT, "influx", "dashboard-office-env.json")

FIELDS = ["co2", "temp", "rh", "voc", "laeq"]


def load_template():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return json.load(f)


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

    def test_has_five_charts(self):
        dash = next(r for r in self.contents if r.get("kind") == "Dashboard")
        charts = dash["spec"]["charts"]
        self.assertEqual(len(charts), 5)

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

    def test_measurement_and_bucket(self):
        dash = next(r for r in self.contents if r.get("kind") == "Dashboard")
        for c in dash["spec"]["charts"]:
            q = c["queries"][0]["query"]
            self.assertIn('from(bucket: "office_env")', q)
            self.assertIn('r._measurement == "env"', q)


if __name__ == "__main__":
    unittest.main()
