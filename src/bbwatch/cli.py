from __future__ import annotations

import argparse
import getpass
import sys

from .auth import login as adfs_login
from .bbclient import BbClient
from .secrets import Credentials, load_credentials, store_credentials
from .transport import CurlCffiTransport, Transport


def run_whoami(transport: Transport, creds: Credentials, login_fn=adfs_login) -> str:
    login_fn(transport, creds)
    client = BbClient(transport)
    me = client.get_me()
    courses = client.list_courses(me.id)
    active = [c for c in courses if c.is_active]
    name = me.given_name or me.user_name
    return (
        f"已登录：{name}（uid={me.id}）\n"
        f"课程：共 {len(courses)} 门，在读 {len(active)} 门"
    )


def cmd_setup(_args) -> int:
    username = input("学校账号(形如 学号@link.cuhk.edu.cn): ").strip()
    password = getpass.getpass("密码（输入不回显）: ")
    store_credentials(username, password)
    print("已存入 macOS 钥匙串。可运行 bbwatch whoami 验证。")
    return 0


def cmd_whoami(_args) -> int:
    creds = load_credentials()
    print(run_whoami(CurlCffiTransport(), creds))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bbwatch")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="录入并保存学校账号密码到钥匙串").set_defaults(fn=cmd_setup)
    sub.add_parser("whoami", help="登录并打印身份与课程数").set_defaults(fn=cmd_whoami)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as e:  # noqa: BLE001
        print(f"错误：{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
