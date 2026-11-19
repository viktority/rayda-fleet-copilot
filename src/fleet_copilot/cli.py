"""Trusted local-operator interface. Use the authenticated HTTP API for remote callers."""

import argparse
import json
import sys

from .actions import Actions, Decision
from .agent import FleetAgent
from .analytics import Analytics, Query, Trend
from .config import Settings
from .domain import FleetError, Principal
from .model import OpenAIModel


def emit(value):
    print(json.dumps(value, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Validate and ingest the assessment dataset")
    for name in ("ask", "query", "trends", "proposals", "decide", "audit"):
        p = sub.add_parser(name)
        p.add_argument("--company", required=True)
        p.add_argument("--actor", default="local-operator")
        p.add_argument("--role", choices=["viewer", "operator", "admin"], default="operator")
        if name == "ask":
            p.add_argument("question")
            p.add_argument(
                "--propose",
                action="store_true",
                help="Allow pending simulated proposals; never automatic approval",
            )
            p.add_argument("--json", action="store_true")
        elif name in ("query", "trends"):
            p.add_argument("parameters", help='JSON tool arguments, for example: {"kind":"low_disk"}')
        elif name == "decide":
            p.add_argument("proposal_id")
            p.add_argument("--digest", required=True, help="Exact digest of the proposal you reviewed")
            group = p.add_mutually_exclusive_group(required=True)
            group.add_argument("--approve", action="store_true")
            group.add_argument("--reject", action="store_true")
            p.add_argument(
                "--confirm", action="store_true", required=True, help="Explicitly confirm this decision"
            )
    serve = sub.add_parser("serve", help="Start authenticated local HTTP API")
    serve.add_argument("--port", type=int, default=8000)
    evaluate = sub.add_parser("eval", help="Run deterministic evaluation corpus or opt-in model evaluations")
    evaluate.add_argument(
        "--live", action="store_true", help="Uses the configured OpenAI model and incurs API usage"
    )
    evaluate.add_argument("--output", default="reports/evaluation-latest.json")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            import uvicorn

            uvicorn.run("fleet_copilot.api:from_config", factory=True, host="127.0.0.1", port=args.port)
            return
        settings = Settings.load()
        if args.command == "init":
            emit(settings.store().ingest(settings.dataset))
            return
        if args.command == "eval":
            from .evaluation import evaluate

            result = evaluate(settings.dataset, live=args.live, output=args.output)
            emit(result)
            if not result["passed"]:
                sys.exit(1)
            return
        store = settings.store()
        principal = Principal(company_id=args.company, actor_id=args.actor, role=args.role)
        analytics = Analytics(store)
        actions = Actions(store, analytics)
        if args.command == "ask":
            result = FleetAgent(store, OpenAIModel()).ask(principal, args.question, args.propose)
            emit(result) if args.json else print(result["answer"])
        elif args.command == "query":
            facts = analytics.query(principal, Query.model_validate_json(args.parameters))
            store.audit(principal, "cli_query", {"parameters": json.loads(args.parameters)})
            emit([f.model_dump() for f in facts])
        elif args.command == "trends":
            facts = analytics.trends(principal, Trend.model_validate_json(args.parameters))
            store.audit(principal, "cli_trends", {"parameters": json.loads(args.parameters)})
            emit([f.model_dump() for f in facts])
        elif args.command == "proposals":
            emit(actions.list(principal))
        elif args.command == "decide":
            emit(
                actions.decide(
                    principal,
                    args.proposal_id,
                    Decision(
                        decision="approve" if args.approve else "reject",
                        expected_digest=args.digest,
                        confirmed=args.confirm,
                    ),
                )
            )
        elif args.command == "audit":
            emit(store.audit_log(principal))
    except FleetError as exc:
        emit({"error": exc.code, "message": exc.message})
        sys.exit(1)
    except ValueError:
        emit(
            {
                "error": "invalid_configuration_or_arguments",
                "message": "Check configuration and tool arguments; no secret values are displayed",
            }
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
