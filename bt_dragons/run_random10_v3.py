#!/usr/bin/env python3
"""
WonderTrader 强生战法 - 随机10只A股回测
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

# ===== 配置 =====
WT_BIN = "/root/wondertrader/src/build_all/build_x64/Release/bin"
BTDIR = "/root/wondertrader/bt_dragons"
DSBDIR = f"{BTDIR}/storage"
CSVDIR = f"{BTDIR}/csv_data"
COMMONDIR = f"{BTDIR}/common"
OUTDIR = f"{BTDIR}/outputs_random"

os.makedirs(CSVDIR, exist_ok=True)
os.makedirs(DSBDIR, exist_ok=True)
os.makedirs(OUTDIR, exist_ok=True)

# ===== Step 0: 确保配置文件正确 =====
print("📌 Step 0: 检查配置文件...")

# 需要合并stocks.json到contracts.json，stk_comms.json到commodities.json
# 检查commodities.json是否已包含STK
with open(f"{COMMONDIR}/commodities.json") as f:
    comms = json.load(f)
if "STK" not in comms.get("SSE", {}):
    print("  合并stk_comms到commodities...")
    for enc in ['gbk', 'utf-8']:
        try:
            with open("/root/wondertrader/dist/common/stk_comms.json", encoding=enc) as f:
                stk_comms = json.load(f)
            for exchg in stk_comms:
                if exchg not in comms:
                    comms[exchg] = {}
                for key, val in stk_comms[exchg].items():
                    comms[exchg][key] = val
            with open(f"{COMMONDIR}/commodities.json", 'w') as f:
                json.dump(comms, f, indent=4, ensure_ascii=False)
            break
        except:
            continue

with open(f"{COMMONDIR}/contracts.json") as f:
    conts = json.load(f)
if len(conts.get("SSE", {})) < 100:
    print("  合并stocks到contracts...")
    for enc in ['gbk', 'utf-8']:
        try:
            with open("/root/wondertrader/dist/common/stocks.json", encoding=enc) as f:
                stocks = json.load(f)
            for exchg in stocks:
                if exchg not in conts:
                    conts[exchg] = {}
                conts[exchg].update(stocks[exchg])
            with open(f"{COMMONDIR}/contracts.json", 'w') as f:
                json.dump(conts, f, indent=4, ensure_ascii=False)
            break
        except:
            continue

# 确保sessions.json是UTF-8
sess_path = f"{COMMONDIR}/sessions.json"
try:
    with open(sess_path, encoding='utf-8') as f:
        json.load(f)
except:
    print("  转换sessions.json到UTF-8...")
    for enc in ['gbk', 'latin-1']:
        try:
            with open("/root/wondertrader/dist/common/sessions.json", encoding=enc) as f:
                data = json.load(f)
            with open(sess_path, 'w') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            break
        except:
            continue

print("  ✅ 配置文件就绪")

# ===== Step 1: 随机选股 =====
print("\n📌 Step 1: 随机选股...")
stock_list = ak.stock_zh_a_spot_em()
# 排除ST、退市、次新(N开头)
stock_list = stock_list[~stock_list['名称'].str.contains('ST|退|N ', na=False)]
# 只选主板+创业板（00/30/60开头）
stock_list = stock_list[stock_list['代码'].str.startswith(('00', '30', '60'))]
# 过滤掉市值太小的（低于50亿）
stock_list = stock_list[stock_list['总市值'] > 5e9]
# 过滤掉价格太高（>200元，1手2万以上）或太低（<3元）
stock_list = stock_list[(stock_list['最新价'] > 3) & (stock_list['最新价'] < 200)]

all_codes = stock_list[['代码', '名称']].values.tolist()
random.seed(88)  # 固定种子可复现
sampled = random.sample(all_codes, 10)

# 确定交易所
selected = []
for code, name in sampled:
    if code.startswith('6'):
        exchg = 'SSE'
    else:
        exchg = 'SZSE'
    selected.append((code, name, exchg))
    print(f"  🎯 {name}({code}) → {exchg}")

# ===== Step 2: 下载数据 =====
print("\n📌 Step 2: 下载数据...")
end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=1825)).strftime('%Y%m%d')
col_map = {'日期':'date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'vol','成交额':'amount'}
csv_cols = ['date','time','open','high','low','close','volume','turnover','open_interest','diff_interest','settle']

for code, name, exchg in selected:
    outf = f"{CSVDIR}/{exchg}.{code}.csv"
    if os.path.exists(outf):
        print(f"  SKIP {name}({code})")
        continue
    try:
        raw = ak.stock_zh_a_hist(symbol=code, period='daily', start_date=start_date, end_date=end_date, adjust='qfq')
        if raw is None or len(raw) < 130:
            print(f"  ❌ {name}({code}) 数据不足({len(raw) if raw is not None else 0})")
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
        print(f"  ✅ {name}({code}): {len(df)}条")
    except Exception as e:
        print(f"  ❌ {name}({code}): {e}")

# ===== Step 3: CSV → DSB =====
print("\n📌 Step 3: CSV → DSB...")
helper = ctypes.CDLL(f"{WT_BIN}/WtDtPorter/libWtDtHelper.so", mode=ctypes.RTLD_GLOBAL)
LOG_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
log_cb = LOG_CALLBACK(lambda msg: None)  # 静默
helper.trans_csv_bars.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, LOG_CALLBACK]
helper.trans_csv_bars.restype = None
helper.trans_csv_bars(CSVDIR.encode(), DSBDIR.encode(), b"d", log_cb)

# 复制到his/day/{exchg}/{code}.dsb
for code, name, exchg in selected:
    src = f"{DSBDIR}/{exchg}.{code}.dsb"
    dst_dir = f"{DSBDIR}/his/day/{exchg}"
    if os.path.exists(src):
        os.makedirs(dst_dir, exist_ok=True)
        shutil.copy2(src, f"{dst_dir}/{code}.dsb")
dsb_count = len([f for f in os.listdir(f"{DSBDIR}/his/day/SSE") if f.endswith('.dsb')]) + \
            len([f for f in os.listdir(f"{DSBDIR}/his/day/SZSE") if f.endswith('.dsb')]) if os.path.exists(f"{DSBDIR}/his/day/SSE") else 0
print(f"  ✅ DSB文件就绪")

# ===== Step 4: WonderTrader回测 =====
print("\n📌 Step 4: WonderTrader回测...")

results = []
for code, name, exchg in selected:
    std_code = f"{exchg}.STK.{code}"
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
    cta_etime: 202605011500
    stime: 202105050930
    etime: 202605011500
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
        trades_file = f"{bt_out}/outputs_bt/qs_{code}/trades.csv"
        
        closes = []
        if os.path.exists(closes_file):
            with open(closes_file) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('openprice'):
                        closes.append(row)
        
        n_trades = 0
        if os.path.exists(trades_file):
            with open(trades_file) as f:
                n_trades = max(0, len(f.read().strip().split('\n')) - 1)
        
        n_closes = len(closes)
        wins = []
        losses = []
        for c in closes:
            pnl = (float(c['closeprice']) / float(c['openprice']) - 1) * 100
            if pnl > 0: wins.append(pnl)
            else: losses.append(pnl)
        
        nw = len(wins)
        nl = len(losses)
        wr = nw / n_closes * 100 if n_closes > 0 else 0
        aw = sum(wins) / nw if nw else 0
        al = sum(losses) / nl if nl else 0
        pr = abs(aw / al) if al != 0 else (999 if nw > 0 else 0)
        tp = sum(wins) + sum(losses)
        
        r = {
            'name': name, 'code': code, 'exchg': exchg, 'std_code': std_code,
            'n_trades': n_trades, 'n_closes': n_closes, 'n_wins': nw, 'n_losses': nl,
            'win_rate': wr, 'avg_win': aw, 'avg_loss': al, 'pnl_ratio': pr,
            'total_pnl': tp, 'closes': closes
        }
        results.append(r)
        
        emoji = "✅" if tp > 0 else "❌"
        print(f"  {emoji} {name}({code}): {n_trades}笔交易 {n_closes}笔平仓 胜率{wr:.0f}% 累计{tp:+.1f}%")
        
    except Exception as e:
        print(f"  ❌ {name}({code}): {e}")
        results.append({'name': name, 'code': code, 'error': str(e)})

# ===== Step 5: 汇总 =====
print("\n" + "=" * 70)
print("🐉 WonderTrader 强生战法V4 · 随机10只A股回测")
print("=" * 70)
print(f"回测: 2024-01 ~ 2026-05 | 1手(100股) | ATR止损 + 20%止盈")

valid = [r for r in results if 'error' not in r]
valid.sort(key=lambda x: x['total_pnl'], reverse=True)

print(f"\n{'#':<3} {'股票':<10} {'平仓':<4} {'赢':<3} {'亏':<3} {'胜率':<6} {'均赢':<7} {'均亏':<7} {'盈亏比':<6} {'累计':<8}")
print("-" * 65)

for i, r in enumerate(valid, 1):
    tag = ["🥇","🥈","🥉"][i-1] if i <= 3 else f"{i}"
    pr_s = f"{r['pnl_ratio']:.1f}" if r['pnl_ratio'] < 900 else "∞"
    print(f"{tag:<3} {r['name']:<10} {r['n_closes']:<4} {r['n_wins']:<3} {r['n_losses']:<3} {r['win_rate']:<5.0f}% {r['avg_win']:<+6.1f}% {r['avg_loss']:<+6.1f}% {pr_s:<6} {r['total_pnl']:+.1f}%")

tc = sum(r['n_closes'] for r in valid)
tw = sum(r['n_wins'] for r in valid)
pos = sum(1 for r in valid if r['total_pnl'] > 0)
print(f"\n总平仓: {tc}笔 | 胜率: {tw}/{tc} = {tw/tc*100:.0f}%" if tc > 0 else "\n无交易")
print(f"正收益: {pos}/{len(valid)} | 累计: {sum(r['total_pnl'] for r in valid):+.1f}%")

# 明细
for r in valid:
    if r['n_closes'] == 0:
        print(f"\n⚪ {r['name']} - 无交易")
        continue
    s = "+" if r['total_pnl'] > 0 else ""
    print(f"\n{'✅' if r['total_pnl']>0 else '❌'} {r['name']}({r['code']}) | {r['n_closes']}笔 | {r['win_rate']:.0f}% | {s}{r['total_pnl']:.1f}%")
    for c in r['closes']:
        pnl = (float(c['closeprice']) / float(c['openprice']) - 1) * 100
        hold = int(c.get('closebarno', 0)) - int(c.get('openbarno', 0))
        e = "✅" if pnl > 0 else "❌"
        ot = c['opentime'][:4]+"-"+c['opentime'][4:6]+"-"+c['opentime'][6:8]
        ct = c['closetime'][:4]+"-"+c['closetime'][4:6]+"-"+c['closetime'][6:8]
        print(f"  {e} {ot}→{ct} [{c['entertag']:>2}→{c['exittag']:<6}] {pnl:+.1f}% ({hold}天)")

print("\n回测完成！")
