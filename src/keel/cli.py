"""The ``keel`` command-line interface (thin; logic lives in the pure modules).

Subcommands
-----------
``keel version``                 print the version
``keel validate <project.yaml…>``  validate config(s) against the schema (CI gate)
``keel plan <project.yaml>``       render the backbone plan for a project (dry-run view)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import (
    __version__,
    artifacts,
    capture,
    checkpoint,
    classify,
    closure,
    consent,
    contracts,
    evidence,
    gates,
    git,
    github,
    github_transport,
    install,
    jury,
    ledger,
    lock,
    project_commands,
    runcontrols,
    runtime,
    scaffold,
    ship,
    status,
    stepverifier,
    window,
)
from . import config as cfg
from . import findings as fnd
from . import orchestrator as orch
from .extensions import ExtensionError, load_extensions
from .gates import GateSpec
from .runner import command_gate_runner, run_argv


def _gate_runner(root: str, diff_text: str):
    """A gate runner that handles command gates plus the ``jury`` built-in (on the diff)."""
    commands = command_gate_runner(root)

    def run(spec: GateSpec):
        if spec.kind == "builtin" and spec.id == "jury":
            return jury.run_gate(diff_text, cwd=root)
        return commands(spec)

    return run


def _cmd_version(args: argparse.Namespace) -> int:
    print(f"keel {__version__}")
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    rc = 0
    for path in args.paths:
        try:
            config = cfg.load_config(path)
        except FileNotFoundError:
            print(f"MISSING  {path}")
            rc = 1
            continue
        except cfg.ConfigError as exc:
            print(f"INVALID  {path}")
            print(f"         {exc}".replace("\n", "\n         "))
            rc = 1
            continue

        if args.root is not None:
            try:
                load_extensions(config, args.root, strict=True)
            except ExtensionError as exc:
                print(f"INVALID  {path} (extensions)")
                print(f"         {exc}".replace("\n", "\n         "))
                rc = 1
                continue
        print(f"OK       {path}  ({config.repo or '-'}, base {config.base_branch})")
    return rc


def _cmd_plan(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    loaded, problems = load_extensions(config, args.root, strict=False)
    try:
        plan = orch.build_plan(config, loaded)
    except gates.GateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    requirement = (
        _scan_capability_requirement(args.command_contract, config)
        if args.command_contract in {"regression", "review-all-day"}
        else _capability_requirement(args.command_contract, config, loaded)
    )
    report = runtime.detect(args.root)
    evaluation = runtime.evaluate(requirement, report)
    transport = github_transport.resolve(report)
    try:
        approved_scopes, approval_source, approval_operator, consent_mode = _approved_consent(
            args,
            config,
            _has_live_consent_scope(
                args, args.command_contract, config, requirement, loaded
            ),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    contract = contracts.build_command_contract(
        command=args.command_contract,
        profile=args.profile,
        config=config,
        loaded=loaded,
        plan=plan,
        requirement=requirement,
        evaluation=evaluation,
        transport=transport,
        extension_problems=tuple(problems),
        dry_run=not args.live,
        approved_consent_scopes=approved_scopes,
        consent_approval_source=approval_source,
        consent_mode=consent_mode,
        operator=approval_operator,
        target=args.target,
        reviewer_override=args.reviewers,
        review_comments=args.review_comments,
        jury=args.jury,
        no_jury=args.no_jury,
        jury_advisory=args.jury_advisory,
        issue_title=args.issue_title,
        issue_body=args.issue_body,
        issue_labels=_issue_labels(args),
    )
    consent_ok, consent_message = consent.assert_operator_consent(contract["operator_consent"])
    if args.json:
        print(json.dumps({
            "contract": contract,
            "plan": orch.plan_as_dict(plan),
            "capabilities": evaluation.as_dict(),
            "github_transport": transport.as_dict(),
        }, indent=2, sort_keys=True))
    else:
        print(orch.render_plan(config, plan))
        print(evaluation.render())
        print(f"operator consent: {contract['operator_consent']['status']}")
        print(f"  {contract['operator_consent']['consent_prompt']}")
    for prob in problems:
        print(f"  ! extension not loaded: {prob}", file=sys.stderr)
    if not consent_ok:
        print(consent_message, file=sys.stderr)
        return 1
    return 0


def _cmd_run_gates(args: argparse.Namespace) -> int:
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

    try:
        specs = gates.plan_gates(config, loaded)
    except gates.GateError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    requirement = _capability_requirement("run-gates", config, loaded)
    report = runtime.detect(args.root)
    evaluation = runtime.evaluate(requirement, report)
    if not evaluation.ok:
        print(evaluation.render(), file=sys.stderr)
        return 1
    if evaluation.missing_optional:
        print(evaluation.render(), file=sys.stderr)

    diff_text = git.diff(config.base_branch, "HEAD", cwd=args.root)
    outcomes = gates.run_gates(specs, _gate_runner(args.root, diff_text))
    for o in outcomes:
        status = "ok" if o.ok else "FAIL"
        print(f"  {status:>4}  {o.gate}")

    verdict = fnd.summarize(gates.collect_findings(outcomes))
    for f in verdict.findings:
        print(f"    [{f.severity}] {f.source}: {f.message.splitlines()[0]}")
    if verdict.blocked:
        print("BLOCKED — merge is gated by the findings above")
        return 1
    return 0


def _cmd_window(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not config.timezone or not config.merge_window:
        print("no merge window configured (needs timezone + merge_window)")
        return 0
    is_open = window.is_merge_open(config.timezone, config.merge_window)
    state = "OPEN" if is_open else "CLOSED (night no-merge)"
    print(f"merge window {state}  [{config.timezone} {config.merge_window}]")
    return 0


def _cmd_claim(args: argparse.Namespace) -> int:
    result = lock.claim_resource(_lock_root(args.root), args.resource, owner=args.owner)
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(f"keel claim — {result.status}  {result.resource}")
        print(f"  owner : {result.owner}")
        print(f"  path  : {result.path}")
        if result.holder:
            print(f"  holder: {result.holder}")
    return 0 if result.granted else 1


def _cmd_release(args: argparse.Namespace) -> int:
    result = lock.release_resource(_lock_root(args.root), args.resource, owner=args.owner)
    if args.json:
        print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    else:
        print(f"keel release — {result.status}  {result.resource}")
        print(f"  owner : {result.owner}")
        print(f"  path  : {result.path}")
        if result.holder:
            print(f"  holder: {result.holder}")
    return 0 if result.status in {"released", "missing"} else 1


def _cmd_worktree_remove(args: argparse.Namespace) -> int:
    try:
        worktree_path = _validated_worktree_path(args.root, args.worktree)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    result = git.worktree_remove(str(worktree_path), cwd=args.root)
    if args.json:
        print(json.dumps({
            "worktree": str(worktree_path),
            "removed": result.ok,
            "code": result.code,
            "output": result.output,
        }, indent=2, sort_keys=True))
    else:
        print(f"keel worktree-remove — {'removed' if result.ok else 'failed'}  {worktree_path}")
        if result.output.strip():
            print(result.output.strip())
    return 0 if result.ok else 1


def _cmd_merge(args: argparse.Namespace) -> int:
    args.live = True
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
    requirement = runtime.CapabilityRequirement(required=("git",), optional=("gh", "gh-auth"))
    report = runtime.detect(args.root)
    evaluation = runtime.evaluate(requirement, report)
    transport = github_transport.resolve(report)
    if not evaluation.ok or not transport.supports("pr_merge"):
        print(evaluation.render(), file=sys.stderr)
        print(transport.render(), file=sys.stderr)
        return 1
    try:
        approved_scopes, approval_source, approval_operator, consent_mode = _approved_consent(
            args, config, True
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    operator_consent = consent.build_consent_contract(
        command="merge",
        side_effects=("git_worktree", "merge"),
        dry_run=False,
        approved_scopes=approved_scopes,
        approval_source=approval_source,
        mode=consent_mode,
        operator=approval_operator,
        target=f"PR #{args.pr}",
    )
    consent_ok, consent_message = consent.assert_operator_consent(operator_consent)
    if not consent_ok:
        print(consent_message, file=sys.stderr)
        return 1
    escalation = consent.evaluate_escalation(
        risk_tier=args.risk_tier,
        trust_signal=args.trust_signal,
        side_effects=("git_worktree", "merge", *args.escalation_side_effect),
        retry_count=args.retry_count,
        conflicting_sources=args.conflicting_sources,
        changed_lines=args.changed_lines,
    )
    missing_escalation_scope = [
        scope for scope in escalation["consent_scope"] if scope not in approved_scopes
    ]
    if escalation["operator_required"] and missing_escalation_scope:
        payload = {
            "schema_version": "keel.merge.v1",
            "pull_request": args.pr,
            "status": "fail",
            "reason": "operator escalation required",
            "escalation": escalation,
            "missing_scope": missing_escalation_scope,
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(
                "operator escalation required: missing approved scope "
                f"{', '.join(missing_escalation_scope)}",
                file=sys.stderr,
            )
        return 1

    owner = args.owner or f"keel-merge-pr-{args.pr}"
    claim = lock.claim_resource(_lock_root(args.root), "merge", owner=owner)
    payload: dict[str, object] = {
        "schema_version": "keel.merge.v1",
        "pull_request": args.pr,
        "lock": claim.as_dict(),
        "window": None,
        "ci": None,
        "evidence": None,
        "escalation": escalation,
        "merged": False,
    }
    if not claim.granted:
        return _finish_merge(args, payload, "resource lock is already held", code=1)
    try:
        if not args.hotfix and config.timezone and config.merge_window:
            open_now = window.is_merge_open(config.timezone, config.merge_window)
            payload["window"] = {
                "open": open_now,
                "timezone": config.timezone,
                "merge_window": config.merge_window,
            }
            if not open_now:
                return _finish_merge(args, payload, "merge window is closed", code=1)
        elif args.hotfix:
            payload["window"] = {"bypassed": True, "reason": "hotfix"}

        try:
            snapshot = _merge_snapshot(args.pr, cwd=args.root)
        except ValueError as exc:
            return _finish_merge(args, payload, str(exc), code=1)
        payload["ci"] = snapshot["ci"]
        if snapshot["merge_state"] not in {"CLEAN", "HAS_HOOKS", "UNKNOWN"}:
            return _finish_merge(
                args, payload, f"PR merge state is {snapshot['merge_state']}", code=1
            )
        if snapshot["ci"]["state"] != "pass":
            return _finish_merge(args, payload, f"CI is {snapshot['ci']['state']}", code=1)

        evidence_payload = _verify_merge_evidence(args, config)
        payload["evidence"] = evidence_payload
        if not evidence_payload["enforced"]:
            return _finish_merge(args, payload, "evidence gate is not enforced", code=1)
        if evidence_payload["verification"]["status"] != "pass":
            missing = ", ".join(evidence_payload["verification"]["missing"])
            return _finish_merge(args, payload, f"missing evidence: {missing}", code=1)

        if args.dry_run:
            return _finish_merge(args, payload, "dry-run: merge not performed", code=0)
        merged = github.merge_pr(args.pr, method=args.method, cwd=args.root)
        payload["merged"] = merged.ok
        payload["merge_output"] = merged.output
        if not merged.ok:
            return _finish_merge(args, payload, "gh merge failed", code=1)
        return _finish_merge(args, payload, "merged", code=0)
    finally:
        lock.release_resource(_lock_root(args.root), "merge", owner=owner, best_effort=True)


def _cmd_ship(args: argparse.Namespace) -> int:
    if args.dry_run and args.live:
        print("--dry-run and --live cannot be used together", file=sys.stderr)
        return 1
    command = getattr(args, "ship_command", "ship")
    profile = "compound" if args.compound or args.profile == "compound" else "standard"

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

    requirement = _capability_requirement(command, config, loaded, pr=args.pr)
    report = runtime.detect(args.root)
    evaluation = runtime.evaluate(requirement, report)
    if not evaluation.ok:
        print(evaluation.render(), file=sys.stderr)
        return 1
    transport = github_transport.resolve(report)
    if args.pr is not None and not transport.supports("check_runs"):
        print(transport.render(), file=sys.stderr)
        print("missing required GitHub transport capability: check_runs", file=sys.stderr)
        return 1
    try:
        approved_scopes, approval_source, approval_operator, consent_mode = _approved_consent(
            args, config, _has_live_consent_scope(args, command, config, requirement, loaded)
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        plan = orch.build_plan(config, loaded)
    except gates.GateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    contract = contracts.build_command_contract(
        command=command,
        profile=profile,
        config=config,
        loaded=loaded,
        plan=plan,
        requirement=requirement,
        evaluation=evaluation,
        transport=transport,
        extension_problems=tuple(problems),
        dry_run=not args.live,
        approved_consent_scopes=approved_scopes,
        consent_approval_source=approval_source,
        consent_mode=consent_mode,
        operator=approval_operator,
        target=args.target or (f"PR #{args.pr}" if args.pr is not None else None),
        reviewer_override=args.reviewers,
        review_comments=args.review_comments,
        jury=args.jury,
        no_jury=args.no_jury,
        jury_advisory=args.jury_advisory,
        issue_title=args.issue_title,
        issue_body=args.issue_body,
        issue_labels=_issue_labels(args),
    )
    consent_ok, consent_message = consent.assert_operator_consent(contract["operator_consent"])
    if not consent_ok:
        if args.json:
            print(json.dumps({"contract": contract}, indent=2, sort_keys=True))
        else:
            print(consent_message, file=sys.stderr)
        return 1
    intake_record = contract["issue_intake"]
    if args.live and _issue_context_provided(args) and intake_record:
        if not intake_record["can_mutate_code"]:
            if args.json:
                print(json.dumps({"contract": contract}, indent=2, sort_keys=True))
            else:
                print(f"issue intake: {intake_record['status']} — {intake_record['reason']}",
                      file=sys.stderr)
                for question in intake_record["questions"]:
                    print(f"  question: {question}", file=sys.stderr)
            return 1
    if args.live and args.append_ledger and args.capture_status is None:
        message = "--capture-status is required when --live --append-ledger is used"
        if args.json:
            print(json.dumps({"contract": contract, "error": message}, indent=2,
                             sort_keys=True))
        else:
            print(message, file=sys.stderr)
        return 1
    run_context_warnings = _run_context_warnings(args)
    if args.live and args.append_ledger and args.strict_run_context and run_context_warnings:
        message = "; ".join(run_context_warnings)
        if args.json:
            print(json.dumps({"contract": contract, "error": message}, indent=2,
                             sort_keys=True))
        else:
            print(message, file=sys.stderr)
        return 1

    changed = git.changed_files(config.base_branch, "HEAD", cwd=args.root)
    # unreachable: orch.build_plan() above already calls plan_gates and surfaces GateError.
    try:
        specs = gates.plan_gates(config, loaded)
    except gates.GateError as exc:  # pragma: no cover - defensive duplicate of the build_plan guard
        print(str(exc), file=sys.stderr)
        return 1
    diff_text = git.diff(config.base_branch, "HEAD", cwd=args.root)
    outcomes = gates.run_gates(specs, _gate_runner(args.root, diff_text))
    verdict = fnd.summarize(gates.collect_findings(outcomes))
    ci_conclusion = (
        github.ci_conclusion(args.pr, cwd=args.root)
        if args.pr and transport.name == "gh"
        else None
    )

    a = ship.assess(
        changed_files=changed,
        gate_verdict=verdict,
        tier3_globs=config.knobs.tier3_globs,
        docs_globs=config.knobs.docs_gate_paths,
        timezone=config.timezone,
        merge_window=config.merge_window,
        merge_window_mode=config.merge_window_mode,
        ci_conclusion=ci_conclusion,
        is_blocker=args.hotfix,
        reviewer_override=args.reviewers,
        review_comments=args.review_comments,
        gates=config.gates,
        policy_pack=config.policy_pack,
        jury=args.jury,
        no_jury=args.no_jury,
        jury_advisory=args.jury_advisory,
    )
    contract["review_merge_contract"] = a.review_contract
    ledger_path = ledger.resolve_path(args.root, config)
    try:
        existing_ledger_records = ledger.read_records(ledger_path)
    except ledger.LedgerError as exc:
        print(f"invalid ledger {ledger_path}: {exc}", file=sys.stderr)
        return 1
    try:
        run_control_events = (
            _read_json_list(args.run_events_file, missing_ok=True)
            if args.run_events_file else []
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    run_control_report = runcontrols.evaluate_run_controls(
        run_control_events,
        max_work_units=args.max_rounds or runcontrols.DEFAULT_RUN_BUDGET,
    )
    ledger_record = ledger.build_ship_run_record(
        command=command,
        base_branch=config.base_branch,
        changed_files=changed,
        outcomes=outcomes,
        verdict=verdict,
        assessment=a,
        issue_intake=contract.get("issue_intake"),
        target=args.target or (f"PR #{args.pr}" if args.pr is not None else None),
        run_id=args.run_id,
        issue_number=args.issue,
        pr_number=args.ledger_pr or args.pr,
        branch=args.branch,
        head_sha=args.head_sha,
        capture_status=args.capture_status,
        capture_reason=args.capture_reason,
        issue_title=args.issue_title,
        issue_labels=_issue_labels(args),
        existing_records=existing_ledger_records,
        config=config,
        implementer=args.implementer,
        reviewer_agents=args.reviewer_agent,
        tester=args.tester,
        host_agent=args.host_agent,
        transport=args.transport or transport.name,
        profile=profile,
        jury_mode=a.review_contract["jury"]["mode"],
        consent_status=contract["operator_consent"]["status"],
        consent_scopes=contract["operator_consent"]["effective_approved_scope"],
        run_controls=run_control_report,
    )
    try:
        ledger_record = ledger.sanitize_record(ledger_record, config)
    except ledger.redaction.RedactionError as exc:
        message = f"capture redaction policy invalid; skipping ledger append: {exc}"
        if args.append_ledger and args.live:
            if args.json:
                print(json.dumps({"contract": contract, "error": message}, indent=2,
                                 sort_keys=True))
            else:
                print(message, file=sys.stderr)
            return 1
        fallback = ledger.redaction.sanitize(
            ledger_record,
            ledger.redaction.policy_from_config(config, strict=False),
        )
        ledger_record = dict(fallback.value)
        ledger_record["redaction"] = {
            "schema_version": ledger.redaction.REDACTION_SCHEMA_VERSION,
            "status": "partial",
            "reason": "invalid-policy",
            "rules": fallback.audit["rules"],
            "redaction_count": fallback.audit["redaction_count"],
        }
    ledger_result = {
        "contract": contract["run_ledger"],
        "path": str(ledger_path),
        "appended": False,
        "record": ledger_record,
        "warnings": run_context_warnings,
    }
    if args.append_ledger and args.live:
        ledger.append_record(ledger_path, ledger_record)
        ledger_result["appended"] = True

    if args.json:
        print(json.dumps({
            "contract": contract,
            "result": contracts.ship_result_as_dict(
                changed_files=changed,
                outcomes=outcomes,
                verdict=verdict,
                assessment=a,
                issue_intake=contract.get("issue_intake"),
                run_ledger=ledger_result,
            ),
        }, indent=2, sort_keys=True))
        if run_control_report["hard_halt"]:
            return 1
        return 0 if a.merge.action != "block" else 1

    name = config.repo or config.extends
    print(f"keel {command} — {name}  (base {config.base_branch})")
    print(f"  changed files : {len(changed)}")
    print(f"  profile       : {contract['workflow_profile']['profile']}")
    print(f"  risk tier     : TIER-{a.tier}  → {a.reviewers} reviewer(s)")
    jury_state = a.review_contract["jury"]["mode"]
    print(f"  review posts  : {a.review_contract['posting']['mode']}")
    print(f"  jury          : {jury_state} ({a.review_contract['jury']['reason']})")
    window = "OPEN" if a.window_open else f"CLOSED ({config.merge_window_mode}, night no-merge)"
    print(f"  merge window  : {window}")
    ci_str = "unknown" if a.ci_ok is None else ("passing" if a.ci_ok else "FAILING")
    print(f"  ci            : {ci_str}")
    print(f"  github        : {transport.name}")
    print(f"  consent       : {contract['operator_consent']['status']}")
    print(f"  intake        : {intake_record['status']}")
    print(f"  run ledger    : {ledger_result['path']}")
    print(f"  run controls  : {run_control_report['status']}")
    if args.append_ledger:
        print(f"  ledger append : {'yes' if ledger_result['appended'] else 'dry-run/no-live'}")
    for warning in run_context_warnings:
        print(f"  run context   : warning: {warning}")
    if intake_record["questions"]:
        print(f"  questions     : {len(intake_record['questions'])}")
    if transport.degraded:
        print(f"  github degraded: {', '.join(transport.degraded)}")
    for o in outcomes:
        print(f"  gate {o.gate:<14} {'ok' if o.ok else 'FAIL'}")
    if evaluation.missing_optional:
        print(f"  degraded opt. : {', '.join(evaluation.missing_optional)}")
    if a.halted:  # pragma: no cover - display only; logic covered in ship.assess tests
        print("  pipeline      : HALTED (merge window paused)")
    if a.bypassed_window:  # pragma: no cover - display only; logic covered in ship.assess
        print("  audit         : hotfix bypassed the merge window")
    print(f"  decision      : {a.merge.action.upper()} — {a.merge.reason}")
    print("  note: dry assessment; live merge (s10) needs a configured runner (git + gh auth).")
    if run_control_report["hard_halt"]:
        return 1
    return 0 if a.merge.action != "block" else 1


def _cmd_ledger(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    contract = ledger.ledger_contract_as_dict(config)
    path = ledger.resolve_path(args.root, config)
    try:
        records = ledger.read_records(path)
    except ledger.LedgerError as exc:
        print(f"invalid ledger {path}: {exc}", file=sys.stderr)
        return 1
    if args.limit is not None:
        records = records[-args.limit:]
    payload = {
        "contract": contract,
        "path": str(path),
        "status": "present" if path.exists() else "missing",
        "records": records,
        "record_count": len(records),
        "capture_health": ledger.capture_health_summary(records),
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"keel ledger — {payload['status']}  {path}")
        print(f"  schema        : {contract['schema_version']}")
        print(f"  records       : {payload['record_count']}")
        print(f"  missing       : {contract['missing_handling']}")
        print(f"  capture       : {payload['capture_health']['status']}")
        print(f"  capture gaps  : {payload['capture_health']['counts']['needs_reconcile']}")
    return 0


def _cmd_capture_verify(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    ledger_path = ledger.resolve_path(args.root, config)
    try:
        records = ledger.read_records(ledger_path)
    except ledger.LedgerError as exc:
        print(f"invalid ledger {ledger_path}: {exc}", file=sys.stderr)
        return 1
    report = capture.verify_session(records, args.merged_pr)
    payload = {
        "contract": capture.contract_as_dict(config),
        "ledger_path": str(ledger_path),
        "verification": report,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"keel capture-verify — {report['status']}  {ledger_path}")
        for result in report["results"]:
            state = "ok" if result["ok"] else "FAIL"
            print(
                f"  {state:>4}  PR #{result['pr']}  "
                f"{result['status']}  {result['reason'] or '-'}"
            )
    return 0 if report["status"] == "complete" else 1


def _cmd_capture_reconcile(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    ledger_path = ledger.resolve_path(args.root, config)
    try:
        records = ledger.read_records(ledger_path)
    except ledger.LedgerError as exc:
        print(f"invalid ledger {ledger_path}: {exc}", file=sys.stderr)
        return 1
    linked_issues: dict[int, list[int]] = {}
    for pr, issue in args.linked_issue:
        linked_issues.setdefault(pr, []).append(issue)
    merged_prs = [
        {"number": pr, "issue_numbers": linked_issues.get(pr, [])}
        for pr in args.merged_pr
    ]
    try:
        plan = capture.reconcile_session(
            records,
            merged_prs,
            config=config,
            capture_capability_available=args.capture_capability == "available",
        )
    except capture.CaptureError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    payload = {
        "contract": capture.contract_as_dict(config)["reconcile"],
        "mode": "live-plan" if args.live else "dry-run",
        "no_mutations": True,
        "ledger_path": str(ledger_path),
        "reconcile": plan,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"keel capture-reconcile — {plan['status']}  {ledger_path}")
        for result in plan["results"]:
            print(f"  PR #{result['pr']}  {result['status']}  {result['reason']}")
            for action in result["actions"]:
                print(f"    DRY-RUN: {action['type']}  {action['idempotency_key']}")
    return 0 if plan["status"] != "blocked" else 1


def _cmd_step_verify(args: argparse.Namespace) -> int:
    try:
        handoff = _read_json_object(args.handoff_file)
        evidence_report = _read_json_object(args.evidence_report)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    review_contract = ship.resolve_review_contract(
        tier=None,
        reviewer_override=args.reviewers,
        review_comments=args.review_comments,
        gates=(),
        policy_pack={},
        jury=args.jury,
        no_jury=args.no_jury,
        jury_advisory=args.jury_advisory,
    )
    try:
        report = stepverifier.verify_step_completion(
            step_id=args.step,
            handoff=handoff,
            evidence_report=evidence_report,
            review_contract=review_contract,
            dry_run=args.dry_run,
            enforced=not args.not_enforced,
        )
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    payload = {
        "contract": stepverifier.contract_as_dict(
            review_contract,
            dry_run=args.dry_run,
            enforced=not args.not_enforced,
        ),
        "verification": report,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"keel step-verify — {report['status']}  {args.step}")
        if report["missing"]:
            print(f"  missing       : {', '.join(report['missing'])}")
        print(f"  required      : {len(report['required_evidence'])}")
    return 0 if report["status"] == "pass" else 1


def _cmd_runcontrols(args: argparse.Namespace) -> int:
    try:
        events = _read_json_list(args.events_file, missing_ok=True)
        event = _event_from_args(args)
        step_caps = _step_caps_from_args(args.step_cap)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if event:
        events.append(event)
        if not args.dry_run:
            _write_json_list(args.events_file, events)
    report = runcontrols.evaluate_run_controls(
        events,
        max_work_units=args.max_work_units,
        default_step_cap=args.default_step_cap,
        step_caps=step_caps,
        identical_action_threshold=args.identical_action_threshold,
        alternating_diff_window=args.alternating_diff_window,
    )
    payload = {
        "contract": runcontrols.contract_as_dict(),
        "path": args.events_file,
        "appended": bool(event) and not args.dry_run,
        "event": event,
        "run_controls": report,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"keel runcontrols — {report['status']}  {args.events_file}")
        print(f"  events        : {report['summary']['event_count']}")
        print(f"  work units    : {report['summary']['work_units']}")
        if report["reason"]:
            reason = report["reason"]
            print(f"  halt          : {reason['reason']} ({reason['scope']})")
    return 0 if report["status"] == "pass" else 1


def _cmd_post_comment(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    marker = _comment_artifact_marker(args.artifact)
    try:
        target_kind, target_number = _parse_comment_target(args.target)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        body = Path(args.body_file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read --body-file {args.body_file}: {exc}", file=sys.stderr)
        return 1
    if marker not in body:
        print(
            f"body for artifact {args.artifact} must contain marker {marker}",
            file=sys.stderr,
        )
        return 1
    if _looks_like_body_file_literal(body):
        print(
            "body appears to be a literal @/path reference; pass rendered markdown, "
            "not a shell-expanded placeholder",
            file=sys.stderr,
        )
        return 1

    report = runtime.detect(args.root)
    evaluation = runtime.evaluate(
        runtime.CapabilityRequirement(required=("gh", "gh-auth")), report
    )
    transport = github_transport.resolve(report)
    if not evaluation.ok or not transport.supports("comments"):
        print(evaluation.render(), file=sys.stderr)
        print(transport.render(), file=sys.stderr)
        return 1
    try:
        owner_repo = _owner_repo(config)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        existing = _gh_json_list(
            ["repos", owner_repo, "issues", str(target_number), "comments"], cwd=args.root
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    match = _find_comment_match(existing, marker=marker, run_id=args.run_id)
    payload: dict[str, object] = {
        "schema_version": "keel.post-comment.v1",
        "target": {"kind": target_kind, "number": target_number},
        "artifact": args.artifact,
        "marker": marker,
        "transport": transport.name,
        "run_id": args.run_id,
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        payload["action"] = "edit" if match else "post"
        payload["comment_id"] = match.get("id") if match else None
        return _finish_post_comment(args, payload, code=0)

    if match:
        comment_id = match.get("id")
        if not isinstance(comment_id, int):
            print("matching comment is missing an integer id", file=sys.stderr)
            return 1
        result = github.edit_issue_comment(owner_repo, comment_id, body, cwd=args.root)
        payload["action"] = "edited"
        payload["comment_id"] = comment_id
    else:
        result = github.post_issue_comment(owner_repo, target_number, body, cwd=args.root)
        payload["action"] = "posted"
    if not result.ok:
        print(result.output.strip() or "gh comment mutation failed", file=sys.stderr)
        return 1
    try:
        response = json.loads(result.output or "{}")
    except json.JSONDecodeError:
        response = {}
    if isinstance(response, dict):
        payload["comment_id"] = response.get("id", payload.get("comment_id"))
        payload["html_url"] = response.get("html_url")
    return _finish_post_comment(args, payload, code=0)


def _cmd_evidence_verify(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        artifacts = _load_evidence_artifacts(args, config)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    changed_files = artifacts["changed_files"]
    tier = (
        classify.tier_for_files(
            changed_files,
            tier3_globs=config.knobs.tier3_globs,
            docs_globs=config.knobs.docs_gate_paths,
        )
        if changed_files else None
    )
    review_contract = ship.resolve_review_contract(
        tier=tier,
        reviewer_override=args.reviewers,
        review_comments=args.review_comments,
        gates=config.gates,
        policy_pack=config.policy_pack,
        jury=args.jury,
        no_jury=args.no_jury,
        jury_advisory=args.jury_advisory,
    )
    gate_label = args.gate_label or config.knobs.evidence_gate_label
    waiver_label = args.waiver_label or evidence.DEFAULT_WAIVER_LABEL
    gate = evidence.gate_decision(
        artifacts["pr_labels"],
        gate_label,
        waiver_label=waiver_label,
        head_ref=artifacts.get("head_ref"),
        pr_comments=artifacts["pr_comments"],
        pr_reviews=artifacts["pr_reviews"],
    )
    enforced = gate["enforced"]
    report = evidence.verify(
        review_contract,
        pr_comments=artifacts["pr_comments"],
        issue_comments=artifacts["issue_comments"],
        pr_reviews=artifacts["pr_reviews"],
        pr_body=artifacts["pr_body"],
        head_sha=artifacts["head_sha"],
        dry_run=args.dry_run,
        enforced=enforced,
        deferrals=tuple(args.deferral or ()),
    )
    payload = {
        "contract": evidence.contract_as_dict(
            review_contract,
            dry_run=args.dry_run,
            enforced=enforced,
            deferrals=tuple(args.deferral or ()),
        ),
        "gate_label": gate_label,
        "waiver_label": waiver_label,
        "gate": gate,
        "enforced": enforced,
        "pr_labels": artifacts["pr_labels"],
        "pull_request": args.pr,
        "issue": artifacts["issue"],
        "head_ref": artifacts.get("head_ref"),
        "head_sha": artifacts["head_sha"],
        "changed_files": artifacts["changed_files"],
        "verification": report,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"keel evidence-verify — {report['status']}  PR #{args.pr}")
        print(f"  issue         : {artifacts['issue'] or 'not resolved'}")
        print(f"  dry-run       : {str(args.dry_run).lower()}")
        if not enforced:
            print(f"  enforced      : false ({gate['reason']})")
            return 0
        print(f"  enforced      : true ({gate['reason']})")
        print(f"  required      : {report['required_count']}")
        if report["missing"]:
            print(f"  missing       : {', '.join(report['missing'])}")
        for result in report["results"]:
            state = "ok" if result["ok"] else "FAIL"
            suffix = " (deferred)" if result["deferred"] else ""
            print(f"  {state:>4}  {result['id']}{suffix}")
    return 0 if report["status"] == "pass" else 1


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    checkpoint_path = checkpoint.resolve_path(args.root, config)
    ledger_path = ledger.resolve_path(args.root, config)
    try:
        checkpoint_record = checkpoint.read_checkpoint(checkpoint_path)
    except checkpoint.CheckpointError as exc:
        print(f"invalid checkpoint {checkpoint_path}: {exc}", file=sys.stderr)
        return 1
    try:
        ledger_records = ledger.read_records(ledger_path)
    except ledger.LedgerError as exc:
        print(f"invalid ledger {ledger_path}: {exc}", file=sys.stderr)
        return 1
    snapshot = status.build_status_snapshot(
        config=config,
        checkpoint_record=checkpoint_record,
        ledger_records=ledger_records,
    )
    payload = {
        "contract": status.status_contract_as_dict(config),
        "checkpoint_path": str(checkpoint_path),
        "ledger_path": str(ledger_path),
        "snapshot": snapshot,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(status.render_status(snapshot))
    return 0


def _cmd_checkpoint(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    contract = checkpoint.checkpoint_contract_as_dict(config)
    path = checkpoint.resolve_path(args.root, config)
    if args.write:
        try:
            record = checkpoint.build_checkpoint_record(
                run_id=args.run_id,
                command=args.checkpoint_command_name,
                current_step=args.step,
                base_branch=config.base_branch,
                target=args.target,
                issue_queue=args.issue_queue,
                active_issue=args.active_issue,
                branch=args.branch,
                worktree=args.worktree,
                pull_request=args.pull_request,
                head_sha=args.head_sha,
                completed_steps=args.completed_step,
                last_gate=args.last_gate,
                last_review=args.last_review,
                last_check=args.last_check,
                merge_state=args.merge_state,
                capture_state=args.capture_state,
                close_state=args.close_state,
                stop_reason=args.stop_reason,
            )
            checkpoint.write_checkpoint(path, record)
        except checkpoint.CheckpointError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    else:
        try:
            record = checkpoint.read_checkpoint(path)
        except checkpoint.CheckpointError as exc:
            print(f"invalid checkpoint {path}: {exc}", file=sys.stderr)
            return 1
    payload = {
        "contract": contract,
        "path": str(path),
        "status": "present" if record is not None else "missing",
        "checkpoint": record,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"keel checkpoint — {payload['status']}  {path}")
        print(f"  schema        : {contract['schema_version']}")
        if record:
            print(f"  run           : {record['run_id']}")
            print(f"  step          : {record['position']['current_step']}")
            print(f"  safe boundary : {record['resume']['safe_boundary']}")
    return 0


def _cmd_resume(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    contract = checkpoint.checkpoint_contract_as_dict(config)
    path = checkpoint.resolve_path(args.root, config)
    try:
        record = checkpoint.read_checkpoint(path)
        plan = checkpoint.resume_plan_as_dict(
            record,
            live_pr_state=args.live_pr_state,
            live_worktree_state=args.live_worktree_state,
        )
    except checkpoint.CheckpointError as exc:
        print(f"invalid checkpoint {path}: {exc}", file=sys.stderr)
        return 1
    payload = {
        "contract": contract,
        "path": str(path),
        "resume_plan": plan,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"keel resume — {plan['status']}  {path}")
        print(f"  can resume    : {str(plan['can_resume']).lower()}")
        print(f"  next step     : {plan['next_step'] or '-'}")
        print(f"  action        : {plan['resume_action'] or '-'}")
        print(f"  reason        : {plan['reason']}")
        for warning in plan["warnings"]:
            print(f"  warning       : {warning}")
    return 0 if plan["status"] != "ambiguous" else 1


def _cmd_standalone(args: argparse.Namespace) -> int:
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
        _ci_check_capability_requirement(config)
        if command == "ci-check"
        else _morning_capability_requirement(config)
        if command == "morning"
        else _scan_capability_requirement(command, config)
        if command in {"regression", "review-all-day"}
        else _capability_requirement(command, config, loaded, pr=getattr(args, "pr", None))
    )
    report = runtime.detect(args.root)
    evaluation = runtime.evaluate(requirement, report)
    if not evaluation.ok:
        print(evaluation.render(), file=sys.stderr)
        return 1
    transport = github_transport.resolve(report)
    target = _standalone_target(args)
    try:
        approved_scopes, approval_source, approval_operator, consent_mode = _approved_consent(
            args, config, _has_live_consent_scope(args, command, config, requirement, loaded)
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    try:
        plan = orch.build_plan(config, loaded)
    except gates.GateError as exc:
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
            print(json.dumps({"contract": contract, "result": result}, indent=2,
                             sort_keys=True))
        else:
            print(consent_message, file=sys.stderr)
        return 1
    intake_record = contract.get("issue_intake")
    if command == "implement" and getattr(args, "live", False) and _issue_context_provided(args):
        if intake_record and not intake_record["can_mutate_code"]:
            if args.json:
                print(json.dumps({"contract": contract, "result": result}, indent=2,
                                 sort_keys=True))
            else:
                print(f"issue intake: {intake_record['status']} — {intake_record['reason']}",
                      file=sys.stderr)
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
            linked_required = (
                session["wrap"]["workspace_preflight"]["must_run_from_linked_worktree"]
            )
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


def _issue_labels(args: argparse.Namespace) -> tuple[str, ...]:
    labels: list[str] = []
    for raw in getattr(args, "issue_label", ()) or ():
        labels.extend(part.strip() for part in raw.split(",") if part.strip())
    return tuple(dict.fromkeys(labels))


def _lock_root(root: str | Path) -> Path:
    return Path(root) / ".keel" / "state" / "locks"


def _finish_merge(args: argparse.Namespace, payload: dict[str, object], reason: str, *,
                  code: int) -> int:
    payload["reason"] = reason
    payload["status"] = "pass" if code == 0 else "fail"
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"keel merge — {payload['status']}  PR #{args.pr}")
        print(f"  reason : {reason}")
        lock_payload = payload.get("lock")
        if isinstance(lock_payload, dict):
            print(f"  lock   : {lock_payload.get('status')}")
        ci_payload = payload.get("ci")
        if isinstance(ci_payload, dict):
            print(f"  ci     : {ci_payload.get('state')}")
        evidence_payload = payload.get("evidence")
        if isinstance(evidence_payload, dict):
            verification = evidence_payload.get("verification")
            if isinstance(verification, dict):
                print(f"  evidence: {verification.get('status')}")
    return code


def _merge_snapshot(pr: int, *, cwd: str) -> dict[str, object]:
    result = github.pr_merge_snapshot(pr, cwd=cwd)
    if not result.ok:
        raise ValueError(f"unable to read PR merge snapshot: {result.output.strip()}")
    try:
        payload = json.loads(result.output or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("PR merge snapshot was not JSON") from exc
    rollup = payload.get("statusCheckRollup")
    rollup = rollup if isinstance(rollup, list) else []
    return {
        "head_sha": payload.get("headRefOid"),
        "merge_state": payload.get("mergeStateStatus") or "UNKNOWN",
        "ci": _ci_rollup_state(rollup),
    }


def _ci_rollup_state(rollup: list[object]) -> dict[str, object]:
    failures = {
        "ACTION_REQUIRED", "CANCELLED", "ERROR", "FAILURE",
        "SKIPPED", "STARTUP_FAILURE", "STALE", "TIMED_OUT",
    }
    pending_states = {"EXPECTED", "PENDING", "QUEUED", "REQUESTED", "WAITING", "IN_PROGRESS"}
    saw_pending = False
    saw_check = False
    for item in rollup:
        if not isinstance(item, dict):
            continue
        saw_check = True
        conclusion = item.get("conclusion")
        conclusion = conclusion.upper() if isinstance(conclusion, str) else ""
        status_value = item.get("status")
        status_value = status_value.upper() if isinstance(status_value, str) else ""
        if conclusion in failures:
            return {"state": "fail", "reason": conclusion}
        if not conclusion and status_value in pending_states:
            saw_pending = True
    if saw_pending:
        return {"state": "pending", "reason": "check-pending"}
    return {"state": "pass", "reason": "all-checks-passing" if saw_check else "no-checks"}


def _verify_merge_evidence(
    args: argparse.Namespace,
    config: cfg.ProjectConfig,
) -> dict[str, object]:
    evidence_args = argparse.Namespace(
        pr=args.pr,
        issue=args.issue,
        pr_body_file=None,
        pr_comments_json=None,
        issue_comments_json=None,
        pr_reviews_json=None,
        changed_file=(),
        head_sha=None,
        pr_label=(),
        dry_run=False,
        root=args.root,
    )
    artifacts = _load_evidence_artifacts(evidence_args, config)
    changed_files = artifacts["changed_files"]
    tier = (
        classify.tier_for_files(
            changed_files,
            tier3_globs=config.knobs.tier3_globs,
            docs_globs=config.knobs.docs_gate_paths,
        )
        if changed_files else None
    )
    review_contract = ship.resolve_review_contract(
        tier=tier,
        reviewer_override=args.reviewers,
        review_comments=args.review_comments,
        gates=config.gates,
        policy_pack=config.policy_pack,
        jury=args.jury,
        no_jury=args.no_jury,
        jury_advisory=args.jury_advisory,
    )
    gate_label = args.gate_label or config.knobs.evidence_gate_label
    waiver_label = getattr(args, "waiver_label", None) or evidence.DEFAULT_WAIVER_LABEL
    gate = evidence.gate_decision(
        artifacts["pr_labels"],
        gate_label,
        waiver_label=waiver_label,
        head_ref=artifacts.get("head_ref"),
        pr_comments=artifacts["pr_comments"],
        pr_reviews=artifacts["pr_reviews"],
    )
    enforced = gate["enforced"]
    report = evidence.verify(
        review_contract,
        pr_comments=artifacts["pr_comments"],
        issue_comments=artifacts["issue_comments"],
        pr_reviews=artifacts["pr_reviews"],
        pr_body=artifacts["pr_body"],
        head_sha=artifacts["head_sha"],
        enforced=enforced,
    )
    return {
        "gate_label": gate_label,
        "waiver_label": waiver_label,
        "gate": gate,
        "enforced": enforced,
        "verification": report,
        "head_sha": artifacts["head_sha"],
        "head_ref": artifacts.get("head_ref"),
        "changed_files": changed_files,
    }


def _validated_worktree_path(root: str | Path, worktree: str) -> Path:
    root_path = Path(root).resolve()
    raw = Path(worktree)
    candidate = (root_path / raw if not raw.is_absolute() else raw).resolve()
    if candidate == root_path or root_path not in candidate.parents:
        raise ValueError("worktree path must be nested under the repository root")
    listed = git.worktree_list(cwd=str(root_path))
    if not listed.ok:
        raise ValueError(f"unable to list registered worktrees: {listed.output.strip()}")
    registered = {
        Path(line.split(" ", 1)[1]).resolve()
        for line in listed.output.splitlines()
        if line.startswith("worktree ")
    }
    if candidate not in registered:
        raise ValueError("worktree path is not a registered git worktree")
    return candidate


def _issue_context_provided(args: argparse.Namespace) -> bool:
    return bool(
        (getattr(args, "issue_title", None) or "").strip()
        or (getattr(args, "issue_body", None) or "").strip()
        or _issue_labels(args)
    )


def _load_evidence_artifacts(
    args: argparse.Namespace,
    config: cfg.ProjectConfig,
) -> dict[str, object]:
    pr_body = _read_optional_text(args.pr_body_file)
    pr_comments = _read_optional_json_list(args.pr_comments_json)
    issue_comments = _read_optional_json_list(args.issue_comments_json)
    pr_reviews = _read_optional_json_list(args.pr_reviews_json)
    changed_files = list(getattr(args, "changed_file", ()) or ())
    head_sha = args.head_sha
    head_ref = getattr(args, "head_ref", None)
    issue_number = args.issue
    injected_labels = list(args.pr_label or ())
    pr_labels: list[str] = []
    using_fixtures = any(
        path is not None for path in (
            args.pr_body_file,
            args.pr_comments_json,
            args.issue_comments_json,
            args.pr_reviews_json,
        )
    )
    if args.dry_run:
        return {
            "pr_body": pr_body,
            "pr_comments": [],
            "issue_comments": [],
            "pr_reviews": [],
            "issue": issue_number,
            "head_sha": head_sha,
            "head_ref": head_ref,
            "changed_files": changed_files,
            "pr_labels": _dedupe_preserve(injected_labels),
        }
    if not using_fixtures:
        owner_repo = _owner_repo(config)
        pr = _gh_json(["repos", owner_repo, "pulls", str(args.pr)], cwd=args.root)
        pr_body = pr.get("body") if isinstance(pr.get("body"), str) else ""
        head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
        head_sha = head.get("sha") if isinstance(head.get("sha"), str) else None
        head_ref = head.get("ref") if isinstance(head.get("ref"), str) else None
        pr_labels = _label_names(pr.get("labels"))
        changed_files = _pr_changed_files(owner_repo, args.pr, cwd=args.root)
        pr_comments = _gh_json_list(
            ["repos", owner_repo, "issues", str(args.pr), "comments"], cwd=args.root
        )
        pr_reviews = _gh_json_list(
            ["repos", owner_repo, "pulls", str(args.pr), "reviews"], cwd=args.root
        )
        if issue_number is None:
            issue_number = _linked_issue_from_body(pr_body)
        if issue_number is not None:
            issue_comments = _gh_json_list(
                ["repos", owner_repo, "issues", str(issue_number), "comments"], cwd=args.root
            )
    elif issue_number is None:
        issue_number = _linked_issue_from_body(pr_body)
    return {
        "pr_body": pr_body,
        "pr_comments": pr_comments,
        "issue_comments": issue_comments,
        "pr_reviews": pr_reviews,
        "issue": issue_number,
        "head_sha": head_sha,
        "head_ref": head_ref,
        "changed_files": changed_files,
        "pr_labels": _dedupe_preserve([*pr_labels, *injected_labels]),
    }


def _label_names(labels: object) -> list[str]:
    if not isinstance(labels, list):
        return []
    return [
        label["name"]
        for label in labels
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    ]


def _dedupe_preserve(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _owner_repo(config: cfg.ProjectConfig) -> str:
    if not config.owner or not config.repo:
        raise ValueError("project config must define owner and repo for live evidence fetch")
    return f"{config.owner}/{config.repo}"


def _run_context_warnings(args: argparse.Namespace) -> list[str]:
    if not getattr(args, "live", False) or not getattr(args, "append_ledger", False):
        return []
    warnings = []
    if not _nonblank(getattr(args, "host_agent", None)):
        warnings.append("missing host_agent in live run context")
    return warnings


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _comment_artifact_marker(artifact: str) -> str:
    markers = {
        "closure-comment": closure.COMMENT_MARKER,
        "issue-update": artifacts.ISSUE_UPDATE_MARKER,
        "review-verdict": evidence.REVIEW_VERDICT_MARKER,
        "jury-verdict": evidence.JURY_VERDICT_MARKER,
        "extension-result": artifacts.EXTENSION_RESULT_MARKER,
        "step-handoff": artifacts.STEP_HANDOFF_MARKER,
        "run-control-halt": artifacts.RUN_CONTROL_HALT_MARKER,
    }
    return markers[artifact]


def _parse_comment_target(raw: str) -> tuple[str, int]:
    match = re.fullmatch(r"(?P<kind>issue|pr):(?P<number>[1-9]\d*)", raw.strip())
    if not match:
        raise ValueError("--target must use issue:<number> or pr:<number>")
    return match.group("kind"), int(match.group("number"))


def _looks_like_body_file_literal(body: str) -> bool:
    stripped = body.strip()
    return bool(re.fullmatch(r"@(?:/|~|\.\.?/).+", stripped))


def _find_comment_match(
    comments: list[dict[str, object]],
    *,
    marker: str,
    run_id: str | None,
) -> dict[str, object] | None:
    if run_id is None:
        return None
    matches: list[dict[str, object]] = []
    for comment in comments:
        body = comment.get("body")
        if not isinstance(body, str) or marker not in body:
            continue
        if not _comment_has_run_id(body, run_id):
            continue
        matches.append(comment)
    return matches[-1] if matches else None


def _comment_has_run_id(body: str, run_id: str) -> bool:
    patterns = (
        rf"^\s*(?:run[-_ ]?id)\s*:\s*{re.escape(run_id)}\s*$",
        rf"<!--\s*keel\.run-id:\s*{re.escape(run_id)}\s*-->",
    )
    return any(re.search(pattern, body, re.IGNORECASE | re.MULTILINE) for pattern in patterns)


def _finish_post_comment(args: argparse.Namespace, payload: dict[str, object], *, code: int) -> int:
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        target = payload["target"]
        if isinstance(target, dict):
            rendered_target = f"{target.get('kind')}:{target.get('number')}"
        else:
            rendered_target = str(target)
        print(f"keel post-comment — {payload.get('action')}  {rendered_target}")
        print(f"  artifact      : {payload.get('artifact')}")
        print(f"  transport     : {payload.get('transport')}")
        if payload.get("comment_id") is not None:
            print(f"  comment       : {payload.get('comment_id')}")
    return code


def _read_optional_text(path: str | None) -> str:
    return Path(path).read_text(encoding="utf-8") if path else ""


def _read_json_object(path: str) -> dict[str, object]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _read_json_list(path: str, *, missing_ok: bool = False) -> list[dict[str, object]]:
    p = Path(path)
    if missing_ok and not p.exists():
        return []
    value = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{path} must contain a JSON array of objects")
    return value


def _write_json_list(path: str, value: list[dict[str, object]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_optional_json_list(path: str | None) -> list[dict[str, object]]:
    if path is None:
        return []
    return _read_json_list(path)


def _event_from_args(args: argparse.Namespace) -> dict[str, object] | None:
    if args.event_json:
        event = _read_json_object(args.event_json)
    else:
        fields = {
            "step_id": args.step,
            "slot": args.slot,
            "action": args.action,
            "output_fingerprint": args.output_fingerprint,
            "diff_fingerprint": args.diff_fingerprint,
            "work_units": args.work_units,
        }
        event = {key: value for key, value in fields.items() if value not in (None, "")}
        if args.soft_failure:
            event["soft_failure"] = True
    return event or None


def _step_caps_from_args(values: list[str]) -> dict[str, int]:
    caps: dict[str, int] = {}
    for raw in values:
        if "=" not in raw:
            raise ValueError("--step-cap must use SLOT=N")
        slot, value = raw.split("=", 1)
        slot = slot.strip()
        try:
            parsed = int(value)
        except ValueError as exc:
            raise ValueError("--step-cap value must be a positive integer") from exc
        if not slot or parsed <= 0:
            raise ValueError("--step-cap must use SLOT=N with N > 0")
        caps[slot] = parsed
    return caps


def _gh_json(args: list[str], *, cwd: str) -> dict[str, object]:
    endpoint = "/".join(args)
    result = run_argv(["gh", "api", endpoint], cwd=cwd)
    if not result.ok:
        raise ValueError(f"gh api {endpoint} failed: {result.output.strip()}")
    value = json.loads(result.output or "{}")
    if not isinstance(value, dict):
        raise ValueError(f"gh api {endpoint} did not return a JSON object")
    return value


def _gh_json_list(args: list[str], *, cwd: str) -> list[dict[str, object]]:
    endpoint = "/".join(args)
    result = run_argv(["gh", "api", "--paginate", "--slurp", endpoint], cwd=cwd)
    if not result.ok:
        raise ValueError(f"gh api {endpoint} failed: {result.output.strip()}")
    value = json.loads(result.output or "[]")
    if value and all(isinstance(item, list) for item in value):
        value = [entry for page in value for entry in page]
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"gh api {endpoint} did not return a JSON array")
    return value


def _pr_changed_files(owner_repo: str, pr: int, *, cwd: str) -> list[str]:
    files = _gh_json_list(["repos", owner_repo, "pulls", str(pr), "files"], cwd=cwd)
    return [item["filename"] for item in files if isinstance(item.get("filename"), str)]


def _linked_issue_from_body(body: str) -> int | None:
    match = re.search(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(?P<n>[1-9]\d*)",
                      body, re.IGNORECASE)
    return int(match.group("n")) if match else None


def _approved_consent(
    args: argparse.Namespace,
    config: cfg.ProjectConfig,
    has_standing_scope: bool,
) -> tuple[tuple[str, ...], str, str | None, str]:
    mode = _consent_mode(args, config)
    explicit = tuple(getattr(args, "approve_scope", ()) or ())
    if explicit:
        return consent.normalize_scopes(explicit), "flag", getattr(args, "operator", None), mode
    if mode == "agent" or not getattr(args, "live", False):
        return (), "none", getattr(args, "operator", None), mode
    if mode == "explicit":
        return (), "none", getattr(args, "operator", None), mode
    if not has_standing_scope:
        return (), "none", getattr(args, "operator", None), mode
    env_value = os.environ.get("KEEL_APPROVE_SCOPE")
    if env_value:
        operator = os.environ.get("KEEL_OPERATOR")
        if not operator:
            raise ValueError("KEEL_OPERATOR is required when KEEL_APPROVE_SCOPE is used")
        return consent.normalize_scopes((env_value,)), "env", operator, mode
    if config.automation.approved_scopes:
        if not config.automation.operator:
            raise ValueError(
                "automation.operator is required when automation.approved_scopes is used"
            )
        return (
            consent.normalize_scopes(config.automation.approved_scopes),
            "config",
            config.automation.operator,
            mode,
        )
    return (), "none", getattr(args, "operator", None), mode


def _consent_mode(args: argparse.Namespace, config: cfg.ProjectConfig) -> str:
    mode = getattr(args, "consent_mode", None) or os.environ.get("KEEL_CONSENT_MODE")
    mode = mode or config.consent_mode
    if mode not in consent.CONSENT_MODES:
        raise ValueError(
            f"unknown consent mode {mode!r}; valid: {', '.join(consent.CONSENT_MODES)}"
        )
    return mode


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


def _ci_check_capability_requirement(config: cfg.ProjectConfig) -> runtime.CapabilityRequirement:
    optional = ["gh", "gh-auth"]
    if config.knobs.ci_workflows:
        optional.append("raw-actions-logs")
    return runtime.CapabilityRequirement(optional=tuple(optional))


def _morning_capability_requirement(config: cfg.ProjectConfig) -> runtime.CapabilityRequirement:
    required: list[str] = []
    optional: list[str] = ["gh", "gh-auth"]
    pack = config.policy_pack or {}
    health = pack.get("health_providers") if isinstance(pack.get("health_providers"), dict) else {}
    for provider in health.values():
        if not isinstance(provider, dict):
            continue
        required.extend(provider.get("required_capabilities") or ())
        optional.extend(provider.get("optional_capabilities") or ())
    return runtime.CapabilityRequirement(
        required=tuple(dict.fromkeys(required)),
        optional=tuple(dict.fromkeys(optional)),
    )


def _scan_capability_requirement(
    command: str,
    config: cfg.ProjectConfig,
) -> runtime.CapabilityRequirement:
    del config
    if command == "regression":
        return runtime.CapabilityRequirement(
            required=("git", "worktree"),
            optional=("gh", "gh-auth", "github-mcp", "parallel-subagents"),
        )
    return runtime.CapabilityRequirement(
        required=("git",),
        optional=("gh", "gh-auth", "github-mcp", "parallel-subagents"),
    )


def _cmd_capabilities(args: argparse.Namespace) -> int:
    report = runtime.detect(args.root)
    transport = github_transport.resolve(report)
    requirement = runtime.CapabilityRequirement()
    problems: list[str] = []
    if args.path:
        try:
            config = cfg.load_config(args.path)
        except FileNotFoundError:
            print(f"no such config: {args.path}", file=sys.stderr)
            return 1
        except cfg.ConfigError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        loaded, problems = load_extensions(config, args.root, strict=False)
        requirement = _capability_requirement(args.for_command, config, loaded, pr=args.pr)
    evaluation = runtime.evaluate(requirement, report)
    if args.json:
        print(json.dumps({
            "report": report.as_dict(),
            "github_transport": transport.as_dict(),
            "evaluation": evaluation.as_dict(),
            "extension_problems": problems,
        }, indent=2, sort_keys=True))
    else:
        print(report.render())
        print(transport.render())
        if args.path:
            print(evaluation.render())
        for prob in problems:
            print(f"  ! extension not loaded: {prob}", file=sys.stderr)
    return 0 if evaluation.ok else 1


def _cmd_project_commands(args: argparse.Namespace) -> int:
    try:
        config = cfg.load_config(args.path)
    except FileNotFoundError:
        print(f"no such config: {args.path}", file=sys.stderr)
        return 1
    except cfg.ConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    commands = project_commands.list_project_commands(config)
    if args.json:
        print(json.dumps({"project_commands": [command.as_dict() for command in commands]},
                         indent=2, sort_keys=True))
    else:
        if not commands:
            print("project commands: none")
            return 0
        print("project commands:")
        for command in commands:
            caps = []
            if command.required_capabilities:
                caps.append("required=" + ",".join(command.required_capabilities))
            if command.optional_capabilities:
                caps.append("optional=" + ",".join(command.optional_capabilities))
            cap_text = f" ({'; '.join(caps)})" if caps else ""
            runner = f" -> {command.command}" if command.command else ""
            print(f"  {command.name}{runner}{cap_text}")
    return 0


def _ask(prompt: str, default: str) -> str:  # pragma: no cover - interactive I/O
    raw = input(f"{prompt} [{default}]: " if default else f"{prompt}: ").strip()
    return raw or default


def _cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root)
    target = root / ".keel" / "project.yaml"
    if target.exists() and not args.force:
        print(
            f"{target} already exists; refusing to overwrite project config "
            "(use --force only if you intentionally want to replace it). "
            "Project extensions are not touched.",
            file=sys.stderr,
        )
        return 1
    stack = scaffold.detect_stack(root)
    repo = root.resolve().name
    try:
        if args.wizard:
            print(f"keel init wizard — detected stack: {stack} (Enter accepts each default)")
            text = scaffold.wizard(stack, _ask, repo=repo)
        else:
            text = scaffold.default_config(stack, repo=repo)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    if args.force:
        print(
            "warning: --force replaced .keel/project.yaml; .keel/extensions/ was not touched",
            file=sys.stderr,
        )
    print(f"wrote {target}  (detected stack: {stack})")
    return 0


def _render_scaffolded_config(root: Path, *, wizard: bool) -> tuple[str, str]:
    stack = scaffold.detect_stack(root)
    repo = root.resolve().name
    if wizard:
        print(f"keel setup wizard — detected stack: {stack} (Enter accepts each default)")
        return scaffold.wizard(stack, _ask, repo=repo), stack
    return scaffold.default_config(stack, repo=repo), stack


def _report_install(surface: str, installed: list[str], skipped: list[str]) -> None:
    for name in installed:
        print(f"  installed  [{surface}] {name}")
    for name in skipped:
        print(f"  skipped    [{surface}] {name}  (exists; --force to overwrite)")


def _report_adapter_rows(rows: dict[str, list[install.AdapterFileStatus]]) -> None:
    for surface, statuses in rows.items():
        for row in statuses:
            detail = f" — {row.detail}" if row.detail else ""
            print(f"  {row.status:<16} [{surface}] {row.name}  {row.path}{detail}")


def _project_only_commands(root: str | Path) -> set[str]:
    """Command names the project declares as project-only (never flagged as orphan).

    Reads ``.keel/project.yaml`` from ``root`` if present; absent or invalid config yields an
    empty set so the scan stays fail-soft and consumer-neutral.
    """
    path = Path(root) / ".keel" / "project.yaml"
    if not path.exists():
        return set()
    try:
        config = cfg.load_config(path)
    except cfg.ConfigError:
        return set()
    return {cmd.name for cmd in project_commands.list_project_commands(config)}


def _scan_orphans(root: str | Path, *, include_unmanaged: bool) -> list[install.OrphanFileStatus]:
    """Run the pure orphan/unmanaged scan with the default known-command set."""
    return install.scan_surface_orphans(
        root,
        known_commands=install.default_known_commands(),
        project_only=_project_only_commands(root),
        include_unmanaged=include_unmanaged,
    )


def _report_orphan_rows(orphans: list[install.OrphanFileStatus]) -> None:
    for row in orphans:
        print(f"  {row.category:<16} [{row.surface}] {row.name}  {row.path} — {row.reason}")


def _cmd_install_adapter(args: argparse.Namespace) -> int:
    if args.agent == "plugin":
        installed, skipped = install.install_plugin(args.root, force=args.force)
        _report_install("plugin", installed, skipped)
        print(f"{len(installed)} plugin command file(s) written under commands/ — "
              "install via /plugin marketplace add berkayturanci/keel; /plugin install keel")
        return 0
    if args.agent == "all":
        results = install.install_all(args.root, force=args.force)
    elif args.agent in install.TARGETS:
        results = {args.agent: install.install(args.agent, args.root, force=args.force)}
    else:
        print(f"unknown target {args.agent!r}; valid: all, plugin, "
              f"{', '.join(install.TARGETS)}",
              file=sys.stderr)
        return 1
    total = 0
    for surface, (installed, skipped) in results.items():
        _report_install(surface, installed, skipped)
        total += len(installed)
    print(f"{total} adapter(s) installed — Claude: /keel:<command>; "
          f"other agents: keel-<command> skill (.agents/skills/)")
    return 0


def _cmd_setup(args: argparse.Namespace) -> int:
    root = Path(args.root)
    target = root / ".keel" / "project.yaml"
    print(f"keel setup — {root}")

    if target.exists() and not args.force:
        print(f"  config       : using existing {target}")
        print("  extensions   : preserved (setup never deletes .keel/extensions/)")
    else:
        existed = target.exists()
        if existed:
            print(
                "warning: --force will replace .keel/project.yaml; "
                ".keel/extensions/ will not be touched",
                file=sys.stderr,
            )
        try:
            text, stack = _render_scaffolded_config(root, wizard=args.wizard)
        except ValueError as exc:
            print(f"  config       : failed ({exc})", file=sys.stderr)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        action = "overwrote" if existed else "wrote"
        print(f"  config       : {action} {target} (detected stack: {stack})")
        print("  extensions   : preserved (setup never deletes .keel/extensions/)")

    agent = args.adapter_target
    if agent == "all":
        results = install.install_all(root, force=args.force)
    else:
        results = {agent: install.install(agent, root, force=args.force)}
    total = 0
    for surface, (installed, skipped) in results.items():
        _report_install(surface, installed, skipped)
        total += len(installed)
    print(f"  adapters     : {total} installed, "
          f"{sum(len(skipped) for _, skipped in results.values())} skipped")

    try:
        config = cfg.load_config(target)
        loaded, _ = load_extensions(config, root, strict=True)
        plan = orch.build_plan(config, loaded)
    except (cfg.ConfigError, ExtensionError, gates.GateError) as exc:
        print(f"  validate     : failed ({exc})", file=sys.stderr)
        return 1

    print(f"  validate     : OK ({config.repo or '-'}, base {config.base_branch})")
    print("  plan         :")
    rendered = orch.render_plan(config, plan)
    for line in rendered.splitlines():
        print(f"    {line}")
    print("  next         : run /keel:ship <issue> or the matching keel-<command> skill.")
    return 0


def _cmd_adapter_status(args: argparse.Namespace) -> int:
    try:
        rows = install.adapter_status(args.agent, args.root)
    except KeyError:
        print(f"unknown target {args.agent!r}; valid: all, {', '.join(install.STATUS_TARGETS)}",
              file=sys.stderr)
        return 1
    orphans = _scan_orphans(args.root, include_unmanaged=args.include_unmanaged)
    if args.json:
        payload = {
            "adapters": {
                surface: [
                    {"surface": r.surface, "name": r.name, "path": r.path,
                     "status": r.status, "detail": r.detail}
                    for r in statuses
                ]
                for surface, statuses in rows.items()
            },
            "orphans": [o.as_dict() for o in orphans],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    _report_adapter_rows(rows)
    _report_orphan_rows(orphans)
    if orphans:
        stale = sum(1 for o in orphans if o.category == install.ORPHAN_STALE_MARKER)
        unmanaged = len(orphans) - stale
        print(f"  {len(orphans)} unmanaged keel-like file(s): "
              f"{stale} orphan (stale-marker), {unmanaged} unmanaged (no-marker) — advisory only")
    if not args.include_unmanaged:
        print("  note: pass --include-unmanaged to also scan for marker-less command surfaces")
    return 0


def _cmd_update_adapter(args: argparse.Namespace) -> int:
    try:
        rows = install.update_adapters(args.agent, args.root, dry_run=args.dry_run)
    except KeyError:
        print(f"unknown target {args.agent!r}; valid: all, {', '.join(install.TARGETS)}",
              file=sys.stderr)
        return 1
    _report_adapter_rows(rows)
    if args.dry_run:
        print("dry-run: no adapter files were written")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    args.agent = args.target
    print(f"keel sync — installed keel {__version__}")
    print("  package      : not upgraded by sync; upgrade keel-workflow with pip/pipx first")
    rc = _cmd_update_adapter(args)
    if rc == 0:
        orphans = _scan_orphans(args.root, include_unmanaged=False)
        if orphans:
            print(f"  orphans      : {len(orphans)} unmanaged keel-like file(s) found — "
                  "run keel adapter-status for details")
        print("  next         : run keel validate .keel/project.yaml --root .")
        print("  next         : run keel plan .keel/project.yaml --root .")
    return rc


def _parse_legacy_mapping(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError("use LEGACY=KEEL, for example ship=ship")
    legacy, command = (part.strip() for part in raw.split("=", 1))
    if not legacy or not command:
        raise argparse.ArgumentTypeError("legacy and keel command names must be non-empty")
    return legacy, command


def _cmd_install_legacy_wrappers(args: argparse.Namespace) -> int:
    matrix = Path(args.parity_matrix)
    if not matrix.exists():
        print(
            f"parity matrix not found: {matrix}; pass --parity-matrix after verifying rows",
            file=sys.stderr,
        )
        return 1
    ready_commands = install.parity_ready_commands(matrix.read_text(encoding="utf-8"))
    mappings = dict(args.command) if args.command else {
        command: command for command in sorted(ready_commands)
    }
    try:
        if args.agent == "all":
            results = install.install_all_legacy_wrappers(
                args.root,
                mappings=mappings,
                ready_commands=ready_commands,
                force=args.force,
            )
        elif args.agent in install.LEGACY_TARGETS:
            results = {
                args.agent: install.install_legacy_wrappers(
                    args.agent,
                    args.root,
                    mappings=mappings,
                    ready_commands=ready_commands,
                    force=args.force,
                )
            }
        else:
            print(
                f"unknown target {args.agent!r}; valid: all, "
                f"{', '.join(install.LEGACY_TARGETS)}",
                file=sys.stderr,
            )
            return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    total = 0
    for surface, (installed, skipped) in results.items():
        _report_install(f"legacy-{surface}", installed, skipped)
        total += len(installed)
    print(f"{total} legacy wrapper(s) installed — legacy commands now delegate to /keel:<command>")
    return 0


def _capability_requirement(
    command: str,
    config: cfg.ProjectConfig,
    loaded: dict[str, list],
    *,
    pr: int | None = None,
) -> runtime.CapabilityRequirement:
    req = runtime.CapabilityRequirement(
        required=config.knobs.required_capabilities,
        optional=config.knobs.optional_capabilities,
    )
    try:
        specs = gates.plan_gates(config, loaded)
    except gates.GateError:
        return req
    if project_command := project_commands.get_project_command(config, command):
        req = req.merged(runtime.CapabilityRequirement(
            required=project_command.required_capabilities,
            optional=project_command.optional_capabilities,
        ))

    command_gate_commands = {
        "run-gates", "ship", "pr-loop", "wrap", "work-block", "overnight",
        "implement", "coverage", "deps-audit", "flake-audit",
    }
    if command in command_gate_commands and any(s.kind == "command" for s in specs):
        req = req.merged(runtime.CapabilityRequirement(required=("shell",)))
    worktree_commands = {
        "ship", "pr-loop", "wrap", "work-block", "overnight", "implement"
    }
    github_read_commands = {
        "morning", "review-cycle", "triage", "stale-prs", "regression", "review-all-day",
        "coverage", "deps-audit", "flake-audit", "ci-check",
    }
    if command in worktree_commands:
        req = req.merged(runtime.CapabilityRequirement(required=("git", "worktree"),
                                                       optional=("gh", "gh-auth")))
    elif command in github_read_commands:
        req = req.merged(runtime.CapabilityRequirement(optional=("gh", "gh-auth")))
    for spec in specs:
        if spec.required_capabilities or spec.optional_capabilities:
            req = req.merged(runtime.CapabilityRequirement(
                required=spec.required_capabilities,
                optional=spec.optional_capabilities,
            ))
    return req


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="keel", description="keel — workflow core")
    parser.add_argument("--version", action="version", version=f"keel {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_version = sub.add_parser("version", help="print the keel version")
    p_version.set_defaults(func=_cmd_version)

    p_validate = sub.add_parser("validate", help="validate project config(s) against the schema")
    p_validate.add_argument("paths", nargs="+", help="path(s) to project.yaml")
    p_validate.add_argument("--root", default=None,
                            help="repo root; if set, also strict-validate extensions")
    p_validate.set_defaults(func=_cmd_validate)

    p_plan = sub.add_parser("plan", help="render the backbone plan for a project")
    p_plan.add_argument("path", help="path to project.yaml")
    p_plan.add_argument("--root", default=".", help="repo root for resolving extensions")
    p_plan.add_argument("--command", dest="command_contract", default="ship",
                        help="adapter command contract to include in JSON output")
    p_plan.add_argument("--profile", choices=("standard", "compound"), default="standard",
                        help="workflow profile for ship command contracts")
    p_plan.add_argument("--live", action="store_true",
                        help="render a live preflight contract and fail if consent is missing")
    p_plan.add_argument("--approve-scope", action="append", default=[],
                        help="approve a consent scope for this run; repeat or comma-separate")
    p_plan.add_argument("--operator", default=None,
                        help="operator identifier to include in an approved consent record")
    p_plan.add_argument("--consent-mode", choices=consent.CONSENT_MODES, default=None,
                        help="operator consent mode: explicit, standing, or agent")
    p_plan.add_argument("--target", default=None,
                        help="task target to include in the consent prompt and record")
    p_plan.add_argument("--issue-title", default=None,
                        help="issue title to include in the intake/readiness contract")
    p_plan.add_argument("--issue-body", default=None,
                        help="issue body markdown to include in the intake/readiness contract")
    p_plan.add_argument("--issue-label", action="append", default=[],
                        help="issue label for intake/readiness; repeat or comma-separate")
    p_plan.add_argument("--review-comments", choices=("inline", "summary"), default="inline",
                        help="review posting mode for ship-like command contracts")
    p_plan.add_argument("--reviewers", type=int, choices=(1, 2, 3), default=None,
                        help="override the resolved reviewer count")
    p_plan.add_argument("--jury", action="store_true",
                        help="enable the cross-vendor jury contract")
    p_plan.add_argument("--no-jury", action="store_true",
                        help="disable the cross-vendor jury contract")
    p_plan.add_argument("--jury-advisory", action="store_true",
                        help="run jury in advisory mode when enabled")
    p_plan.add_argument("--json", action="store_true", help="emit structured JSON")
    p_plan.set_defaults(func=_cmd_plan)

    p_run = sub.add_parser("run-gates", help="run a project's command gates")
    p_run.add_argument("path", help="path to project.yaml")
    p_run.add_argument("--root", default=".", help="repo root for commands + extensions")
    p_run.set_defaults(func=_cmd_run_gates)

    p_window = sub.add_parser("window", help="is the merge window open now?")
    p_window.add_argument("path", help="path to project.yaml")
    p_window.set_defaults(func=_cmd_window)

    p_claim = sub.add_parser("claim", help="claim a single-host keel resource")
    p_claim.add_argument("--root", default=".", help="repo root for the claim store")
    p_claim.add_argument("--owner", required=True, help="claim owner id")
    p_claim.add_argument("--json", action="store_true", help="emit structured JSON")
    p_claim.add_argument("resource", help="resource name, e.g. merge")
    p_claim.set_defaults(func=_cmd_claim)

    p_release = sub.add_parser("release", help="release a single-host keel resource")
    p_release.add_argument("--root", default=".", help="repo root for the claim store")
    p_release.add_argument("--owner", default=None, help="claim owner id; omit to release any")
    p_release.add_argument("--json", action="store_true", help="emit structured JSON")
    p_release.add_argument("resource", help="resource name, e.g. merge")
    p_release.set_defaults(func=_cmd_release)

    p_merge = sub.add_parser("merge", help="perform the fail-closed core-owned PR merge")
    p_merge.add_argument("path", help="path to project.yaml")
    p_merge.add_argument("--root", default=".", help="repo root for git/GitHub operations")
    p_merge.add_argument("--pr", type=_positive_int, required=True, help="pull request number")
    p_merge.add_argument("--issue", type=_positive_int, default=None,
                         help="linked issue number for evidence verification")
    p_merge.add_argument("--method", choices=("squash", "merge", "rebase"), default="squash",
                         help="GitHub merge method")
    p_merge.add_argument("--owner", default=None, help="resource claim owner id")
    p_merge.add_argument("--hotfix", action="store_true",
                         help="bypass the merge window with explicit consent")
    p_merge.add_argument("--dry-run", action="store_true", help="verify only; do not merge")
    p_merge.add_argument("--approve-scope", action="append", default=[],
                         help="approve a consent scope for this merge")
    p_merge.add_argument("--operator", default=None,
                         help="operator identifier for consent evidence")
    p_merge.add_argument("--consent-mode", choices=consent.CONSENT_MODES, default=None,
                         help="operator consent mode")
    p_merge.add_argument("--risk-tier", choices=consent.RISK_TIERS, default="tier-1",
                         help="risk tier for deterministic escalation evaluation")
    p_merge.add_argument("--trust-signal", choices=consent.TRUST_SIGNALS, default="medium",
                         help="trust signal for deterministic escalation evaluation")
    p_merge.add_argument("--retry-count", type=int, default=0,
                         help="retry count for deterministic escalation evaluation")
    p_merge.add_argument("--conflicting-sources", action="store_true",
                         help="mark conflicting sources for escalation evaluation")
    p_merge.add_argument("--changed-lines", type=int, default=0,
                         help="changed-line count for escalation evaluation")
    p_merge.add_argument("--escalation-side-effect", action="append", default=[],
                         help="additional side-effect signal for escalation evaluation")
    p_merge.add_argument("--review-comments", choices=("inline", "summary"), default="inline",
                         help="review posting mode for evidence verification")
    p_merge.add_argument("--reviewers", type=int, choices=(1, 2, 3), default=None,
                         help="override required reviewer count")
    p_merge.add_argument("--jury", action="store_true", help="enable jury evidence")
    p_merge.add_argument("--no-jury", action="store_true", help="disable jury evidence")
    p_merge.add_argument("--jury-advisory", action="store_true",
                         help="make jury advisory for evidence verification")
    p_merge.add_argument("--gate-label", default=None,
                         help="override evidence gate label")
    p_merge.add_argument("--json", action="store_true", help="emit structured JSON")
    p_merge.set_defaults(func=_cmd_merge)

    p_wr = sub.add_parser("worktree-remove", help="safely remove a registered nested worktree")
    p_wr.add_argument("--root", default=".", help="repo root")
    p_wr.add_argument("--json", action="store_true", help="emit structured JSON")
    p_wr.add_argument("worktree", help="worktree path to remove")
    p_wr.set_defaults(func=_cmd_worktree_remove)

    p_ledger = sub.add_parser("ledger", help="read the structured run ledger offline")
    p_ledger.add_argument("path", help="path to project.yaml")
    p_ledger.add_argument("--root", default=".", help="repo root for resolving the ledger path")
    p_ledger.add_argument("--limit", type=_positive_int, default=None,
                          help="return only the newest N records")
    p_ledger.add_argument("--json", action="store_true", help="emit structured JSON")
    p_ledger.set_defaults(func=_cmd_ledger)

    p_capture = sub.add_parser(
        "capture-verify",
        help="verify post-merge capture markers for merged PRs",
    )
    p_capture.add_argument("path", help="path to project.yaml")
    p_capture.add_argument("--root", default=".",
                           help="repo root for resolving the ledger path")
    p_capture.add_argument("--merged-pr", type=_positive_int, action="append", required=True,
                           help="merged PR number expected to have a capture marker")
    p_capture.add_argument("--json", action="store_true", help="emit structured JSON")
    p_capture.set_defaults(func=_cmd_capture_verify)

    p_reconcile = sub.add_parser(
        "capture-reconcile",
        help="plan idempotent post-merge capture reconciliation actions",
    )
    p_reconcile.add_argument("path", help="path to project.yaml")
    p_reconcile.add_argument("--root", default=".",
                             help="repo root for resolving the ledger path")
    p_reconcile.add_argument("--merged-pr", type=_positive_int, action="append", required=True,
                             help="merged PR number to reconcile")
    p_reconcile.add_argument(
        "--linked-issue",
        type=_parse_pr_issue_mapping,
        action="append",
        default=[],
        help="unambiguous PR-to-issue mapping as PR=ISSUE; repeat for multiple issues",
    )
    p_reconcile.add_argument(
        "--capture-capability",
        choices=("available", "unavailable"),
        default="unavailable",
        help="whether the capture extension capability is currently available",
    )
    p_reconcile.add_argument("--live", action="store_true",
                             help="label the output as a live reconciliation plan")
    p_reconcile.add_argument("--json", action="store_true", help="emit structured JSON")
    p_reconcile.set_defaults(func=_cmd_capture_reconcile)

    p_step = sub.add_parser(
        "step-verify",
        help="verify a persisted step handoff against the evidence report",
    )
    p_step.add_argument("--step", required=True, help="backbone step id, e.g. s7")
    p_step.add_argument("--handoff-file", required=True, help="JSON step handoff file")
    p_step.add_argument("--evidence-report", required=True,
                        help="JSON evidence verification report or verification block")
    p_step.add_argument("--review-comments", choices=("inline", "summary"),
                        default="inline", help="review posting mode in the ship contract")
    p_step.add_argument("--reviewers", type=int, choices=(1, 2, 3), default=None,
                        help="override the required reviewer verdict count")
    p_step.add_argument("--jury", action="store_true",
                        help="enable the cross-vendor jury requirement")
    p_step.add_argument("--no-jury", action="store_true",
                        help="disable the cross-vendor jury requirement")
    p_step.add_argument("--jury-advisory", action="store_true",
                        help="make an enabled jury advisory instead of required")
    p_step.add_argument("--dry-run", action="store_true",
                        help="verify with dry-run evidence requirements")
    p_step.add_argument("--not-enforced", action="store_true",
                        help="verify with evidence requirements disabled")
    p_step.add_argument("--json", action="store_true", help="emit structured JSON")
    p_step.set_defaults(func=_cmd_step_verify)

    p_rc = sub.add_parser(
        "runcontrols",
        help="append/evaluate deterministic run-control events",
    )
    p_rc.add_argument("events_file", help="JSON array run-events file")
    p_rc.add_argument("--event-json", default=None, help="single event JSON object to append")
    p_rc.add_argument("--step", default=None, help="event step id")
    p_rc.add_argument("--slot", default=None, help="event slot name")
    p_rc.add_argument("--action", default=None, help="event action")
    p_rc.add_argument("--output-fingerprint", default=None, help="event output fingerprint")
    p_rc.add_argument("--diff-fingerprint", default=None, help="event diff fingerprint")
    p_rc.add_argument("--work-units", type=int, default=None, help="event work-unit count")
    p_rc.add_argument("--soft-failure", action="store_true", help="mark event as soft failure")
    p_rc.add_argument("--max-work-units", type=int, default=runcontrols.DEFAULT_RUN_BUDGET,
                      help="run budget hard cap")
    p_rc.add_argument("--default-step-cap", type=int, default=runcontrols.DEFAULT_STEP_CAP,
                      help="default per-step/slot iteration cap")
    p_rc.add_argument("--step-cap", action="append", default=[],
                      help="override per-slot cap as SLOT=N; repeatable")
    p_rc.add_argument("--identical-action-threshold", type=int,
                      default=runcontrols.DEFAULT_IDENTICAL_THRESHOLD,
                      help="oscillation threshold for repeated identical actions")
    p_rc.add_argument("--alternating-diff-window", type=int,
                      default=runcontrols.DEFAULT_ALTERNATION_WINDOW,
                      help="oscillation window for alternating diff fingerprints")
    p_rc.add_argument("--dry-run", action="store_true",
                      help="evaluate without appending the event")
    p_rc.add_argument("--json", action="store_true", help="emit structured JSON")
    p_rc.set_defaults(func=_cmd_runcontrols)

    p_post = sub.add_parser(
        "post-comment",
        help="post or update a deterministic GitHub issue/PR artifact comment",
    )
    p_post.add_argument("path", help="path to project.yaml")
    p_post.add_argument("--root", default=".", help="repo root for GitHub operations")
    p_post.add_argument("--target", required=True, help="comment target as issue:N or pr:N")
    p_post.add_argument(
        "--artifact",
        required=True,
        choices=(
            "closure-comment",
            "issue-update",
            "review-verdict",
            "jury-verdict",
            "extension-result",
            "step-handoff",
            "run-control-halt",
        ),
        help="artifact contract expected in --body-file",
    )
    p_post.add_argument("--body-file", required=True, help="rendered markdown body to post")
    p_post.add_argument(
        "--run-id",
        default=None,
        help="update the existing same-marker comment for this run id when present",
    )
    p_post.add_argument("--dry-run", action="store_true", help="plan only; do not mutate GitHub")
    p_post.add_argument("--json", action="store_true", help="emit structured JSON")
    p_post.set_defaults(func=_cmd_post_comment)

    p_evidence = sub.add_parser(
        "evidence-verify",
        help="verify required pre-merge ship evidence artifacts",
    )
    p_evidence.add_argument("path", help="path to project.yaml")
    p_evidence.add_argument("--root", default=".", help="repo root for live gh fetches")
    p_evidence.add_argument("--pr", type=_positive_int, required=True,
                            help="pull request number to verify")
    p_evidence.add_argument("--issue", type=_positive_int, default=None,
                            help="linked issue number; otherwise inferred from PR body")
    p_evidence.add_argument("--review-comments", choices=("inline", "summary"),
                            default="inline", help="review posting mode in the ship contract")
    p_evidence.add_argument("--reviewers", type=int, choices=(1, 2, 3), default=None,
                            help="override the required reviewer verdict count")
    p_evidence.add_argument("--jury", action="store_true",
                            help="enable the cross-vendor jury requirement")
    p_evidence.add_argument("--no-jury", action="store_true",
                            help="disable the cross-vendor jury requirement")
    p_evidence.add_argument("--jury-advisory", action="store_true",
                            help="make an enabled jury advisory instead of required")
    p_evidence.add_argument("--dry-run", action="store_true",
                            help="emit the contract without requiring evidence")
    p_evidence.add_argument("--deferral", action="append", default=[],
                            help="explicitly defer an evidence id, kind, or all")
    p_evidence.add_argument("--pr-comments-json", default=None,
                            help="offline PR issue-comments JSON fixture")
    p_evidence.add_argument("--issue-comments-json", default=None,
                            help="offline linked-issue comments JSON fixture")
    p_evidence.add_argument("--pr-reviews-json", default=None,
                            help="offline PR reviews JSON fixture")
    p_evidence.add_argument("--pr-body-file", default=None,
                            help="offline PR body fixture, used only to infer linked issue")
    p_evidence.add_argument("--changed-file", action="append", default=[],
                            help="offline changed file path; repeat to derive tier from fixtures")
    p_evidence.add_argument("--head-sha", default=None,
                            help="offline PR head SHA used to bind verdict evidence")
    p_evidence.add_argument("--head-ref", default=None,
                            help="offline PR head branch used to detect ship provenance")
    p_evidence.add_argument("--pr-label", action="append", default=[],
                            help="inject a PR label name (repeatable); merged with live labels. "
                                 "A live PR fetch still runs unless an offline fixture flag is "
                                 "also supplied")
    p_evidence.add_argument("--gate-label", default=None,
                            help="override the legacy evidence arming label")
    p_evidence.add_argument("--waiver-label", default=None,
                            help="override the operator-applied evidence waiver label")
    p_evidence.add_argument("--json", action="store_true", help="emit structured JSON")
    p_evidence.set_defaults(func=_cmd_evidence_verify)

    p_status = sub.add_parser("status", help="show active/recent run progress")
    p_status.add_argument("path", help="path to project.yaml")
    p_status.add_argument("--root", default=".",
                          help="repo root for resolving checkpoint and ledger paths")
    p_status.add_argument("--json", action="store_true", help="emit structured JSON")
    p_status.set_defaults(func=_cmd_status)

    p_checkpoint = sub.add_parser("checkpoint", help="read or write the resumable checkpoint")
    p_checkpoint.add_argument("path", help="path to project.yaml")
    p_checkpoint.add_argument("--root", default=".",
                              help="repo root for resolving the checkpoint path")
    p_checkpoint.add_argument("--write", action="store_true",
                              help="write a checkpoint record instead of reading it")
    p_checkpoint.add_argument("--run-id", default="run",
                              help="run id for --write")
    p_checkpoint.add_argument("--checkpoint-command", dest="checkpoint_command_name",
                              choices=checkpoint.COMMANDS, default="ship",
                              help="workflow command being checkpointed")
    p_checkpoint.add_argument("--step", choices=checkpoint.STEP_IDS,
                              default="s0", help="current backbone step for --write")
    p_checkpoint.add_argument("--target", default=None,
                              help="target text to store in the checkpoint")
    p_checkpoint.add_argument("--issue-queue", type=_positive_int, action="append", default=[],
                              help="queued issue number; repeatable")
    p_checkpoint.add_argument("--active-issue", type=_positive_int, default=None,
                              help="active issue number")
    p_checkpoint.add_argument("--branch", default=None, help="recorded branch")
    p_checkpoint.add_argument("--worktree", default=None, help="recorded worktree path")
    p_checkpoint.add_argument("--pull-request", type=_positive_int, default=None,
                              help="recorded pull request number")
    p_checkpoint.add_argument("--head-sha", default=None, help="recorded head SHA")
    p_checkpoint.add_argument("--completed-step", choices=checkpoint.STEP_IDS,
                              action="append", default=[],
                              help="completed backbone step; repeatable")
    p_checkpoint.add_argument("--last-gate", default=None, help="last completed gate id")
    p_checkpoint.add_argument("--last-review", default=None,
                              help="last completed review marker")
    p_checkpoint.add_argument("--last-check", default=None,
                              help="last completed CI/check marker")
    p_checkpoint.add_argument("--merge-state", choices=checkpoint.MERGE_STATES,
                              default="not-started")
    p_checkpoint.add_argument("--capture-state", choices=checkpoint.CAPTURE_STATES,
                              default="not-started")
    p_checkpoint.add_argument("--close-state", choices=checkpoint.CLOSE_STATES,
                              default="not-started")
    p_checkpoint.add_argument("--stop-reason", default=None,
                              help="why the run stopped at this checkpoint")
    p_checkpoint.add_argument("--json", action="store_true", help="emit structured JSON")
    p_checkpoint.set_defaults(func=_cmd_checkpoint)

    p_resume = sub.add_parser("resume", help="render a dry-run resume plan")
    p_resume.add_argument("path", help="path to project.yaml")
    p_resume.add_argument("--root", default=".",
                          help="repo root for resolving the checkpoint path")
    p_resume.add_argument("--live-pr-state", choices=checkpoint.LIVE_PR_STATES,
                          default="unknown",
                          help="adapter-supplied live PR state for dry-run reconcile")
    p_resume.add_argument("--live-worktree-state", choices=checkpoint.LIVE_WORKTREE_STATES,
                          default="unknown",
                          help="adapter-supplied live worktree state for dry-run reconcile")
    p_resume.add_argument("--json", action="store_true", help="emit structured JSON")
    p_resume.set_defaults(func=_cmd_resume)

    _add_ship_parser(
        sub.add_parser("ship", help="dry ship assessment (tier, window, gates, decision)"),
        command="ship",
    )

    p_implement = sub.add_parser(
        "implement",
        help="standalone implement-step preflight contract",
    )
    p_implement.add_argument("path", help="path to project.yaml")
    p_implement.add_argument("issue", type=_positive_int, help="issue number to implement")
    p_implement.add_argument("--root", default=".", help="repo root for git and extensions")
    p_implement.add_argument("--delegate", default=None,
                             help="explicit implementer delegate override")
    p_implement.add_argument("--dry-run", action="store_true",
                             help="explicitly mark the assessment as non-mutating")
    p_implement.add_argument("--live", action="store_true",
                             help="render a live preflight and fail if consent is missing")
    p_implement.add_argument("--approve-scope", action="append", default=[],
                             help="approve a consent scope for this run; repeat or comma-separate")
    p_implement.add_argument("--operator", default=None,
                             help="operator identifier to include in an approved consent record")
    p_implement.add_argument("--consent-mode", choices=consent.CONSENT_MODES, default=None,
                             help="operator consent mode: explicit, standing, or agent")
    p_implement.add_argument("--target", default=None,
                             help="additional target text for the consent prompt")
    p_implement.add_argument("--issue-title", default=None,
                             help="issue title to include in the intake/readiness contract")
    p_implement.add_argument("--issue-body", default=None,
                             help="issue body markdown to include in the intake/readiness contract")
    p_implement.add_argument("--issue-label", action="append", default=[],
                             help="issue label for intake/readiness; repeat or comma-separate")
    p_implement.add_argument("--json", action="store_true", help="emit structured JSON")
    p_implement.set_defaults(func=_cmd_standalone, standalone_command="implement")

    p_ci = sub.add_parser(
        "ci-check",
        help="standalone read-only CI diagnostic preflight contract",
    )
    p_ci.add_argument("path", help="path to project.yaml")
    p_ci.add_argument("--root", default=".", help="repo root for capability checks")
    p_ci.add_argument("--pr", type=_positive_int, default=None,
                      help="PR number whose latest checks should be diagnosed")
    p_ci.add_argument("--target", default=None,
                      help="target text to include in the diagnostic contract")
    p_ci.add_argument("--json", action="store_true", help="emit structured JSON")
    p_ci.set_defaults(func=_cmd_standalone, standalone_command="ci-check")

    p_morning = sub.add_parser(
        "morning",
        help="standalone daily-brief preflight contract",
    )
    p_morning.add_argument("path", help="path to project.yaml")
    p_morning.add_argument("--root", default=".", help="repo root for capability checks")
    p_morning.add_argument("--since", default=None,
                           help="optional brief window start label or timestamp")
    p_morning.add_argument("--target", default=None,
                           help="target text to include in the morning contract")
    p_morning.add_argument("--dry-run", action="store_true",
                           help="explicitly mark the assessment as non-mutating")
    p_morning.add_argument("--live", action="store_true",
                           help="render a live preflight and fail if consent is missing")
    p_morning.add_argument("--approve-scope", action="append", default=[],
                           help="approve a consent scope for this run; repeat or comma-separate")
    p_morning.add_argument("--operator", default=None,
                           help="operator identifier to include in an approved consent record")
    p_morning.add_argument("--consent-mode", choices=consent.CONSENT_MODES, default=None,
                           help="operator consent mode: explicit, standing, or agent")
    p_morning.add_argument("--json", action="store_true", help="emit structured JSON")
    p_morning.set_defaults(func=_cmd_standalone, standalone_command="morning")

    p_wrap = sub.add_parser(
        "wrap",
        help="standalone session-wrap preflight contract",
    )
    p_wrap.add_argument("path", help="path to project.yaml")
    p_wrap.add_argument("title", nargs="?", default=None,
                        help="optional PR title override to include in the contract")
    p_wrap.add_argument("--root", default=".", help="repo root for git and capability checks")
    p_wrap.add_argument("--since", default=None,
                        help="optional session start label or timestamp")
    p_wrap.add_argument("--target", default=None,
                        help="target text to include in the wrap contract")
    p_wrap.add_argument("--dry-run", action="store_true",
                        help="explicitly mark the assessment as non-mutating")
    p_wrap.add_argument("--live", action="store_true",
                        help="render a live preflight and fail if consent is missing")
    p_wrap.add_argument("--approve-scope", action="append", default=[],
                        help="approve a consent scope for this run; repeat or comma-separate")
    p_wrap.add_argument("--operator", default=None,
                        help="operator identifier to include in an approved consent record")
    p_wrap.add_argument("--consent-mode", choices=consent.CONSENT_MODES, default=None,
                        help="operator consent mode: explicit, standing, or agent")
    p_wrap.add_argument("--json", action="store_true", help="emit structured JSON")
    p_wrap.set_defaults(func=_cmd_standalone, standalone_command="wrap")

    p_work_block = sub.add_parser(
        "work-block",
        help="standalone daytime multi-issue work-block preflight contract",
    )
    p_work_block.add_argument("path", help="path to project.yaml")
    p_work_block.add_argument("issues", nargs="*", type=_positive_int,
                              help="explicit issue numbers to process in order")
    p_work_block.add_argument("--root", default=".",
                              help="repo root for git and capability checks")
    p_work_block.add_argument("--queue", default=None,
                              help=(
                                  "project queue selector to use when no explicit issues "
                                  "are given"
                              ))
    p_work_block.add_argument("--max", dest="max_items", type=_positive_int, default=None,
                              help="maximum issues to attempt in this work block")
    p_work_block.add_argument("--hours", type=float, default=None,
                              help="optional time budget in hours")
    p_work_block.add_argument("--review-comments", choices=("inline", "summary"),
                              default="inline",
                              help="review posting mode to pass through ship handoffs")
    p_work_block.add_argument("--reviewers", type=int, choices=(1, 2, 3), default=None,
                              help="reviewer override for ship handoff contracts")
    p_work_block.add_argument("--target", default=None,
                              help="target text to include in the work-block contract")
    p_work_block.add_argument("--dry-run", action="store_true",
                              help="explicitly mark the assessment as non-mutating")
    p_work_block.add_argument("--live", action="store_true",
                              help="render a live preflight and fail if consent is missing")
    p_work_block.add_argument("--approve-scope", action="append", default=[],
                              help="approve a consent scope for this run; repeat or comma-separate")
    p_work_block.add_argument("--operator", default=None,
                              help="operator identifier to include in an approved consent record")
    p_work_block.add_argument("--consent-mode", choices=consent.CONSENT_MODES, default=None,
                              help="operator consent mode: explicit, standing, or agent")
    p_work_block.add_argument("--json", action="store_true", help="emit structured JSON")
    p_work_block.set_defaults(func=_cmd_standalone, standalone_command="work-block")

    p_overnight = sub.add_parser(
        "overnight",
        help="standalone overnight-session preflight contract",
    )
    p_overnight.add_argument("path", help="path to project.yaml")
    p_overnight.add_argument("hours", nargs="?", type=float, default=None,
                             help="optional time budget in hours")
    p_overnight.add_argument("--root", default=".", help="repo root for git and capability checks")
    p_overnight.add_argument("--max", dest="max_items", type=_positive_int, default=None,
                             help="maximum issues to attempt in this session")
    p_overnight.add_argument("--review-comments", choices=("inline", "summary"), default="inline",
                             help="review posting mode to pass through ship handoffs")
    p_overnight.add_argument("--reviewers", type=int, choices=(1, 2, 3), default=None,
                             help="reviewer override for ship handoff contracts")
    p_overnight.add_argument("--target", default=None,
                             help="target text to include in the overnight contract")
    p_overnight.add_argument("--dry-run", action="store_true",
                             help="explicitly mark the assessment as non-mutating")
    p_overnight.add_argument("--live", action="store_true",
                             help="render a live preflight and fail if consent is missing")
    p_overnight.add_argument("--approve-scope", action="append", default=[],
                             help="approve a consent scope for this run; repeat or comma-separate")
    p_overnight.add_argument("--operator", default=None,
                             help="operator identifier to include in an approved consent record")
    p_overnight.add_argument("--consent-mode", choices=consent.CONSENT_MODES, default=None,
                             help="operator consent mode: explicit, standing, or agent")
    p_overnight.add_argument("--json", action="store_true", help="emit structured JSON")
    p_overnight.set_defaults(func=_cmd_standalone, standalone_command="overnight")

    p_regression = sub.add_parser(
        "regression",
        help="standalone scan-and-file regression preflight contract",
    )
    p_regression.add_argument("path", help="path to project.yaml")
    p_regression.add_argument("--root", default=".", help="repo root for git/capability checks")
    p_regression.add_argument("--scope", choices=("full", "changed", "since"), default="full",
                              help="scan scope to include in the preflight target")
    p_regression.add_argument("--since", default=None,
                              help="optional ref or timestamp when --scope since is used")
    p_regression.add_argument("--target", default=None,
                              help="target text to include in the regression contract")
    p_regression.add_argument("--dry-run", action="store_true",
                              help="explicitly mark the assessment as non-mutating")
    p_regression.add_argument("--live", action="store_true",
                              help="render a live preflight and fail if consent is missing")
    p_regression.add_argument("--approve-scope", action="append", default=[],
                              help="approve a consent scope for this run; repeat or comma-separate")
    p_regression.add_argument("--operator", default=None,
                              help="operator identifier to include in an approved consent record")
    p_regression.add_argument("--consent-mode", choices=consent.CONSENT_MODES, default=None,
                              help="operator consent mode: explicit, standing, or agent")
    p_regression.add_argument("--json", action="store_true", help="emit structured JSON")
    p_regression.set_defaults(func=_cmd_standalone, standalone_command="regression")

    p_review_all_day = sub.add_parser(
        "review-all-day",
        help="standalone time-window scan-and-file preflight contract",
    )
    p_review_all_day.add_argument("path", help="path to project.yaml")
    p_review_all_day.add_argument("days", nargs="?", type=_positive_int, default=1,
                                  help="number of merge-window days to scan")
    p_review_all_day.add_argument("--root", default=".", help="repo root for git/capability checks")
    p_review_all_day.add_argument("--target", default=None,
                                  help="target text to include in the review-all-day contract")
    p_review_all_day.add_argument("--dry-run", action="store_true",
                                  help="explicitly mark the assessment as non-mutating")
    p_review_all_day.add_argument("--live", action="store_true",
                                  help="render a live preflight and fail if consent is missing")
    p_review_all_day.add_argument("--approve-scope", action="append", default=[],
                                  help=("approve a consent scope for this run; repeat or "
                                        "comma-separate"))
    p_review_all_day.add_argument("--operator", default=None,
                                  help=("operator identifier to include in an approved "
                                        "consent record"))
    p_review_all_day.add_argument("--consent-mode", choices=consent.CONSENT_MODES, default=None,
                                  help="operator consent mode: explicit, standing, or agent")
    p_review_all_day.add_argument("--json", action="store_true", help="emit structured JSON")
    p_review_all_day.set_defaults(func=_cmd_standalone, standalone_command="review-all-day")

    p_caps = sub.add_parser("capabilities", help="print runtime capability report")
    p_caps.add_argument("--root", default=".", help="repo root for capability checks")
    p_caps.add_argument("--project", dest="path", default=None,
                        help="optional project.yaml to evaluate requirements")
    p_caps.add_argument("--for", dest="for_command", default="ship",
                        help="command requirement to evaluate when --project is set")
    p_caps.add_argument("--pr", type=int, default=None,
                        help="PR number for ship capability requirements")
    p_caps.add_argument("--json", action="store_true", help="emit structured JSON")
    p_caps.set_defaults(func=_cmd_capabilities)

    p_proj = sub.add_parser("project-commands",
                            help="list project-provided commands declared by policy")
    p_proj.add_argument("path", help="path to project.yaml")
    p_proj.add_argument("--json", action="store_true", help="emit structured JSON")
    p_proj.set_defaults(func=_cmd_project_commands)

    p_init = sub.add_parser("init", help="scaffold a default .keel/project.yaml for this repo")
    p_init.add_argument("--root", default=".", help="repo root to scaffold into")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing config")
    p_init.add_argument("--wizard", action="store_true", help="prompt for values interactively")
    p_init.set_defaults(func=_cmd_init)

    p_setup = sub.add_parser(
        "setup",
        help="scaffold config, install adapters, validate, and render the plan",
    )
    p_setup.add_argument("--root", default=".", help="project root to set up")
    p_setup.add_argument(
        "--adapter-target",
        choices=("all", *install.TARGETS),
        default="all",
        help="adapter surface to install (default: all)",
    )
    p_setup.add_argument(
        "--force",
        action="store_true",
        help="overwrite existing config and generated adapters",
    )
    p_setup.add_argument("--wizard", action="store_true", help="prompt for config values")
    p_setup.set_defaults(func=_cmd_setup)

    p_ia = sub.add_parser("install-adapter", help="install the /keel:<command> adapters")
    p_ia.add_argument("agent",
                      help=f"'all', 'plugin', or one of: {', '.join(install.TARGETS)}")
    p_ia.add_argument("--root", default=".", help="project root to install into")
    p_ia.add_argument("--force", action="store_true", help="overwrite existing adapters")
    p_ia.set_defaults(func=_cmd_install_adapter)

    p_as = sub.add_parser("adapter-status", help="report generated adapter freshness")
    p_as.add_argument("agent", nargs="?", default="all",
                      help=f"'all' or one of: {', '.join(install.STATUS_TARGETS)}")
    p_as.add_argument("--root", default=".", help="project root to inspect")
    p_as.add_argument("--include-unmanaged", action="store_true",
                      help="also report marker-less command-like surfaces (heuristic, opt-in)")
    p_as.add_argument("--json", action="store_true",
                      help="emit adapter freshness + orphan/unmanaged findings as JSON")
    p_as.set_defaults(func=_cmd_adapter_status)

    p_ua = sub.add_parser("update-adapter", help="safely update generated adapters")
    p_ua.add_argument("agent", nargs="?", default="all",
                      help=f"'all' or one of: {', '.join(install.TARGETS)}")
    p_ua.add_argument("--root", default=".", help="project root to update")
    p_ua.add_argument("--dry-run", action="store_true", help="show planned updates only")
    p_ua.set_defaults(func=_cmd_update_adapter)

    p_sync = sub.add_parser("sync", help="sync generated adapters with the installed keel package")
    p_sync.add_argument("--root", default=".", help="project root to update")
    p_sync.add_argument(
        "--target",
        choices=("all", *install.TARGETS),
        default="all",
        help="adapter surface to sync (default: all)",
    )
    p_sync.add_argument("--dry-run", action="store_true", help="show planned updates only")
    p_sync.set_defaults(func=_cmd_sync)

    p_lw = sub.add_parser(
        "install-legacy-wrappers",
        help="install thin legacy command wrappers that delegate to /keel:<command>",
    )
    p_lw.add_argument("agent", help=f"'all' or one of: {', '.join(install.LEGACY_TARGETS)}")
    p_lw.add_argument("--root", default=".", help="project root to install into")
    p_lw.add_argument("--force", action="store_true", help="overwrite existing wrappers")
    p_lw.add_argument(
        "--parity-matrix",
        default="docs/keel/parity-matrix.md",
        help="markdown parity matrix whose ready rows allow wrapper generation",
    )
    p_lw.add_argument(
        "--command",
        action="append",
        type=_parse_legacy_mapping,
        default=[],
        metavar="LEGACY=KEEL",
        help="install one wrapper mapping; repeat for multiple commands",
    )
    p_lw.set_defaults(func=_cmd_install_legacy_wrappers)

    return parser


def _add_ship_parser(parser: argparse.ArgumentParser, *, command: str) -> None:
    parser.add_argument("path", help="path to project.yaml")
    parser.add_argument("--root", default=".", help="repo root for git, gates + extensions")
    parser.add_argument("--pr", type=int, default=None, help="PR number for CI status (gh)")
    parser.add_argument("--hotfix", action="store_true", help="emergency: bypass the merge window")
    parser.add_argument("--dry-run", action="store_true",
                        help="explicitly mark the assessment as non-mutating")
    parser.add_argument("--live", action="store_true",
                        help=("run the live preflight gate and fail before gates "
                              "if consent is missing"))
    parser.add_argument("--approve-scope", action="append", default=[],
                        help="approve a consent scope for this run; repeat or comma-separate")
    parser.add_argument("--operator", default=None,
                        help="operator identifier to include in an approved consent record")
    parser.add_argument("--consent-mode", choices=consent.CONSENT_MODES, default=None,
                        help="operator consent mode: explicit, standing, or agent")
    parser.add_argument("--target", default=None,
                        help="task target to include in the consent prompt and record")
    parser.add_argument("--append-ledger", action="store_true",
                        help="append the structured ship run record when --live succeeds")
    parser.add_argument("--run-id", default=None,
                        help="operator/session run id to store in the run ledger record")
    parser.add_argument("--run-events-file", default=None,
                        help="JSON run-events file to evaluate and stamp into the ledger record")
    parser.add_argument("--max-rounds", type=_positive_int, default=None,
                        help="explicit run-control work-unit budget override")
    parser.add_argument("--issue", type=_positive_int, default=None,
                        help="issue number to store in the run ledger record")
    parser.add_argument("--pull-request", dest="ledger_pr", type=_positive_int, default=None,
                        help="PR number to store in the run ledger record without CI lookup")
    parser.add_argument("--branch", default=None,
                        help="branch name to store in the run ledger record")
    parser.add_argument("--head-sha", default=None,
                        help="head commit SHA to store in the run ledger record")
    parser.add_argument("--capture-status", type=_capture_status_arg, default=None,
                        help="capture outcome to store in the run ledger record")
    parser.add_argument("--capture-reason", default=None,
                        help="capture outcome reason to store in the run ledger record")
    parser.add_argument("--implementer", default=None,
                        help="effective implementer codename or vendor/model label")
    parser.add_argument("--reviewer-agent", action="append", default=[],
                        help="effective reviewer codename or vendor/model label; repeatable")
    parser.add_argument("--tester", default=None,
                        help="effective tester codename or vendor/model label")
    parser.add_argument("--host-agent", default=None,
                        help="host agent codename (e.g. claude/codex/agy) for the run context")
    parser.add_argument("--transport", choices=("gh", "mcp"), default=None,
                        help="detected GitHub transport for the run context; "
                             "defaults to the resolved transport when omitted")
    parser.add_argument("--strict-run-context", action="store_true",
                        help="block live ledger append when required run-context fields "
                             "would degrade")
    parser.add_argument("--issue-title", default=None,
                        help="issue title to include in the intake/readiness contract")
    parser.add_argument("--issue-body", default=None,
                        help="issue body markdown to include in the intake/readiness contract")
    parser.add_argument("--issue-label", action="append", default=[],
                        help="issue label for intake/readiness; repeat or comma-separate")
    parser.add_argument("--review-comments", choices=("inline", "summary"), default="inline",
                        help="review posting mode for the resolved ship contract")
    parser.add_argument("--reviewers", type=int, choices=(1, 2, 3), default=None,
                        help="override the risk-derived reviewer count")
    parser.add_argument("--jury", action="store_true",
                        help="enable the cross-vendor jury gate")
    parser.add_argument("--no-jury", action="store_true",
                        help="disable the cross-vendor jury gate")
    parser.add_argument("--jury-advisory", action="store_true",
                        help="make an enabled jury advisory instead of merge-gating")
    parser.add_argument("--profile", choices=("standard", "compound"), default="standard",
                        help="workflow profile: standard (default) or compound")
    parser.add_argument("--compound", action="store_true",
                        help="select the compound workflow profile (alias for --profile compound)")
    parser.add_argument("--json", action="store_true", help="emit structured JSON")
    parser.set_defaults(func=_cmd_ship, ship_command=command)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _parse_pr_issue_mapping(value: str) -> tuple[int, int]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("linked issue mapping must be PR=ISSUE")
    raw_pr, raw_issue = value.split("=", 1)
    try:
        pr = _positive_int(raw_pr)
        issue = _positive_int(raw_issue)
    except (argparse.ArgumentTypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("linked issue mapping must be PR=ISSUE") from exc
    return pr, issue


def _capture_status_arg(value: str) -> str:
    if value == "skipped":
        return value
    try:
        capture.normalize_status(value)
    except capture.CaptureError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    return func(args)
