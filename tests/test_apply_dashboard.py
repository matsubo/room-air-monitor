import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import apply_dashboard as ad


class ExtractHostOrgTest(unittest.TestCase):
    def test_extracts_from_write_url(self):
        url = "https://us-east-1-1.aws.cloud2.influxdata.com/api/v2/write?org=ORG123&bucket=office_env&precision=s"
        host, org = ad.extract_host_org(url)
        self.assertEqual(host, "https://us-east-1-1.aws.cloud2.influxdata.com")
        self.assertEqual(org, "ORG123")

    def test_empty_on_garbage(self):
        host, org = ad.extract_host_org("not a url")
        self.assertEqual((host, org), ("", ""))


class ResolveConnTest(unittest.TestCase):
    def test_env_token_takes_priority(self):
        secrets = {
            "influx_write_url": "https://h.example.com/api/v2/write?org=O1&bucket=office_env",
            "influx_dashboard_token": "SECRET_TOKEN",
        }
        env = {"INFLUX_DASHBOARD_TOKEN": "ENV_TOKEN"}
        base, token, org = ad.resolve_conn(secrets, env)
        self.assertEqual(base, "https://h.example.com")
        self.assertEqual(token, "ENV_TOKEN")
        self.assertEqual(org, "O1")

    def test_falls_back_to_secrets_token(self):
        secrets = {
            "influx_write_url": "https://h.example.com/api/v2/write?org=O1&bucket=office_env",
            "influx_dashboard_token": "SECRET_TOKEN",
        }
        base, token, org = ad.resolve_conn(secrets, {})
        self.assertEqual(token, "SECRET_TOKEN")

    def test_strips_token_prefix(self):
        secrets = {
            "influx_write_url": "https://h.example.com/api/v2/write?org=O1",
            "influx_dashboard_token": "Token ABC",
        }
        _, token, _ = ad.resolve_conn(secrets, {})
        self.assertEqual(token, "ABC")

    def test_exits_when_token_missing(self):
        secrets = {"influx_write_url": "https://h.example.com/api/v2/write?org=O1"}
        with self.assertRaises(SystemExit):
            ad.resolve_conn(secrets, {})

    def test_exits_when_org_missing(self):
        secrets = {"influx_dashboard_token": "T"}
        with self.assertRaises(SystemExit):
            ad.resolve_conn(secrets, {})


class BuildPayloadTest(unittest.TestCase):
    def test_dry_run_payload_shape(self):
        contents = [{"kind": "Dashboard"}]
        p = ad.build_payload("O1", contents, dry_run=True, stack_id=None)
        self.assertEqual(p["orgID"], "O1")
        self.assertTrue(p["dryRun"])
        self.assertEqual(p["template"]["contents"], contents)
        self.assertNotIn("stackID", p)

    def test_stack_id_included_when_given(self):
        p = ad.build_payload("O1", [], dry_run=False, stack_id="STACK9")
        self.assertFalse(p["dryRun"])
        self.assertEqual(p["stackID"], "STACK9")


class LoadTemplateTest(unittest.TestCase):
    def test_loads_repo_template(self):
        path = os.path.join(ROOT, "influx", "dashboard-office-env.json")
        contents = ad.load_template(path)
        self.assertIsInstance(contents, list)
        self.assertTrue(any(r.get("kind") == "Dashboard" for r in contents))


if __name__ == "__main__":
    unittest.main()
