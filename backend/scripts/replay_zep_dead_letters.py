"""XRUN-6: 回放 Zep 死信 episodes（uploads/simulations/_zep_dead_letter/<graph_id>.jsonl）。

ZepGraphMemoryUpdater 在批次发送最终失败时把活动写入 sim-scoped 死信 JSONL
（zep_graph_memory_updater._write_dead_letter 的记录格式：graph_id/platform/
combined_text/agent_name/action_type/round/timestamp/dead_lettered_at）——但此前
从无回放路径：3,383 条 episode 静静躺了一个月，建立在这些图谱上的报告证据饥饿。

本 CLI 按死信记录里的 combined_text 走**同一条** episode-ingest 路径
（Zep facade graph.add，与 _send_batch_activities 完全一致）重新提交：

  backend/.venv/bin/python backend/scripts/replay_zep_dead_letters.py <graph_id>
  backend/.venv/bin/python backend/scripts/replay_zep_dead_letters.py --all
  backend/.venv/bin/python backend/scripts/replay_zep_dead_letters.py --all --dry-run

语义（degrade-safe，绝不销毁数据）：
* 逐批（默认 5 条 = ZepGraphMemoryUpdater.BATCH_SIZE）提交；某批失败即停止该文件，
  未提交的记录 + 解析失败的原始行 原子写回死信文件（tmp + os.replace），下次可续跑。
* 全部成功 → 死信文件改名为 <name>.replayed-<ts>（保留审计痕迹，不再被健康检查计数）。
* 回放期间若有在跑的模拟继续追加死信，收尾时会把快照之后新增的行一并保留。
  建议在无模拟运行时执行。
"""

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import Config  # noqa: E402
from app.services.graphiti_client import Zep  # noqa: E402

DEFAULT_BATCH_SIZE = 5  # 与 ZepGraphMemoryUpdater.BATCH_SIZE 对齐
SEND_INTERVAL = 0.5     # 与 ZepGraphMemoryUpdater.SEND_INTERVAL 对齐


def _dead_letter_dir() -> str:
    return os.path.abspath(os.path.join(Config.OASIS_SIMULATION_DATA_DIR, "_zep_dead_letter"))


def _atomic_rewrite(path: str, lines: list) -> None:
    """把剩余（未回放）行原子写回死信文件；空列表 = 全部回放完，由调用方改名归档。"""
    tmp = f"{path}.tmp-{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        if lines:
            fh.write("\n".join(lines) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def replay_file(client: Zep, path: str, batch_size: int, dry_run: bool, limit: int = 0) -> dict:
    """回放单个死信文件，返回统计 dict。失败绝不抛出到 main 之外的粒度。"""
    graph_id = os.path.splitext(os.path.basename(path))[0]
    with open(path, "r", encoding="utf-8") as fh:
        snapshot = [ln.rstrip("\n") for ln in fh]
    snapshot = [ln for ln in snapshot if ln.strip()]

    records = []      # (原始行, combined_text)
    malformed = []    # 解析失败/缺 combined_text 的原始行（永远保留）
    for ln in snapshot:
        try:
            rec = json.loads(ln)
            text = str(rec.get("combined_text") or "").strip()
            if text:
                records.append((ln, text))
            else:
                malformed.append(ln)
        except (json.JSONDecodeError, TypeError):
            malformed.append(ln)

    stats = {"graph_id": graph_id, "total": len(snapshot), "malformed": len(malformed),
             "replayed": 0, "remaining": 0, "error": ""}
    if dry_run:
        stats["remaining"] = len(records)
        print(f"[dry-run] {graph_id}: {len(records)} 条可回放, {len(malformed)} 条格式异常")
        return stats

    replayed_upto = 0  # records 中已成功提交的条数
    error = ""
    for i in range(0, len(records), batch_size):
        if limit and replayed_upto >= limit:
            break
        batch = records[i:i + batch_size]
        combined = "\n".join(text for _, text in batch)
        try:
            client.graph.add(graph_id=graph_id, type="text", data=combined)
            replayed_upto = i + len(batch)
            print(f"  [{graph_id}] 已回放 {replayed_upto}/{len(records)} 条")
            time.sleep(SEND_INTERVAL)
        except Exception as e:  # noqa: BLE001 — 单批失败：停止本文件，剩余写回续跑
            error = f"{type(e).__name__}: {str(e)[:200]}"
            print(f"  [{graph_id}] 批次失败，停止本文件: {error}")
            break

    remaining_lines = [ln for ln, _ in records[replayed_upto:]] + malformed
    # 快照之后（回放期间）新追加的死信行必须保留。
    try:
        with open(path, "r", encoding="utf-8") as fh:
            current = [ln.rstrip("\n") for ln in fh if ln.strip()]
        if len(current) > len(snapshot):
            remaining_lines.extend(current[len(snapshot):])
    except OSError:
        pass

    if remaining_lines:
        _atomic_rewrite(path, remaining_lines)
    else:
        archived = f"{path}.replayed-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
        os.replace(path, archived)
        print(f"  [{graph_id}] 全部回放完成，死信文件已归档: {archived}")

    stats.update(replayed=replayed_upto, remaining=len(remaining_lines), error=error)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="回放 Zep 死信 episodes 到对应图谱")
    ap.add_argument("graph_id", nargs="?", help="图谱ID（省略时需 --all）")
    ap.add_argument("--all", action="store_true", help="回放死信目录下所有 <graph_id>.jsonl")
    ap.add_argument("--dry-run", action="store_true", help="只统计不提交")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    ap.add_argument("--limit", type=int, default=0, help="每个图谱最多回放条数（0=不限）")
    args = ap.parse_args()

    dl_dir = _dead_letter_dir()
    if args.graph_id:
        paths = [os.path.join(dl_dir, f"{args.graph_id}.jsonl")]
    elif args.all:
        paths = sorted(glob.glob(os.path.join(dl_dir, "*.jsonl")))
    else:
        ap.error("需要 graph_id 或 --all")
        return 2

    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        print(f"没有可回放的死信文件（目录: {dl_dir}）")
        return 0

    # dry-run 只统计不提交，不必启动 graphiti runtime（Zep() 构造即拉起后台事件循环）。
    client = None if args.dry_run else Zep(api_key=Config.ZEP_API_KEY or "local")
    failed = 0
    for p in paths:
        print(f"回放 {p} ...")
        try:
            st = replay_file(client, p, max(1, args.batch_size), args.dry_run, args.limit)
        except Exception as e:  # noqa: BLE001 — 单文件异常不影响其余文件
            print(f"  文件级异常，跳过 {p}: {type(e).__name__}: {e}")
            failed += 1
            continue
        if st["error"]:
            failed += 1
        print(f"  统计: {st}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
