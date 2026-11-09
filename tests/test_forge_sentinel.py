#!/usr/bin/env python3
"""Unit test suite for ForgeGraph Sentinel (Codegeist 2026)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.forge_mcp_server import (
    ForgeSentinelMCPServer,
    MarketplaceTenantGuard,
    build_default_enterprise_environment,
    run_fleet_benchmark,
)


class TestForgeGraphSentinel(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ForgeSentinelMCPServer()
        self.graph, self.scenarios, self.guard = build_default_enterprise_environment()

    def test_manifest_declares_all_codegeist_tracks(self) -> None:
        manifest_path = ROOT / "manifest.yml"
        self.assertTrue(manifest_path.exists(), "manifest.yml must exist")
        content = manifest_path.read_text(encoding="utf-8")
        self.assertIn("rovo:agent:", content)
        self.assertIn("jira:issuePanel:", content)
        self.assertIn("compass:componentPage:", content)
        self.assertIn("graph:connector:", content)
        self.assertIn("sentinel-mcp-remote", content)

    def test_teamwork_graph_traversal_and_isolation(self) -> None:
        subgraph = self.graph.traverse_subgraph(
            seed_id="INC-4092",
            cloud_id="cloud-atlassian-enterprise-01",
            max_hops=3,
        )
        self.assertGreaterEqual(subgraph["nodeCount"], 6)
        self.assertGreaterEqual(subgraph["edgeCount"], 5)

        with self.assertRaises(PermissionError):
            self.graph.traverse_subgraph(
                seed_id="INC-4092",
                cloud_id="cloud-unauthorized-tenant-99",
                max_hops=2,
            )

    def test_compass_blast_radius_computation(self) -> None:
        blast = self.graph.compute_blast_radius(
            component_id="compass:svc-ledger-core",
            cloud_id="cloud-atlassian-enterprise-01",
        )
        self.assertIn("compass:svc-payment-checkout", blast["downstreamImpactedServices"])
        self.assertIn("compass:svc-api-gateway", blast["downstreamImpactedServices"])
        self.assertIn("compass:svc-merchant-webhooks", blast["downstreamImpactedServices"])
        self.assertEqual(blast["riskLevel"], "CRITICAL")
        self.assertGreaterEqual(blast["blastRadiusScore"], 70.0)

    def test_pr_regression_correlator_identifies_all_root_causes(self) -> None:
        expected = {
            "INC-4092": "PR-847",
            "PAY-1184": "PR-912",
            "CORE-903": "PR-761",
        }
        for issue_key, expected_pr in expected.items():
            ranked = self.server.correlator.rank_candidate_prs(self.scenarios[issue_key])
            self.assertEqual(ranked[0].pr_id, expected_pr)
            self.assertTrue(ranked[0].is_root_cause)
            self.assertGreaterEqual(ranked[0].confidence_score, 0.80)

    def test_rovo_remediation_planner_and_postmortem(self) -> None:
        plan = self.server.planner.build_remediation_plan(self.scenarios["INC-4092"])
        self.assertEqual(plan["rootCausePR"]["pr_id"], "PR-847")
        self.assertIn("git revert --no-edit PR-847", plan["remediationActions"]["rollbackCommand"])
        self.assertIn("Confluence Post-Mortem: INC-4092", plan["confluencePostmortemMarkdown"])
        self.assertGreater(plan["slaMetrics"]["mttrReductionPercentage"], 95.0)

    def test_marketplace_tenant_guardrails(self) -> None:
        app_ari = "ari:cloud:ecosystem::app/f849c210-7b6a-43e9-9d21-6c84a902e51b"
        sig = self.guard.sign_invocation("cloud-01", app_ari, "correlate-teamwork-graph")
        verified = self.guard.verify_invocation(
            session_cloud_id="cloud-01",
            target_cloud_id="cloud-01",
            app_ari=app_ari,
            action_key="correlate-teamwork-graph",
            signature=sig,
        )
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["marketplaceCompliance"], "GOLD_ENTERPRISE_READY")

        # Block cross-tenant call
        with self.assertRaises(PermissionError):
            self.guard.verify_invocation(
                session_cloud_id="cloud-01",
                target_cloud_id="cloud-02",
                app_ari=app_ari,
                action_key="correlate-teamwork-graph",
                signature=sig,
            )

        # Block destructive action
        bad_sig = self.guard.sign_invocation("cloud-01", app_ari, "jira:DeleteProject")
        with self.assertRaises(PermissionError):
            self.guard.verify_invocation(
                session_cloud_id="cloud-01",
                target_cloud_id="cloud-01",
                app_ari=app_ari,
                action_key="jira:DeleteProject",
                signature=bad_sig,
            )

    def test_mcp_jsonrpc_protocol_endpoints(self) -> None:
        init_res = self.server.handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init_res["result"]["serverInfo"]["name"], "forgegraph-sentinel-atlassian-mcp")

        list_res = self.server.handle_jsonrpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tool_names = {t["name"] for t in list_res["result"]["tools"]}
        self.assertEqual(
            tool_names,
            {
                "teamwork_graph_traverse",
                "diagnose_incident_rca",
                "audit_pr_blast_radius",
                "draft_rovo_remediation",
                "verify_marketplace_tenant_guard",
            },
        )

        call_res = self.server.handle_jsonrpc(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "diagnose_incident_rca",
                    "arguments": {"issueKey": "INC-4092"},
                },
            }
        )
        self.assertFalse(call_res["result"]["isError"])
        structured = call_res["result"]["structuredContent"]
        self.assertEqual(structured["rootCausePR"]["pr_id"], "PR-847")

    def test_fleet_benchmark_summary(self) -> None:
        bench = run_fleet_benchmark()
        self.assertEqual(bench["scenariosEvaluated"], 3)
        self.assertEqual(bench["rootCauseTop1Accuracy"], "100.0%")
        self.assertGreater(bench["fleetMttrReductionPercentage"], 97.0)


if __name__ == "__main__":
    unittest.main()
