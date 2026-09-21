from __future__ import annotations

import asyncio
import sys

import pytest

pytest.importorskip("mcp")

from reviewtrace import core
from reviewtrace.mcp import server as srv
from reviewtrace.reports.json_report import render_json
from reviewtrace.utils.errors import ReviewTraceError

TOOL_NAMES = {
    "extract_review_comments",
    "extract_review_issues",
    "compare_documents",
    "audit_revision",
    "inspect_issue",
    "generate_traceability_matrix",
    "generate_response_to_reviewers",
}


def test_tools_delegate_to_the_same_core_as_the_cli(demo_files):
    audit = srv.audit_revision(
        reviewed_file=str(demo_files["reviewed"]), revised_file=str(demo_files["revised"])
    )
    direct = core.audit_word_comments(demo_files["reviewed"], demo_files["revised"])
    assert [f["status"] for f in audit["findings"]] == [f.status.value for f in direct.findings]
    assert [f["evidence"] for f in audit["findings"]] == [
        f.model_dump(mode="json")["evidence"] for f in direct.findings
    ]


def test_all_tool_functions(demo_files, tmp_path):
    reviewed, revised, original = (str(demo_files[k]) for k in ("reviewed", "revised", "original"))
    assert srv.extract_review_comments(reviewed)["count"] == 4
    ex = srv.extract_review_issues(reviewed)
    assert ex["source"] == "word_comment" and "RT-004" in ex["issues_yaml"]
    assert srv.compare_documents(original, revised)["summary"]["references_added"] == 1
    audit = srv.audit_revision(reviewed_file=reviewed, revised_file=revised)
    one = srv.inspect_issue("rt-004", audit=audit)
    assert one["finding"]["status"] == "UNRESOLVED" and one["issue"]["anchor_text"]
    matrix = srv.generate_traceability_matrix(audit=audit, format="markdown")
    assert "| RT-001 |" in matrix["content"]
    assert len(srv.generate_traceability_matrix(audit=audit)["rows"]) == 4
    letter = srv.generate_response_to_reviewers(audit=audit)
    assert "Comment 4" in letter["markdown"] and letter["needs_author_attention"]
    saved = tmp_path / "audit.json"
    saved.write_text(render_json(core.audit_word_comments(reviewed, revised)), encoding="utf-8")
    assert srv.inspect_issue("RT-001", audit_file=str(saved))["finding"]["status"] == "RESOLVED"


def test_three_file_and_issue_file_audits(demo_files, tmp_path):
    report = demo_files["report_docx"]
    a = srv.audit_revision(
        original_file=str(demo_files["original"]),
        review_file=str(report),
        revised_file=str(demo_files["revised"]),
    )
    assert a["findings"][0]["status"] == "RESOLVED"


def test_bad_arguments_raise_clear_errors(demo_files):
    with pytest.raises(ReviewTraceError, match="revised_file is required"):
        srv.audit_revision(reviewed_file=str(demo_files["reviewed"]))
    with pytest.raises(ReviewTraceError, match="exactly one"):
        srv.audit_revision(original_file="a", revised_file="b")
    with pytest.raises(ReviewTraceError, match="cannot be combined"):
        srv.audit_revision(reviewed_file="a", revised_file="b", issues_file="c")
    with pytest.raises(ReviewTraceError, match="either 'audit'"):
        srv.inspect_issue("RT-001")
    with pytest.raises(ReviewTraceError, match="format"):
        srv.generate_traceability_matrix(
            audit={"schema_version": 1, "mode": "issues_file"}, format="pdf"
        )


def test_root_confinement(demo_files, tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWTRACE_MCP_ROOT", str(tmp_path / "only-here"))
    (tmp_path / "only-here").mkdir()
    with pytest.raises(ReviewTraceError, match="outside the permitted directory"):
        srv.extract_review_comments(str(demo_files["reviewed"]))


def test_server_launches_over_stdio_and_serves_tools(demo_files):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    async def go():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "reviewtrace.mcp.server"]
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            ok = await session.call_tool(
                "audit_revision",
                {
                    "reviewed_file": str(demo_files["reviewed"]),
                    "revised_file": str(demo_files["revised"]),
                },
            )
            bad = await session.call_tool(
                "audit_revision", {"reviewed_file": "nope.docx", "revised_file": "x.docx"}
            )
            return tools, ok, bad

    tools, ok, bad = asyncio.run(asyncio.wait_for(go(), timeout=60))
    assert tools == TOOL_NAMES
    assert not ok.is_error
    assert [f["status"] for f in ok.structured_content["findings"]][3] == "UNRESOLVED"
    assert bad.is_error and "file not found" in bad.content[0].text
