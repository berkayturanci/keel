"""Standalone subagent command handler.

Supports implement, ci-check, morning, wrap, work-block, overnight, regression,
and review-all-day commands.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import config as cfg
from . import consent, contracts, github_transport, runtime
from . import orchestrator as orch
from .extensions import load_extensions
from .gates import GateError


def _issue_labels(args: argparse.Namespace) -> tuple[str, ...]:
    labels: list[str] = []
    for raw in getattr(args, "issue_label", ()) or ():
        labels.extend(part.strip() for part in raw.split(",") if part.strip())
    return tuple(dict.fromkeys(labels))


def _issue_context_provided(args: argparse.Namespace) -> bool:
    return bool(
        (getattr(args, "issue_title", None) or "").strip()
        or (getattr(args, "issue_body", None) or "").strip()
        or _issue_labels(args)
    )


def _standalone_target(args: argparse.Namespace) -> str | None:
    if getattr(args, "issue", None) is not None:
        issue = f"issue #{args.issue}"
        extra = getattr(args, "target", None)
        return f"{issue} ({extra})" if extra else issue
    if getattr(args, "pr", None) is not None:
        return f"PR #{args.pr}"
    if getattr(args, "since", None) is not None:
        extra = getattr(args, "target", None)
        target = f"since {args.since}"
        return f"{target} ({extra})" if extra else target
    if getattr(args, "scope", None) is not None:
        scope = f"scope {args.scope}"
        extra = getattr(args, "target", None)
        if getattr(args, "days", None) is not None:
            scope = f"{args.days} day scan ({scope})"
        return f"{scope} ({extra})" if extra else scope
    if getattr(args, "days", None) is not None:
        return f"{args.days} day scan"
    if getattr(args, "issues", None):
        target = "issues " + ", ".join(f"#{issue}" for issue in args.issues)
        max_items = getattr(args, "max_items", None)
        extra = getattr(args, "target", None)
        if max_items is not None:
            target = f"{target} (max {max_items})"
        return f"{target} ({extra})" if extra else target
    if getattr(args, "queue", None) is not None:
        target = f"queue {args.queue}"
        max_items = getattr(args, "max_items", None)
        extra = getattr(args, "target", None)
        if max_items is not None:
            target = f"{target} (max {max_items})"
        return f"{target} ({extra})" if extra else target
    if getattr(args, "title", None) is not None:
        return args.title
    if getattr(args, "hours", None) is not None:
        target = f"{args.hours:g}h session"
        max_items = getattr(args, "max_items", None)
        return f"{target} (max {max_items})" if max_items is not None else target
    return getattr(args, "target", None)


def _has_live_consent_scope(
    args: argparse.Namespace,
    command: str,
    config: cfg.ProjectConfig,
    requirement: runtime.CapabilityRequirement,
    loaded: dict,
) -> bool:
    if not getattr(args, "live", False):
        return False
    side_effects = contracts.command_side_effects(command, config, requirement, loaded)
    return bool(consent.side_effect_scopes(side_effects))


def cmd_standalone(args: argparse.Namespace) -> int:
    """Execute any standalone subagent command."""
    if getattr(args, "dry_run", False) and getattr(args, "live", False):
        print("--dry-run and --live cannot be used together", file=sys.stderr)
        return 1
    command = args.standalone_command
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    loaded, problems = load_extensions(config, args.root, strict=False)
    for prob in problems:
        print(f"  ! extension not loaded: {prob}", file=sys.stderr)

    requirement = (
        runtime.ci_check_capability_requirement(config)
        if command == "ci-check"
        else runtime.morning_capability_requirement(config)
        if command == "morning"
        else runtime.scan_capability_requirement(command, config)
        if command in {"regression", "review-all-day"}
        else runtime.build_capability_requirement(
            command, config, loaded, pr=getattr(args, "pr", None)
        )
    )
    report = runtime.detect(args.root)
    evaluation = runtime.evaluate(requirement, report)
    if not evaluation.ok:
        print(evaluation.render(), file=sys.stderr)
        return 1
    transport = github_transport.resolve(report)
    target = _standalone_target(args)
    try:
        consent_mode = consent.resolve_consent_mode(
            getattr(args, "consent_mode", None),
            config.consent_mode,
            env_mode=os.environ.get("KEEL_CONSENT_MODE"),
        )
        approved_scopes, approval_source, approval_operator, consent_mode = (
            consent.resolve_approved_consent(
                mode=consent_mode,
                explicit_scopes=tuple(getattr(args, "approve_scope", ()) or ()),
                operator=getattr(args, "operator", None),
                is_live=getattr(args, "live", False),
                has_standing_scope=_has_live_consent_scope(
                    args, command, config, requirement, loaded
                ),
                env_scopes=os.environ.get("KEEL_APPROVE_SCOPE"),
                env_operator=os.environ.get("KEEL_OPERATOR"),
                config_approved_scopes=config.automation.approved_scopes,
                config_operator=config.automation.operator,
            )
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        plan = orch.build_plan(config, loaded)
    except GateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    contract = contracts.build_command_contract(
        command=command,
        config=config,
        loaded=loaded,
        plan=plan,
        requirement=requirement,
        evaluation=evaluation,
        transport=transport,
        extension_problems=tuple(problems),
        dry_run=not getattr(args, "live", False),
        approved_consent_scopes=approved_scopes,
        consent_approval_source=approval_source,
        consent_mode=consent_mode,
        operator=approval_operator,
        target=target,
        reviewer_override=getattr(args, "reviewers", None),
        review_comments=getattr(args, "review_comments", "inline"),
        issue_title=getattr(args, "issue_title", None),
        issue_body=getattr(args, "issue_body", None),
        issue_labels=_issue_labels(args),
    )
    consent_ok, consent_message = consent.assert_operator_consent(contract["operator_consent"])
    result = contracts.standalone_result_as_dict(
        command=command,
        config=config,
        target=target,
        delegate=getattr(args, "delegate", None),
        transport=transport,
        evaluation=evaluation,
    )
    if not consent_ok:
        if args.json:
            print(json.dumps({"contract": contract, "result": result}, indent=2, sort_keys=True))
        else:
            print(consent_message, file=sys.stderr)
        return 1
    intake_record = contract.get("issue_intake")
    is_live_implement = command == "implement" and getattr(args, "live", False)
    if is_live_implement and _issue_context_provided(args):
        if intake_record and not intake_record["can_mutate_code"]:
            if args.json:
                print(
                    json.dumps({"contract": contract, "result": result}, indent=2, sort_keys=True)
                )
            else:
                print(
                    f"issue intake: {intake_record['status']} — {intake_record['reason']}",
                    file=sys.stderr,
                )
                for question in intake_record["questions"]:
                    print(f"  question: {question}", file=sys.stderr)
            return 1
    if args.json:
        print(json.dumps({"contract": contract, "result": result}, indent=2, sort_keys=True))
        return 0

    name = config.repo or config.extends
    print(f"keel {command} — {name}  (base {config.base_branch})")
    print(f"  target        : {target or 'not specified'}")
    print(f"  profile       : {contract['workflow_profile']['profile']}")
    print(f"  github        : {transport.name}")
    print(f"  consent       : {contract['operator_consent']['status']}")
    if evaluation.missing_optional:
        print(f"  degraded opt. : {', '.join(evaluation.missing_optional)}")
    if command == "implement":
        print(f"  worktree      : {result['worktree_path_pattern']}")
        print(f"  branch        : {result['branch_pattern']}")
        print("  merge         : never in standalone implement")
        if args.delegate:
            print(f"  delegate      : {args.delegate}")
    elif command == "ci-check":
        workflows = ", ".join(result["ci_workflows"]) or "not configured"
        print(f"  workflows     : {workflows}")
        print("  mode          : read-only; propose one fix, never apply")
    elif command == "morning":
        brief = result["brief"]
        health = brief["health_providers"]
        unavailable = [p["name"] for p in health if p["status"] in {"blocked", "unavailable"}]
        report_names = ", ".join(brief["reports"]) or "not configured"
        print(f"  reports       : {report_names}")
        print(f"  health        : {len(health)} provider(s)")
        if unavailable:
            print(f"  unavailable   : {', '.join(unavailable)}")
        print(f"  deferrals     : {brief['deferral_queue']['status']}")
    elif command in {"wrap", "work-block", "overnight"}:
        session = result["session"]
        report_names = ", ".join(session["reports"]) or "not configured"
        print(f"  reports       : {report_names}")
        print(f"  deferrals     : {session['deferral_queue']['status']}")
        if command == "wrap":
            linked_required = session["wrap"]["workspace_preflight"][
                "must_run_from_linked_worktree"
            ]
            print(f"  worktree      : linked required={linked_required}")
            print("  pr            : ready PR after configured gates")
        elif command == "work-block":
            print("  mode source   : keel work-block")
            print("  queue         : explicit issues or selector")
            print("  handoff       : ship per issue")
            print("  outcomes      : shipped, PR-open, deferred, blocked, skipped, needs-input")
        else:
            print(f"  window        : {session['merge_window'] or 'not configured'}")
            print(f"  mode source   : {session['overnight']['mode_source']['command']}")
            print("  merge policy  : ship window + no-night-merge")
    elif command in {"regression", "review-all-day"}:
        scan = result["scan"]
        print(f"  areas         : {len(scan['areas'])} configured")
        print(f"  dedupe        : similarity>={scan['dedupe']['near_text_similarity']}")
        print("  writes        : issues only after consent; no code/PR mutation")
        if command == "review-all-day":
            print(f"  title prefix  : {scan['review_all_day']['issue_creation']['title_prefix']}")
        else:
            print(f"  handoff       : {scan['regression']['issue_creation']['route_to']}")
    mode = "live preflight contract" if getattr(args, "live", False) else "dry-run contract"
    print(f"  note          : {mode}; adapters perform any approved live work.")
    return 0
