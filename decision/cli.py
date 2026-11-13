"""命令行适配。查看、批准、拒绝和执行都调用决策模块的同一组操作。"""
import argparse
import json
import sys

from decision.service import DecisionService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='采购主管查看、批准、拒绝或重试执行')
    commands = parser.add_subparsers(dest='command', required=True)

    show = commands.add_parser('show', help='展示决策和证据快照')
    show.add_argument('--id', type=int, required=True)
    show.add_argument('--token', required=True)

    reject = commands.add_parser('reject', help='拒绝待审批决策')
    reject.add_argument('--id', type=int, required=True)
    reject.add_argument('--token', required=True)
    reject.add_argument('--reason', required=True)

    approve = commands.add_parser('approve', help='批准仍待审批的决策，并在提交后尝试执行')
    approve.add_argument('--id', type=int, required=True)
    approve.add_argument('--token', required=True)
    approve.add_argument('--comment', default='')

    execute = commands.add_parser('execute', help='按决策编号重试执行，不再次批准')
    execute.add_argument('--id', type=int, required=True)

    args = parser.parse_args(argv)
    service = DecisionService()
    if args.command == 'show':
        result = service.show(args.id, args.token)
    elif args.command == 'reject':
        result = service.reject(args.id, args.token, args.reason)
    elif args.command == 'execute':
        result = service.execute(args.id)
    else:
        result = service.approve(args.id, args.token, args.comment)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1)
