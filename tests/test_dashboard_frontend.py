"""Behavior regressions for the actual inline dashboard JavaScript.

Node is optional for Python-only installs. These tests do not install packages,
start a browser/server, or permit the script to issue live HTTP requests.
"""

import json
import os
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "src/bbwatch/dashboard/index.html"
HARNESS = Path(__file__).parent / "fixtures/frontend-harness.cjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is needed for inline JavaScript tests")


def frontend(scenario):
    result = subprocess.run(
        [NODE, str(HARNESS), str(HTML), scenario],
        capture_output=True,
        text=True,
        timeout=5,
        env={**os.environ, "TZ": "Pacific/Honolulu"},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout, f"Dashboard operation did not finish: {scenario}"
    return json.loads(result.stdout)


class Tags(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.tags = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def assert_failure_is_visible(result):
    assert re.search(
        r"失败|无法|不可用|错误|重试|断开|中断|未连接|网络|error|fail|unavailable|retry",
        result["text"],
        re.IGNORECASE,
    ), "The interface must explain the failed operation"


def test_deadlines_always_display_china_time_and_roll_over_dates():
    assert frontend("helpers")["times"] == ["09/09 00:05", "09/09 00:05", "01/01 07:59"]


def test_assignment_links_encode_ids_and_fall_back_to_course_grades():
    assert frontend("helpers")["links"] == [
        ("https://bb.cuhk.edu.cn/webapps/assignment/uploadAssignment"
         "?content_id=_x%3F2&course_id=_c%20%261&mode=view"),
        "https://bb.cuhk.edu.cn/webapps/gradebook/do/student/viewGrades?course_id=_course_1",
    ]


@pytest.mark.parametrize("row", ["task", "done", "pending", "hidden"])
def test_rows_escape_remote_assignment_names_and_keep_submission_links(row):
    markup = frontend("helpers")[row]
    assert "Assignment &lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;" in markup
    tags = Tags(markup).tags
    assert not any(tag == "script" for tag, _ in tags)
    links = [attrs for tag, attrs in tags if tag == "a"]
    assert any("content_id=_content_9" in attrs.get("href", "") for attrs in links)
    assert all("noopener" in attrs.get("rel", "") for attrs in links
               if attrs.get("target") == "_blank")


@pytest.mark.parametrize("row, checked", [("task", "false"), ("done", "true")])
def test_completion_control_is_keyboard_operable_and_exposes_state(row, checked):
    tags = Tags(frontend("helpers")[row]).tags
    controls = [(tag, attrs) for tag, attrs in tags
                if attrs.get("role") == "checkbox" or "aria-pressed" in attrs
                or (tag == "input" and attrs.get("type") == "checkbox")]
    assert len(controls) == 1, "Each task needs one accessible completion control"
    tag, attrs = controls[0]
    assert tag in {"button", "input"}, "Use a native control with built-in keyboard activation"
    if tag == "input":
        assert ("checked" in attrs) is (checked == "true")
    else:
        assert attrs.get("aria-checked", attrs.get("aria-pressed")) == checked
    assert attrs.get("aria-label") or attrs.get("aria-labelledby"), "Name the completion action"


@pytest.mark.parametrize("state", ["initial", "refresh"])
@pytest.mark.parametrize("failure", ["http", "network"])
def test_failed_load_preserves_known_state_and_never_invents_demo_tasks(state, failure):
    result = frontend(f"load_{state}_{failure}")
    assert result["requests"][0]["url"] == "/api/tasks"
    assert result["state"] == result["expectedState"]
    assert "离线演示" not in result["text"]
    assert_failure_is_visible(result)


@pytest.mark.parametrize("state", ["initial", "refresh"])
@pytest.mark.parametrize("group", ["tasks", "pending", "hidden"])
@pytest.mark.parametrize("kind", ["null", "string", "key", "name"])
def test_malformed_task_rows_never_replace_the_last_valid_state(state, group, kind):
    result = frontend(f"malformed_{state}_{group}_{kind}")
    assert result["requests"][0]["url"] == "/api/tasks"
    assert result["state"] == result["expectedState"]
    assert_failure_is_visible(result)


@pytest.mark.parametrize("operation, endpoint", [
    ("done", "/api/done"), ("hide", "/api/hide"), ("restore", "/api/hide"),
])
@pytest.mark.parametrize("failure", ["http", "network"])
def test_failed_mutation_preserves_tasks_and_reports_failure(operation, endpoint, failure):
    result = frontend(f"mutation_{operation}_{failure}")
    assert result["state"] == result["expectedState"]
    assert result["requests"][0]["url"] == endpoint
    assert result["requests"][0]["method"] == "POST"
    assert "离线演示" not in result["text"]
    assert_failure_is_visible(result)


@pytest.mark.parametrize("operation", ["done", "hide", "pending", "restore"])
def test_successful_mutation_waits_for_acceptance_and_uses_server_task_groups(operation):
    result = frontend(f"success_{operation}")
    assert result["beforeResponse"] == result["initial"]
    assert result["state"] == result["expectedState"]
    request = result["requests"][0]
    expected_body = {"entity_key": result["key"]}
    expected_body["done" if operation == "done" else "hidden"] = operation != "restore"
    assert json.loads(request["body"]) == expected_body


@pytest.mark.parametrize("failure", ["http", "network"])
def test_failed_scan_reports_failure_without_polling_or_leaving_busy_controls(failure):
    result = frontend(f"scan_{failure}")
    assert result["requests"][0]["url"] == "/api/scan"
    assert result["intervalCount"] == 0
    assert result["scanDisabled"] is False
    assert result["scanSpinning"] is False
    assert "已开始扫描" not in result["text"]
    assert_failure_is_visible(result)


@pytest.mark.parametrize("scenario", ["reset_inline_scan", "restore_scan_page"])
def test_scan_controls_recover_labels_and_availability_after_busy_state_ends(scenario):
    result = frontend(scenario)
    assert result["before"]["disabled"] is True
    assert "扫描中" in result["before"]["label"]
    assert result["active"] is False
    assert result["scanDisabled"] is False
    assert result["scanSpinning"] is False
    assert result["inlineDisabled"] is False
    assert "扫描中" not in result["inlineLabel"]
    assert "扫描" in result["inlineLabel"]
    assert "扫描中" not in result["scanLabel"]
    assert result["pollTimers"] == 0


def test_disclosure_and_pending_shortcut_controls_support_keyboard_activation():
    tags = Tags(frontend("render")["text"]).tags
    controls = [(tag, attrs) for tag, attrs in tags
                if "onclick" in attrs or "data-action" in attrs]
    assert controls, "The dashboard must render actionable task controls"
    for tag, attrs in controls:
        assert tag in {"button", "input", "summary"} or (
            tag == "a" and attrs.get("href")
        ), f"Clickable {tag} lacks native keyboard behavior: {attrs.get('onclick')}"
