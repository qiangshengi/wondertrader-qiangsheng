#!/usr/bin/env python3
"""
WonderTrader 强生战法V3 - 大样本100只回测
"""
import akshare as ak
import pandas as pd
import numpy as np
import json
import os
import shutil
import subprocess
import ctypes
import random
import csv
from datetime import datetime, timedelta

WT_BIN = "/root/wondertrader/src/build_all/build_x64/Release/bin"
BTDIR = "/root/wondertrader/bt_dragons"
DSBDIR = f"{BTDIR}/storage"
CSVDIR = f"{BTDIR}/csv_data_100"
COMMONDIR = f"{BTDIR}/common"
OUTDIR = f"{BTDIR}/outputs_100"

os.makedirs(CSVDIR, exist_ok=True)
os.makedirs(OUTDIR, exist_ok=True)

# Step 1: 选股100只
print("📌 Step 1: 随机选股100只...")
stock_list = ak.stock_zh_a_spot_em()
stock_list = stock_list[~stock_list['名称'].str.contains('ST|退|N ', na=False)]
stock_list = stock_list[stock_list['代码'].str.startswith(('00', '30', '60'))]
stock_list = stock_list[stock_list['总市值'] > 5e9]
stock_list = stock_list[(stock_list['最新价'] > 3) & (stock_list['最新价'] < 200)]

all_codes = stock_list[['代码', '名称']].values.tolist()
random.seed(2025)
sampled = random.sample(all_codes, 100)

selected = []
for code, name in sampled:
    exchg = 'SSE' if code.startswith('6') else 'SZSE'
    selected.append((code, name, exchg))

# Step 2: 下载数据
print(f"📌 Step 2: 下载100只数据 (5年)...")
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=1825)).strftime('%Y%m%d')
col_map = {'日期':'date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'vol','成交额':'amount'}
csv_cols = ['date','time','open','high','low','close','volume','turnover','open_interest','diff_interest','settle']

ok_count = 0
for code, name, exchg in selected:
    outf = f"{CSVDIR}/{exchg}.{code}.csv"
    if os.path.exists(outf):
        ok_count += 1
        continue
    try:
        raw = ak.stock_zh_a_hist(symbol=code, period='daily', start_date=start_date, end_date=end_date, adjust='qfq')
        if raw is None or len(raw) < 130:
            continue
        df = raw.rename(columns=col_map)
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
        df['time'] = 0
        df['volume'] = df['vol'].astype(float)
        df['turnover'] = df['amount'].astype(float)
        df['open_interest'] = 0.0
        df['diff_interest'] = 0.0
        df['settle'] = 0.0
        df[csv_cols].to_csv(outf, index=False)
        ok_count += 1
    except:
        continue

print(f"  ✅ {ok_count}只下载完成")

# Step 3: CSV → DSB
print("📌 Step 3: CSV → DSB...")
helper = ctypes.CDLL(f"{WT_BIN}/WtDtPorter/libWtDtHelper.so", mode=ctypes.RTLD_GLOBAL)
LOG_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
log_cb = LOG_CALLBACK(lambda msg: None)
helper.trans_csv_bars.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, LOG_CALLBACK]
helper.trans_csv_bars.restype = None
helper.trans_csv_bars(CSVDIR.encode(), DSBDIR.encode(), b"d", log_cb)

for code, name, exchg in selected:
    src = f"{DSBDIR}/{exchg}.{code}.dsb"
    dst_dir = f"{DSBDIR}/his/day/{exchg}"
    if os.path.exists(src):
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src, f"{dst_dir}/{code}.dsb")

print("  ✅ DSB就绪")

# Step 4: 回测
print(f"📌 Step 4: WonderTrader回测100只...")

