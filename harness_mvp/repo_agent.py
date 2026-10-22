"""Bounded repository-repair agent: one episode = fresh workspace + fresh conversation.

Flow: tool-calling turns (bounded model calls, tool calls and test runs) -> end of episode ->
public-test fallback (public outcomes only) -> hidden scoring in a separate container.
Hidden-test results are computed only after the last model call and never reach the model.
"""
from __future__ import annotations

import time
from pathlib import Path

from . import llm
from .benchmark import RepoTask
from .contracts import GUIDANCE_HEADING, AgentLimits, estimate_tokens
from .model import CallBudget
from .tools import EXAMPLE_REPLY, ToolExecutor, cap, function_declarations, parse_tool_calls, tool_help
from .workspace import Workspace, candidate_hash, execution_files

SYSTEM_TEMPLATE = """You are a careful software engineer repairing bugs in a small Python repository. You act only by requesting actions.

Request actions by calling the provided functions, or by replying with exactly one JSON object and nothing else, for example:
{example}
Up to {per_turn} actions per reply run in order; their results come back in the next message.

Actions:
{tools}

Rules: only the writable files can be changed; tests cannot be modified; keep public interfaces unchanged.
Limits: {model_calls} replies, {tool_calls} actions and {test_runs} public test runs per task.
Request "submit" when you are done; if you run out of replies, your current files are submitted."""

INFRA_OUTCOMES = ("api_error", "auth_error", "budget_exhausted")


def system_prompt(limits: AgentLimits) -> str:
    return SYSTEM_TEMPLATE.format(example=EXAMPLE_REPLY, per_turn=limits.max_tool_calls_per_turn, tools=tool_help(),
                                  model_calls=limits.max_model_calls, tool_calls=limits.max_tool_calls,
                                  test_runs=limits.max_test_runs)


def first_user_message(task: RepoTask, guidance_text: str) -> str:
    view = task.public_view()
    parts = [f"# Task: {view['title']}", view["statement"], "",
             "Writable files: " + ", ".join(view["allowed_files"]),
             f"Public tests: {view['public_tests']}"]
    if guidance_text:
        parts += ["", GUIDANCE_HEADING, guidance_text]
    parts += ["", "Start by inspecting the repository."]
    return "\n".join(parts)


def _public_summary(result) -> str:
    head = f"public tests: {result.outcome} ({result.tests_run}/{result.expected_tests} ran)"
    if result.outcome == "passed":
        return head
    body = (result.stdout + "\n" + result.stderr).strip()
    return head + "\n" + body


