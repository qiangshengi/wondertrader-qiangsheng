#!/bin/bash
# WonderTrader 强生战法 - 10大龙头股回测全流程
set -e

BTDIR="/root/wondertrader/bt_dragons"
DATADIR="$BTDIR/csv_data"
DSBDIR="$BTDIR/storage"
WT_BIN="/root/wondertrader/src/build_all/build_x64/Release/bin"
OUTDIR="$BTDIR/outputs"
LOGFILE="$BTDIR/full_run.log"

exec > >(tee -a "$LOGFILE") 2>&1

echo "=========================================="
echo "🐉 龙头股回测 开始 $(date)"
echo "=========================================="

mkdir -p "$DATADIR" "$DSBDIR" "$OUTDIR"

# ===== Step 1: 下载数据(逐个) =====
echo ""
echo "📌 Step 1: 下载龙头股数据"

STOCKS="600519:贵州茅台:SSE 300750:宁德时代:SZSE 002594:比亚迪:SZSE 601318:中国平安:SSE 600036:招商银行:SSE 600276:恒瑞医药:SSE 603288:海天味业:SSE 601012:隆基绿能:SSE 601888:中国中免:SSE 300059:东方财富:SZSE"

for item in $STOCKS; do
    IFS=':' read -r code name exchg <<< "$item"
    outf="$DATADIR/${exchg}.${code}.csv"
    if [ -f "$outf" ]; then
        echo "  SKIP $name($code) - already exists"
        continue
    fi
    echo -n "  Downloading $name($code)..."
    python3 -c "
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import os

end_date = datetime.now().strftime('%Y%m%d')
start_date = (datetime.now() - timedelta(days=800)).strftime('%Y%m%d')

raw = ak.stock_zh_a_hist(symbol='$code', period='daily', start_date=start_date, end_date=end_date, adjust='qfq')
if raw is None or len(raw) < 130:
    print(f' FAIL (data={len(raw) if raw is not None else 0})')
    exit(1)

col_map = {'日期':'date','开盘':'open','收盘':'close','最高':'high','最低':'low','成交量':'vol','成交额':'amount'}
df = raw.rename(columns=col_map)
df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
df['time'] = 0
df['volume'] = df['vol'].astype(float)
df['turnover'] = df['amount'].astype(float)
df['open_interest'] = 0.0
df['diff_interest'] = 0.0
df['settle'] = 0.0
csv_cols = ['date','time','open','high','low','close','volume','turnover','open_interest','diff_interest','settle']
df[csv_cols].to_csv('$outf', index=False)
print(f' OK ({len(df)} rows)')
" 2>&1 || echo " FAIL"
    sleep 2
done

echo ""
echo "✅ 下载完成，CSV文件:"
ls -la "$DATADIR/"

# ===== Step 2: CSV → DSB =====
echo ""
echo "📌 Step 2: 转换CSV → DSB"

python3 << 'DSBEOF'
import ctypes
import os

WT_BIN = "/root/wondertrader/src/build_all/build_x64/Release/bin"
DATADIR = "/root/wondertrader/bt_dragons/csv_data"
DSBDIR = "/root/wondertrader/bt_dragons/storage"

helper = ctypes.CDLL(f"{WT_BIN}/WtDtPorter/libWtDtHelper.so", mode=ctypes.RTLD_GLOBAL)
LOG_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_char_p)

def log_cb(msg):
    if msg:
        print(f"  [WT] {msg.decode('utf-8', errors='ignore')}")

cb = LOG_CALLBACK(log_cb)
helper.trans_csv_bars.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, LOG_CALLBACK]
helper.trans_csv_bars.restype = None
helper.trans_csv_bars(DATADIR.encode(), DSBDIR.encode(), b"d", cb)

print("DSB files:")
for f in sorted(os.listdir(DSBDIR)):
    size = os.path.getsize(f"{DSBDIR}/{f}")
    print(f"  {f} ({size:,} bytes)")
DSBEOF

# ===== Step 3: 逐个回测 =====
echo ""
echo "📌 Step 3: WonderTrader回测"

for item in $STOCKS; do
    IFS=':' read -r code name exchg <<< "$item"
    std_code="${exchg}.${code}"
    dsb_path="$DSBDIR/${std_code}.d"
    
    if [ ! -d "$dsb_path" ] && [ ! -f "$DSBDIR/${std_code}.dsb" ]; then
        echo "  ⚠️ $name($code) - DSB不存在，跳过"
        continue
    fi
    
    bt_out="$OUTDIR/${code}_${name}"
    mkdir -p "$bt_out"
    
    echo -n "  回测 $name($std_code)... "
    
    # 写配置
    cat > "$bt_out/config.yaml" << CFGEOF
replayer:
    basefiles:
        commodity: $BTDIR/common/commodities.json
        contract: $BTDIR/common/contracts.json
        holiday: $BTDIR/common/holidays.json
        hot: $BTDIR/common/hots.json
        session: $BTDIR/common/sessions.json
    fees: $BTDIR/common/fees.json
    cta_stime: 202401010930
    cta_etime: 202605011500
    stime: 202401010930
    etime: 202605011500
    mode: dsb
    path: $DSBDIR/
env:
    mocker: cta
    slippage: 1
cta:
    module: $WT_BIN/WtCtaStraFact/libWtCtaStraFact.so
    strategy:
        id: qs_${code}
        name: QS
        params:
            code: ${std_code}
            period: d
            count: 200
            lots: 1
            stock: true
CFGEOF

    cat > "$bt_out/logcfg.json" << 'LOGEOF'
{"outputs": ["console"]}
LOGEOF

    export LD_LIBRARY_PATH="$WT_BIN/WtCtaStraFact:$WT_BIN/WtDtPorter:$WT_BIN"
    
    cd "$bt_out"
    timeout 60 "$WT_BIN/WtBtRunner/WtBtRunner" "$bt_out/config.yaml" > "$bt_out/stdout.log" 2>&1
    rc=$?
    
    if [ $rc -eq 0 ]; then
        echo "✅"
    else
        echo "❌ (exit=$rc)"
        # 打印关键错误
        grep -i "error\|fatal\|fail\|exception" "$bt_out/stderr.log" "$bt_out/stdout.log" 2>/dev/null | tail -5 | sed 's/^/    /'
    fi
done

echo ""
echo "=========================================="
echo "🐉 龙头股回测完成 $(date)"
echo "=========================================="

# 汇总结果
echo ""
echo "📊 结果汇总:"
for item in $STOCKS; do
    IFS=':' read -r code name exchg <<< "$item"
    bt_out="$OUTDIR/${code}_${name}"
    if [ -d "$bt_out" ]; then
        echo "--- $name($code) ---"
        # 查找交易记录
        find "$bt_out" -name "*.csv" -o -name "*trade*" -o -name "*signal*" 2>/dev/null | while read f; do
            echo "  File: $f"
            head -5 "$f" 2>/dev/null
        done
        # 从stdout提取关键信息
        grep -i "profit\|return\|trade\|win\|loss\|fund\|signal\|买卖\|买入\|卖出\|收益" "$bt_out/stdout.log" 2>/dev/null | tail -20 | sed 's/^/  /'
    fi
done
