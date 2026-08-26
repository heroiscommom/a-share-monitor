#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易命令脚本 —— 解析 GitHub Issue 里的 /trade 命令，录入交易流水。
由 .github/workflows/trade-command.yml 在 issue 打开时调用。

命令格式（放在 issue 正文任意一行）：
  /trade buy  600664 900 9.107 哈药股份 初始建仓
  /trade sell 600664 300 8.50  哈药股份 止盈一半
  /trade pnl  002900 -4961 哈三联 清仓亏损

字段：方向 代码 股数 价格 [名称] [备注]（pnl 时第3个字段为盈亏金额，无价格）
"""

import os
import re
import sys

import trade


def parse_command(text):
    """从文本中提取 /trade 命令，返回参数列表或 None"""
    if not text:
        return None
    m = re.search(r"/trade\s+(buy|sell|pnl)\s+(\d{6})\s+([-\d.]+)\s+([\d.]*)", text)
    if not m:
        return None
    side, code = m.group(1), m.group(2)
    qty = m.group(3)
    price = m.group(4)
    # 提取名称与备注（命令行剩余部分）
    line = next((ln.strip() for ln in text.splitlines() if ln.strip().startswith("/trade")), "")
    parts = line.split()
    name = ""
    note = ""
    if side == "pnl":
        # /trade pnl 002900 -4961 哈三联 清仓亏损
        if len(parts) > 3:
            name = parts[3]
        if len(parts) > 4:
            note = " ".join(parts[4:])
        return {"side": "pnl", "code": code, "amount": qty, "name": name, "note": note}
    # buy/sell: /trade buy 600519 100 1500.00 贵州茅台 备注
    if len(parts) > 5:
        name = parts[5]
    if len(parts) > 6:
        note = " ".join(parts[6:])
    if not price:
        return None
    return {"side": side, "code": code, "shares": qty, "price": price, "name": name, "note": note}


def main():
    title = os.environ.get("ISSUE_TITLE", "")
    body = os.environ.get("ISSUE_BODY", "")
    text = f"{title}\n{body}"
    cmd = parse_command(text)
    lines = []
    if not cmd:
        lines.append("⚠️ 未识别到有效的 /trade 命令。格式：")
        lines.append("`/trade buy 600664 900 9.107 哈药股份 备注`")
        lines.append("`/trade sell 600664 300 8.50 哈药股份 备注`")
        lines.append("`/trade pnl 002900 -4961 哈三联 清仓亏损`")
        print("\n".join(lines))
        sys.exit(0)

    try:
        if cmd["side"] == "pnl":
            trade.add_trade("pnl", cmd["code"], cmd["amount"], 0,
                            name=cmd.get("name", ""), reason=cmd.get("note") or "已实现盈亏记录")
            lines.append(f"✅ 已记录盈亏: {cmd['code']} {cmd['amount']:+,.0f}（{cmd.get('name', '')}）")
        else:
            trade.add_trade(cmd["side"], cmd["code"], cmd["shares"], cmd["price"],
                            name=cmd.get("name", ""), reason=cmd.get("note") or f"{'买入' if cmd['side']=='buy' else '卖出'}")
            lines.append(f"✅ 已记录: {cmd['side'].upper()} {cmd['code']} {cmd['shares']}股 @{cmd['price']}（{cmd.get('name', '')}）")
        # 输出当前持仓摘要
        lines.append("")
        lines.append("【当前持仓】")
        pos = trade.positions_from_trades(trade.load_trades()["trades"])
        for code, p in sorted(pos.items()):
            if p["shares"] > 0:
                lines.append(f"• {p['name']}({code}) {p['shares']:.0f}股 均价{p['avg_cost']:.3f} 已实现{p['realized_pnl']:+,.0f}")
            else:
                lines.append(f"• {p['name']}({code}) 清仓 已实现{p['realized_pnl']:+,.0f}")
    except Exception as e:
        lines.append(f"❌ 处理失败: {e}")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
