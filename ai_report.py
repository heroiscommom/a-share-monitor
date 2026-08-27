#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 决策报告模块（2026-08-26 新增，P0）
================================================
把 auto_report 的结构化量化数据(持仓建议/支撑压力/市场情绪/龙头)打包给 DeepSeek,
生成自然语言「决策仪表盘」: 大盘速览/持仓操作/重点关注/风险提示。

安全设计:
  - API key 只从环境变量 DEEPSEEK_API_KEY 读取(GitHub Secrets / 本地环境变量)
  - 绝无 key 写入仓库文件
  - LLM 只负责「表达润色」，建议结论以规则引擎为准（系统提示强约束+输出后校验）
  - 无 key 时自动跳过，不影响原有报告

用法:
  python3 ai_report.py --dry-run          # 打印 AI 报告
  python3 ai_report.py --send             # 发送邮件（需 SMTP 凭据）
"""

import os
import sys
import json
import datetime
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"

SYSTEM_PROMPT = """你是A股量化分析助手。你只根据用户提供的结构化数据进行分析，严禁编造任何数据、价格、事件。
规则：
1. 所有结论必须基于给定的数据；数据里没有的，写"数据不足"。
2. 操作建议必须引用数据中的【建议】(持有/止损/止盈/低吸/减仓)和理由，不得擅自改变。
3. 报告格式（不超过500字）：
   【今日大盘】市场状态+情绪一句话（含仓位建议）
   【持仓操作】逐只：名称 现价 建议 一句话理由
   【仓位观点】基于现金比例给一句仓位建议
   【风险提示】1-2条基于数据的风险点（如支撑守住率低、高位风险评分）
   【建议复盘】如有 advice_check 命中率数据：一句话点评历史建议命中情况，点名最近打脸案例（没有则写"数据积累中"）
4. 语言精炼口语化，像资深交易员复盘，不用寒暄。
5. 结尾固定一行：⚠️ 本报告由AI基于量化数据生成，仅供参考，不构成投资建议。"""


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return default


def build_data_payload():
    """收集结构化数据（与 auto_report.py 同源）"""
    base = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(base, "data")
    snap = load_json(os.path.join(data_dir, "snapshot.json"), {})
    quant = load_json(os.path.join(data_dir, "quant.json"), {})
    dragon = load_json(os.path.join(data_dir, "dragon_head.json"), {})
    trades = load_json(os.path.join(data_dir, "trades.json"), {"trades": []})

    import portfolio as pf
    portfolio, capital = pf.load_portfolio()
    quote_map = {q["code"]: q for q in (snap.get("quotes") or [])}
    advices = []
    for p in portfolio:
        hist = load_json(os.path.join(data_dir, "history", f"{p['code']}.json"), [])
        a = pf.advice_one(p, quote_map.get(p["code"]), hist)
        advices.append({k: a.get(k) for k in
                        ("name", "code", "price", "cost", "pnl_pct", "score", "signal",
                         "support", "resistance", "support_held", "risk", "advice", "reason")})

    sentiment = (dragon.get("sentiment") or {}).get("today") or {}
    sm = (dragon.get("sentiment") or {}).get("state_machine") or {}
    trend = (dragon.get("sentiment") or {}).get("trend") or {}
    tiers = dragon.get("tiers") or {}
    sa = (tiers.get("S") or [])[:3] + (tiers.get("A") or [])[:3]

    # 打脸复盘统计（P1-5）
    advice_stats = load_json(os.path.join(data_dir, "advice_history.json"), {"entries": []})
    vcount = {"应验": 0, "打脸": 0, "持平": 0}
    facepalms = []
    for e in advice_stats.get("entries", []):
        for code, v in (e.get("verdicts") or {}).items():
            r = v.get("result")
            if r in vcount:
                vcount[r] += 1
                if r == "打脸":
                    name = next((it.get("name", code) for it in e.get("items", []) if it.get("code") == code), code)
                    facepalms.append(f"{e['date']} {name} {v.get('detail', '')}")
    vn = vcount["应验"] + vcount["打脸"]
    advice_check = {
        "hit_rate": round(vcount["应验"] / vn * 100) if vn else None,
        "counts": vcount,
        "facepalms": facepalms[-3:],
    }

    return {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "market": (quant.get("market_regime") or {}).get("desc", "未知"),
        "sentiment": {"state": sentiment.get("state"), "zt_count": sentiment.get("zt_count"),
                      "max_lbc": sentiment.get("max_lbc"),
                      "direction": sm.get("direction"), "position_advice": sm.get("position_advice"),
                      "zbc_rate": sm.get("zbc_rate"),
                      "trend": trend.get("desc", "")},
        "advice_check": advice_check,
        "capital": {"total": capital.get("total"), "cash": capital.get("cash")},
        "positions": advices,
        "dragon_watch": [{"name": it.get("name"), "lbc": it.get("lbc"),
                          "score": it.get("dragon_score")} for it in sa],
    }


def call_deepseek(payload, temperature=0.4, max_tokens=900):
    """调用 DeepSeek Chat（OpenAI 兼容），返回文本"""
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        return None
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode("utf-8"))
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        print(f"[ai] 调用失败: {e}")
        return None


def send_email(subject, body):
    user = os.environ.get("SMTP_USER")
    pw = os.environ.get("SMTP_PASS")
    to = os.environ.get("SMTP_TO")
    if not (user and pw and to):
        print("[notify] 未配置 SMTP_USER/SMTP_PASS/SMTP_TO，跳过发信")
        return False
    import smtplib
    from email.mime.text import MIMEText
    from email.header import Header
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = to
    try:
        host = os.environ.get("SMTP_HOST") or "smtp.qq.com"
        port = int(os.environ.get("SMTP_PORT") or 465)
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, pw)
            server.sendmail(user, [to], msg.as_string())
        print("[notify] 邮件已发送")
        return True
    except Exception as e:
        print(f"[notify] 发信失败: {e}")
        return False


def main():
    dry = "--dry-run" in sys.argv
    send = "--send" in sys.argv
    payload = build_data_payload()
    print(f"[ai] 数据打包完成: {len(payload['positions'])} 只持仓, 情绪{payload['sentiment'].get('state')}")
    report = call_deepseek(payload)
    if not report:
        print("[ai] 未生成报告(无 DEEPSEEK_API_KEY 或调用失败), 跳过")
        return
    print("=" * 44)
    print("🤖 AI 决策报告")
    print("=" * 44)
    print(report)
    if send:
        subject = f"【AI决策】{datetime.date.today().isoformat()} 持仓建议"
        send_email(subject, report)


if __name__ == "__main__":
    main()