def run_episode(task: RepoTask, model, runner, budget: CallBudget, workspace_root: Path, *,
                episode_id: str, phase: str, condition: str, repetition: int, guidance: dict,
                limits: AgentLimits = AgentLimits(), api_retries: int = 0, retry_wait_s: float = 10.0,
                order_position: int | None = None, log=None) -> tuple[dict, list[dict], dict]:
    """Returns (episode record, transcript entries, artifacts {snapshots, final, submitted})."""
    log = log or (lambda msg: None)
    started_at, t0 = llm.now_iso(), time.perf_counter()
    pacing_before, backoff = budget.waited_s, 0.0
    ws = Workspace(workspace_root, task)
    tools = ToolExecutor(ws, limits)
    original = ws.candidate()
    guidance_text = guidance.get("text", "")
    messages = [{"role": "system", "content": system_prompt(limits)},
                {"role": "user", "content": first_user_message(task, guidance_text)}]
    turns, transcript, snapshots, snapshot_files = [], [], [], {}
    tool_calls = test_runs = 0
    end_reason, fatal = "model_call_limit", None

    def snapshot(source: str, turn: int, result) -> dict:
        cand = ws.candidate()
        snap = {"snapshot_id": f"s{len(snapshots) + 1}", "turn": turn, "source": source,
                "candidate_hash": candidate_hash(cand), "public": result.to_record()}
        snapshots.append(snap)
        snapshot_files[snap["snapshot_id"]] = cand
        return snap

    for turn_no in range(1, limits.max_model_calls + 1):
        context = {"task_id": task.task_id, "turn": turn_no, "condition": condition, "repetition": repetition,
                   "guidance_memory_ids": guidance.get("memory_ids", []), "guidance_text": guidance_text,
                   "tools": function_declarations()}
        resp, requests, fatal, waited = llm.request(model, messages, budget, context, api_retries=api_retries,
                                                    retry_wait_s=retry_wait_s, log=log, label=f"turn {turn_no}")
        backoff += waited
        turn = {"turn": turn_no, "requests": requests, "parse_error": None, "tool_calls": []}
        turns.append(turn)
        transcript.append({"episode_id": episode_id, "turn": turn_no, "messages": list(messages),
                           "response": resp.text if resp else None,
                           "errors": [r["error"] for r in requests if r["error"]]})
        if resp is None:
            end_reason = fatal or "api_error"
            break
        calls, err = parse_tool_calls(resp.text, limits.max_tool_calls_per_turn)
        messages = messages + [{"role": "assistant", "content": resp.text}]
        if err:
            turn["parse_error"] = err
            messages.append({"role": "user", "content": f"Invalid reply: {err}. Reply with one JSON object only."})
            continue
        results, submitted = [], False
        for call in calls:
            if tool_calls >= limits.max_tool_calls:
                results.append(f"[{call['name']}] error: action limit reached")
                break
            tool_calls += 1
            rec = {"name": call["name"], "args": {k: (v if len(str(v)) <= 200 else str(v)[:200] + "...")
                                                 for k, v in call["args"].items()}}
            if call["name"] == "submit":
                rec.update(ok=True, result="submitted")
                submitted = True
            elif call["name"] == "run_public_tests":
                if test_runs >= limits.max_test_runs:
                    rec.update(ok=False, result="error: public test run limit reached")
                else:
                    test_runs += 1
                    files, test_rel = execution_files(task, ws.candidate(), "public")
                    result = runner.run_files(files, test_rel, label=f"{task.task_id}-public")
                    snap = snapshot("agent_test_run", turn_no, result)
                    rec.update(ok=True, result=cap(_public_summary(result), limits.tool_result_chars),
                               snapshot_id=snap["snapshot_id"])
            else:
                ok, out = tools.execute(call["name"], call["args"])
                rec.update(ok=ok, result=cap(out, limits.tool_result_chars))
            turn["tool_calls"].append({k: v for k, v in rec.items() if k != "result"} | {"result_chars": len(rec["result"])})
            results.append(f"[{call['name']}] {rec['result']}")
        if submitted:
            end_reason = "submitted"
            break
        if tool_calls >= limits.max_tool_calls:
            end_reason = "tool_call_limit"
            break
        messages.append({"role": "user", "content": "Action results:\n" + "\n\n".join(results)})

    # ---- Episode over: no more model calls. Public-test fallback uses public outcomes only. ----
    final = ws.candidate()
    final_hash = candidate_hash(final)
    known = [s for s in snapshots if s["candidate_hash"] == final_hash]
    if known:
        final_public = known[-1]["public"]
    else:
        files, test_rel = execution_files(task, final, "public")
        result = runner.run_files(files, test_rel, label=f"{task.task_id}-final-public")
        final_public = snapshot("harness_final_check", None, result)["public"]
    passing = [s for s in snapshots if s["public"]["outcome"] == "passed"]
    fallback = final_public["outcome"] != "passed" and bool(passing)
    submitted_files = snapshot_files[passing[-1]["snapshot_id"]] if fallback else final
    submitted_public = passing[-1]["public"] if fallback else final_public

    # ---- Hidden scoring: separate container, after the episode. Never shown to the agent. ----
    def hidden(cand):
        files, test_rel = execution_files(task, cand, "hidden")
        return runner.run_files(files, test_rel, label=f"{task.task_id}-hidden").to_record()

    hidden_submitted = hidden(submitted_files)
    hidden_pre_fallback = hidden(final) if fallback else None  # analysis only

    requests = [r for t in turns for r in t["requests"]]
    waited = (budget.waited_s - pacing_before) + backoff
    first_run = next((s for s in snapshots if s["source"] == "agent_test_run"), None)
    if fatal in ("auth_error", "budget_exhausted"):
        category = fatal
    elif end_reason == "api_error":
        category = "api_error"
    elif hidden_submitted["outcome"] == "passed":
        category = "none"
    elif hidden_submitted["outcome"] in ("timeout", "runner_error"):
        category = hidden_submitted["outcome"]
    elif submitted_public["outcome"] != "passed":
        category = "public_test_failure"
    else:
        category = "hidden_test_failure"

    record = {
        "episode_id": episode_id,
        "phase": phase,
        "task_id": task.task_id,
        "split": task.split,
        "family": task.family,
        "principles": list(task.principles),  # maintainer label, reporting only
        "condition": condition,
        "repetition": repetition,
        "order_position": order_position,
        "synthetic": bool(getattr(model, "synthetic", False)),
        "task_hashes": task.hashes(),
        "started_at": started_at,
        "guidance": {"condition": condition, "memory_ids": guidance.get("memory_ids", []),
                     "scores": guidance.get("scores", {}), "text": guidance_text,
                     "chars": len(guidance_text), "estimated_tokens": estimate_tokens(guidance_text),
                     "budget_tokens": guidance.get("budget_tokens"), "source_hash": guidance.get("source_hash")},
        "turns": turns,
        "snapshots": snapshots,
        "end_reason": end_reason,
        "model_calls": len(requests),
        "tool_calls": tool_calls,
        "test_runs": test_runs,
        "edits": tools.edits,
        "parse_errors": sum(1 for t in turns if t["parse_error"]),
        "files_changed": sorted(k for k in final if final[k] != original[k]),
        "first_public_pass": bool(first_run and first_run["public"]["outcome"] == "passed"),
        "ever_public_pass": bool(passing),
        "final_candidate_hash": final_hash,
        "final_public_pass": final_public["outcome"] == "passed",
        "fallback_activated": fallback,
        "raw_regression": fallback,  # an earlier candidate passed public tests, the final one did not
        "submitted_candidate_hash": candidate_hash(submitted_files),
        "submitted_public_pass": submitted_public["outcome"] == "passed",
        "hidden": hidden_submitted,
        "hidden_success": hidden_submitted["outcome"] == "passed",
        "pre_fallback_hidden": hidden_pre_fallback,
        "input_tokens": llm.sum_or_none(r["input_tokens"] for r in requests),
        "output_tokens": llm.sum_or_none(r["output_tokens"] for r in requests),
        "total_tokens": llm.sum_or_none(r["total_tokens"] for r in requests),
        "model_latency_s": round(sum(r["latency_s"] for r in requests), 3),
        "wait_s": round(waited, 3),
        "episode_latency_s": round(time.perf_counter() - t0 - waited, 3),
        "api_errors": sum(1 for r in requests if not r["ok"]),
        "timeouts": sum(1 for s in snapshots if s["public"]["outcome"] == "timeout")
                    + int(hidden_submitted["outcome"] == "timeout"),
        "error_category": category,
        "fatal": fatal if fatal in ("auth_error", "budget_exhausted") else None,
    }
    artifacts = {"snapshots": snapshot_files, "final": final, "submitted": submitted_files}
    return record, transcript, artifacts
