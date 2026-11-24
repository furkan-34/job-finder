"""The permissions the safety design rests on, pinned so that widening one is loud.

Agents that read postings or mail have Read and nothing that writes, runs or fetches; Gmail's
writing tools and the .env file stay denied; the browser hook blocks on failure; nothing under
data/ is committed. A change made on purpose updates this file in the same PR.
"""

import json
import shutil
import subprocess

import pytest

from jobfinder.paths import ROOT

GMAIL_READ = ("search_threads", "get_thread", "get_message")
GMAIL_WRITE = ("send_message", "reply", "forward", "create_draft", "update_draft", "delete_draft",
               "create_label", "update_label", "delete_label", "label_message", "label_thread",
               "unlabel_message", "unlabel_thread", "update_message_labels",
               "apply_sensitive_message_label", "apply_sensitive_thread_label", "mark_message_spam",
               "mark_thread_spam", "unmark_message_spam", "unmark_thread_spam", "trash_message",
               "trash_thread", "untrash_message", "untrash_thread", "create_filter")
GMAIL_PREFIXES = ("mcp__claude_ai_Gmail__", "mcp__Gmail__")
AGENT_TOOLS = {
    "cv-writer": {"Read"},
    "cv-reviewer": {"Read"},
    "job-extractor": {"Read"},
    "job-matcher": {"Read"},
    "profile-extractor": {"Read", "Glob"},
    "mail-reader": {"Read"} | {prefix + tool for prefix in GMAIL_PREFIXES for tool in GMAIL_READ},
    "job-sender": {"Read", "ToolSearch", "mcp__playwright__*"},
}
NEVER_FOR_AGENTS = {"Edit", "Write", "NotebookEdit", "Bash", "WebFetch", "WebSearch", "Agent", "Task"}
ALLOWED_WRITES = {"Write(data/profile/profile.json)", "Write(data/searches/searches.json)",
                  "Write(data/score/results/**)", "Write(data/cv/*/*/answer.json)",
                  "Write(data/track/results/**)", "Write(data/apply/*/*/answer.json)",
                  "Write(data/apply/*/*/user-answers.json)"}


def settings() -> dict:
    return json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))


def agent_tools(name: str) -> set[str] | None:
    """The `tools:` of an agent's frontmatter; None when the line is missing (every tool)."""
    text = (ROOT / ".claude" / "agents" / f"{name}.md").read_text(encoding="utf-8")
    frontmatter = text.split("---")[1]
    for line in frontmatter.splitlines():
        if line.startswith("tools:"):
            return {tool.strip() for tool in line.removeprefix("tools:").split(",") if tool.strip()}
    return None


def test_every_agent_is_pinned():
    assert {path.stem for path in (ROOT / ".claude" / "agents").glob("*.md")} == set(AGENT_TOOLS)


@pytest.mark.parametrize("name", sorted(AGENT_TOOLS))
def test_agent_tools_are_exactly_these(name):
    tools = agent_tools(name)
    assert tools is not None, "an agent without a tools: line gets every tool"
    assert tools == AGENT_TOOLS[name]
    assert not tools & NEVER_FOR_AGENTS


def test_gmail_writing_tools_and_env_stay_denied():
    deny = set(settings()["permissions"]["deny"])
    assert {prefix + tool for prefix in GMAIL_PREFIXES for tool in GMAIL_WRITE} <= deny
    assert {"Read(.env)", "Read(.env.*)"} <= deny


def test_allow_list_does_not_widen():
    allow = settings()["permissions"]["allow"]
    assert not [rule for rule in allow if rule in ("Bash", "Bash(*)", "Write", "Edit")
                or rule.startswith(("Bash(curl", "Bash(wget"))]
    assert not [rule for rule in allow if any(rule == prefix + tool
                                              for prefix in GMAIL_PREFIXES for tool in GMAIL_WRITE)]
    assert {rule for rule in allow if rule.startswith(("Write(", "Edit("))} <= ALLOWED_WRITES
    assert all(rule.startswith("Bash(uv run ") for rule in allow if rule.startswith("Bash("))


def test_browser_hook_blocks_on_failure_and_answers_are_handed_off():
    hooks = settings()["hooks"]
    browser = [hook for entry in hooks["PreToolUse"] if entry["matcher"] == "mcp__playwright__.*"
               for hook in entry["hooks"]]
    assert any("python -m jobfinder.send" in hook["command"] and hook.get("onFailure") == "block"
               for hook in browser)
    assert any("python -m jobfinder.send" in hook["command"]
               for entry in hooks["PostToolUse"] if entry["matcher"] == "mcp__playwright__.*"
               for hook in entry["hooks"])
    assert any("python -m jobfinder.handoff" in hook["command"]
               for entry in hooks["PostToolUse"] if entry["matcher"] == "^(Agent|Task)$"
               for hook in entry["hooks"])


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="no git checkout")
@pytest.mark.parametrize("path, ignored", [
    ("data/profile/profile.json", True), ("data/jobs/jobs.json", True), ("data/cv/x/y/z.pdf", True),
    (".env", True), ("data/jobs/.gitkeep", False), (".env.example", False)])
def test_user_data_and_secrets_are_ignored(path, ignored):
    result = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT, check=False)
    assert (result.returncode == 0) is ignored


def test_the_browser_is_headed_pinned_and_keeps_to_its_own_profile():
    server = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["playwright"]
    args = server["args"]
    assert "--headless" not in args and "--isolated" not in args  # a visible window, as chosen
    assert any(arg.startswith("@playwright/mcp@") and arg[len("@playwright/mcp@"):][0].isdigit()
               for arg in args), "pin the version: npx would run whatever is latest"
    assert args[args.index("--user-data-dir") + 1].startswith("data/apply/")
    assert not {"--cdp-endpoint", "--extension", "--allow-unrestricted-file-access"} & set(args)
    assert settings()["enabledMcpjsonServers"] == ["playwright"]