results = []
for idx, (code, name, exchg) in enumerate(selected):
    std_code = f"{exchg}.STK.{code}"
    dsb_file = f"{DSBDIR}/his/day/{exchg}/{code}.dsb"
    if not os.path.exists(dsb_file):
        continue
    
    bt_out = f"{OUTDIR}/{code}_{name}"
    for d in ['outputs_bt', 'BtLogs']:
        p = f"{bt_out}/{d}"
        if os.path.exists(p):
            shutil.rmtree(p)
    os.makedirs(bt_out, exist_ok=True)

    config = f"""replayer:
    basefiles:
        commodity: {COMMONDIR}/commodities.json
        contract: {COMMONDIR}/contracts.json
        holiday: {COMMONDIR}/holidays.json
        hot: {COMMONDIR}/hots.json
        session: {COMMONDIR}/sessions.json
    fees: {COMMONDIR}/fees.json
    cta_stime: 202105050930
    cta_etime: 202605051500
    stime: 202105050930
    etime: 202605051500
    mode: bin
    path: {DSBDIR}/
env:
    mocker: cta
    slippage: 1
cta:
    module: {WT_BIN}/libWtCtaStraFact.so
    strategy:
        id: qs_{code}
        name: QS
        params:
            code: {std_code}
            period: d
            count: 200
            lots: 1
            stock: true
"""
    with open(f"{bt_out}/configbt.yaml", "w") as f:
        f.write(config)

    logcfg = """dyn_pattern:
    strategy:
        async: false
        level: debug
        sinks:
        -   filename: BtLogs/Strategy_%s.log
            pattern: '[%Y.%m.%d %H:%M:%S - %-5l] %v'
            truncate: true
            type: basic_file_sink
root:
    async: false
    level: debug
    sinks:
    -   filename: BtLogs/Runner.log
        pattern: '[%Y.%m.%d %H:%M:%S - %-5l] %v'
        truncate: true
        type: basic_file_sink
    -   pattern: '[%m.%d %H:%M:%S - %^%-5l%$] %v'
        type: console_sink
"""
    with open(f"{bt_out}/logcfgbt.yaml", "w") as f:
        f.write(logcfg)

    env = os.environ.copy()
    env['LD_LIBRARY_PATH'] = f"{WT_BIN}:{DSBDIR}"

    try:
        proc = subprocess.Popen(
            [f"{WT_BIN}/WtBtRunner/WtBtRunner", "-c", f"{bt_out}/configbt.yaml", "-l", f"{bt_out}/logcfgbt.yaml"],
            cwd=bt_out, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = proc.communicate(input=b"\n", timeout=60)

        closes_file = f"{bt_out}/outputs_bt/qs_{code}/closes.csv"
        closes = []
        if os.path.exists(closes_file):
            with open(closes_file) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('openprice'):
                        closes.append(row)

        n_closes = len(closes)
        wins, losses = [], []
        for c in closes:
            pnl = (float(c['closeprice']) / float(c['openprice']) - 1) * 100
            if pnl > 0: wins.append(pnl)
            else: losses.append(pnl)

        nw, nl = len(wins), len(losses)
        wr = nw / n_closes * 100 if n_closes > 0 else 0
        aw = sum(wins) / nw if nw else 0
        al = sum(losses) / nl if nl else 0
        pr = abs(aw / al) if al != 0 else (999 if nw > 0 else 0)
        tp = sum(wins) + sum(losses)

        r = {
            'name': name, 'code': code, 'exchg': exchg,
            'n_closes': n_closes, 'n_wins': nw, 'n_losses': nl,
            'win_rate': wr, 'avg_win': aw, 'avg_loss': al,
            'pnl_ratio': pr, 'total_pnl': tp, 'closes': closes
        }
        results.append(r)

        if (idx + 1) % 10 == 0:
            print(f"  进度: {idx+1}/100")

    except Exception as e:
        continue

# Step 5: 汇总
valid = [r for r in results if r['n_closes'] > 0]
valid.sort(key=lambda x: x['total_pnl'], reverse=True)

print(f"\n{'='*70}")
print(f"🐉 强生战法V3 · 100只A股 · 近5年回测")
print(f"{'='*70}")

tc = sum(r['n_closes'] for r in valid)
tw = sum(r['n_wins'] for r in valid)
pos = sum(1 for r in valid if r['total_pnl'] > 0)
total_pnl = sum(r['total_pnl'] for r in valid)

print(f"有交易: {len(valid)}/100 | 总平仓: {tc}笔 | 胜率: {tw/tc*100:.0f}%")
print(f"正收益: {pos}/{len(valid)} | 负收益: {len(valid)-pos}/{len(valid)}")
print(f"累计盈亏: {total_pnl:+.1f}% | 平均每只: {total_pnl/len(valid):+.1f}%")

# Top 10
print(f"\n🏆 Top 10:")
print(f"{'#':<3} {'股票':<12} {'平仓':<4} {'胜率':<6} {'盈亏比':<6} {'累计':<8}")
print("-" * 45)
for i, r in enumerate(valid[:10], 1):
    pr_s = f"{r['pnl_ratio']:.1f}" if r['pnl_ratio'] < 900 else "∞"
    print(f"{i:<3} {r['name']:<10} {r['n_closes']:<4} {r['win_rate']:<5.0f}% {pr_s:<6} {r['total_pnl']:+.1f}%")

# Bottom 10
print(f"\n💔 Bottom 10:")
print(f"{'#':<3} {'股票':<12} {'平仓':<4} {'胜率':<6} {'盈亏比':<6} {'累计':<8}")
print("-" * 45)
for i, r in enumerate(valid[-10:], 1):
    pr_s = f"{r['pnl_ratio']:.1f}" if r['pnl_ratio'] < 900 else "∞"
    print(f"{i:<3} {r['name']:<10} {r['n_closes']:<4} {r['win_rate']:<5.0f}% {pr_s:<6} {r['total_pnl']:+.1f}%")

# 信号分布统计
print(f"\n📊 卖出原因分布:")
reason_dist = {}
for r in valid:
    for c in r['closes']:
        tag = c.get('exittag', '?')
        reason_dist[tag] = reason_dist.get(tag, 0) + 1
for reason, count in sorted(reason_dist.items(), key=lambda x: -x[1]):
    print(f"  {reason}: {count}次")

# 信号分布
print(f"\n📡 买入信号分布:")
sig_dist = {}
for r in valid:
    for c in r['closes']:
        tag = c.get('entertag', '?')
        sig_dist[tag] = sig_dist.get(tag, 0) + 1
for sig, count in sorted(sig_dist.items(), key=lambda x: -x[1]):
    print(f"  {sig}: {count}次")

# 分档统计
brackets = [
    ("大赚(>50%)", lambda x: x > 50),
    ("盈利(20-50%)", lambda x: 20 < x <= 50),
    ("小赚(0-20%)", lambda x: 0 < x <= 20),
    ("小亏(0--20%)", lambda x: -20 < x <= 0),
    ("亏损(-20--50%)", lambda x: -50 < x <= -20),
    ("大亏(<-50%)", lambda x: x <= -50),
]
print(f"\n📈 收益分布:")
for label, fn in brackets:
    cnt = sum(1 for r in valid if fn(r['total_pnl']))
    print(f"  {label}: {cnt}只 ({cnt/len(valid)*100:.0f}%)")
