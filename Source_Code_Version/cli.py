"""QQ 群聊总结器 — 命令行入口

用于验证核心逻辑，也是 GUI 的兜底（GUI 出问题时仍可用 CLI）。

用法：
    py -3 cli.py                          # 最近一个已结束的周期
    py -3 cli.py --date 2026-10-12        # 指定日期（该日12:00~次日12:00）
    py -3 cli.py --start "2026-10-12 12:00" --end "2026-10-12 18:00"
    py -3 cli.py --list-groups            # 列出当前账号的群
    py -3 cli.py --test-ai                # 测试 AI 连接
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

# 让脚本能直接跑（把项目根加入 sys.path）
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config, config_path, mask_key  # noqa: E402
from app.core.ai import AIClient  # noqa: E402
from app.core.napcat import (  # noqa: E402
    OneBotClient, NapCatService, date_tag,
    latest_complete_day, period_bounds,
)
from app.core.summarize import SummarizeTask  # noqa: E402
from app.logger import get_logger  # noqa: E402


def cmd_list_groups(cfg: Config) -> int:
    client = OneBotClient(cfg)
    try:
        info = client.login_info()
        print(f'当前登录：{info.get("nickname", "")} ({info.get("user_id", "")})')
        print()
        groups = client.group_list(no_cache=True)
        if not groups:
            print('（该账号没有加入任何群）')
            return 0
        print(f'共 {len(groups)} 个群：')
        for g in groups:
            print(f'  {g["group_id"]:>12s}  {g["group_name"]}'
                  f'  ({g["member_count"]} 人)')
        return 0
    except Exception as e:
        print(f'错误：{e}')
        return 1


def cmd_list_accounts(cfg: Config) -> int:
    svc = NapCatService(cfg)
    accounts = svc.quick_login_list()
    if not accounts:
        print('未能获取账号列表（NapCat 可能未运行）')
        return 1
    print(f'本机可用账号 {len(accounts)} 个：')
    for a in accounts:
        nick = f'  {a["nickname"]}' if a.get('nickname') else ''
        print(f'  {a["uin"]}{nick}')
    return 0


def cmd_test_ai(cfg: Config) -> int:
    errs = cfg.validate()
    if errs:
        print('配置有问题：')
        for e in errs:
            print(f'  - {e}')
        return 1
    print(f'接口：{cfg.ai_base_url}')
    print(f'模型：{cfg.get("ai_model")}')
    print(f'Key ：{mask_key(str(cfg.get("ai_key")))}')
    print()
    print('测试中...')
    ai = AIClient(cfg)
    r = ai.test_connection()
    if r['ok']:
        print(f'✓ 连接成功，耗时 {r["latency"]:.2f}s')
        print(f'  模型回复：{r["reply"]}')
        return 0
    print(f'✗ 连接失败：{r["error"]}')
    return 1


def parse_when(s: str) -> dt.datetime:
    """解析 'YYYY-MM-DD HH:MM' 或 'YYYY-MM-DD'。"""
    s = s.strip()
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H', '%Y-%m-%d'):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(
        f'时间格式无效：{s}（应为 "2026-10-12 12:00"）')


def main() -> int:
    ap = argparse.ArgumentParser(
        description='QQ 群聊总结器（CLI）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    ap.add_argument('--date', help='日期标签 YYYY-MM-DD（该日12:00~次日12:00）')
    ap.add_argument('--start', type=parse_when, help='起始时间 "2026-10-12 12:00"')
    ap.add_argument('--end', type=parse_when, help='结束时间 "2026-10-12 18:00"')
    ap.add_argument('--group', help='群号（默认用配置里的）')
    ap.add_argument('--config', help='指定配置文件路径')
    ap.add_argument('--list-groups', action='store_true', help='列出群列表')
    ap.add_argument('--list-accounts', action='store_true', help='列出本机账号')
    ap.add_argument('--test-ai', action='store_true', help='测试 AI 连接')
    ap.add_argument('--include-current', action='store_true',
                    help='允许总结尚未结束的周期')
    ap.add_argument('--max', type=int, default=60000, help='最多拉取条数')
    args = ap.parse_args()

    # 加载配置
    try:
        cfg = Config.load(Path(args.config) if args.config else None)
    except Exception as e:
        print(f'配置加载失败：{e}')
        return 1

    # 首次运行：没有配置文件时生成模板
    if not config_path().exists() and not args.config:
        try:
            cfg.save()
            print(f'已生成配置文件：{config_path()}')
            print('请先填写 AI API Key，再运行总结。')
        except Exception:
            pass

    logger = get_logger(cfg)
    # 把 AI Key 登记为敏感信息（日志里自动打码）
    logger.register_secret(str(cfg.get('ai_key', '')))

    # 子命令
    if args.list_groups:
        return cmd_list_groups(cfg)
    if args.list_accounts:
        return cmd_list_accounts(cfg)
    if args.test_ai:
        return cmd_test_ai(cfg)

    # 配置校验
    errs = cfg.validate()
    if errs:
        print('配置不完整：')
        for e in errs:
            print(f'  - {e}')
        print(f'\n配置文件：{config_path()}')
        return 1

    # 确定时间区间
    now = dt.datetime.now()
    if args.start and args.end:
        start_ts, end_ts = args.start.timestamp(), args.end.timestamp()
        label = args.start.date()
        span = (end_ts - start_ts) / 3600
        if span <= 0:
            print('结束时间必须晚于起始时间')
            return 1
        if span > 24:
            print(f'时间跨度 {span:.1f} 小时，超过 24 小时上限')
            return 1
        if end_ts > now.timestamp():
            print('结束时间不能是未来时间')
            return 1
    elif args.date:
        label = dt.date.fromisoformat(args.date)
        start_ts, end_ts = period_bounds(label, int(cfg.get('day_start_hour', 12)))
    else:
        label = latest_complete_day(int(cfg.get('day_start_hour', 12)))
        start_ts, end_ts = period_bounds(label, int(cfg.get('day_start_hour', 12)))

    if end_ts > now.timestamp() and not args.include_current:
        print(f'周期 {dt.datetime.fromtimestamp(start_ts):%Y-%m-%d %H:%M} ~ '
              f'{dt.datetime.fromtimestamp(end_ts):%Y-%m-%d %H:%M} 尚未结束，跳过。')
        print('（如需强行总结，加 --include-current）')
        return 1

    # 群
    group_id = args.group or str(cfg.get('group_id') or '').strip()
    if not group_id:
        print('未指定群号。请用 --group 指定，或在配置文件里设置 group_id。')
        print('（可用 --list-groups 查看可选群）')
        return 1
    group_name = str(cfg.get('group_name') or '').strip()
    if not group_name:
        # 尝试从群列表查名字
        try:
            for g in OneBotClient(cfg).group_list():
                if g['group_id'] == group_id:
                    group_name = g['group_name']
                    break
        except Exception:
            pass
        group_name = group_name or f'群 {group_id}'

    # 执行
    task = SummarizeTask(cfg, logger)
    result = task.run(start_ts, end_ts, label, group_id, group_name)

    print()
    if result.ok:
        print(f'完成！输出：{result.output_file}')
        print(f'  有效发言 {result.valid_count} 条，图片 {result.image_count} 张，'
              f'耗时 {result.elapsed:.0f}s')
        return 0
    if result.cancelled:
        print('已取消')
        return 130
    print(f'失败：{result.error}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
