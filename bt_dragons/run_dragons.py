#!/usr/bin/env python3
"""
WonderTrader 强生战法回测 - 10大龙头股
步骤：
1. akshare下载日K数据 → CSV
2. WtDtHelper转CSV → DSB二进制
3. WtBtRunner跑QS策略回测
"""
import akshare as ak
import pandas as pd
import os
import subprocess
from datetime import datetime, timedelta

# ===== 配置 =====
WT_BIN = "/root/wondertrader/src/build_all/build_x64/Release/bin"
WTDTHelper = f"{WT_BIN}/WtDtPorter/libWtDtHelper.so"
BTDIR = "/root/wondertrader/bt_dragons"
DATADIR = f"{BTDIR}/csv_data"
DSBDIR = f"{BTDIR}/storage"
OUTDIR = f"{BTDIR}/outputs"

DRAGONS = [
    ("600519", "贵州茅台", "SSE"),
    ("300750", "宁德时代", "SZSE"),
    ("002594", "比亚迪",   "SZSE"),
    ("601318", "中国平安", "SSE"),
    ("600036", "招商银行", "SSE"),
    ("600276", "恒瑞医药", "SSE"),
    ("603288", "海天味业", "SSE"),
    ("601012", "隆基绿能", "SSE"),
    ("601888", "中国中免", "SSE"),
    ("300059", "东方财富", "SZSE"),
]

# ===== Step 1: 下载数据 =====
def download_data():
    os.makedirs(DATADIR, exist_ok=True)
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=800)).strftime("%Y%m%d")
    
    for code, name, exchg in DRAGONS:
        try:
            print(f"📥 下载 {name}({code})...", end=" ")
            raw = ak.stock_zh_a_hist(symbol=code, period="daily", 
                                      start_date=start_date, end_date=end_date, adjust="qfq")
            if raw is None or len(raw) < 130:
                print(f"❌ 数据不足({len(raw) if raw is not None else 0}条)")
                continue
            
            col_map = {'日期':'date','开盘':'open','收盘':'close','最高':'high',
                       '最低':'low','成交量':'vol','成交额':'amount','振幅':'amplitude',
                       '涨跌幅':'pct_change','涨跌额':'change_amount','换手率':'turnover',
                       '股票代码':'code'}
            df = raw.rename(columns=col_map)
            
            # WonderTrader CSV格式: date,open,high,low,close,volume,turnover,open_interest,diff_interest,settle
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
            df['time'] = '0'
            df['volume'] = df['vol']
            df['turnover'] = df['amount']
            df['open_interest'] = 0
            df['diff_interest'] = 0
            df['settle'] = 0
            
            csv_cols = ['date', 'time', 'open', 'high', 'low', 'close', 'volume', 'turnover', 'open_interest', 'diff_interest', 'settle']
            
            # 保存为 {exchg}.{code}.csv (WonderTrader的命名规范)
            # 对于股票，WonderTrader用: SSE.600519 / SZSE.300750
            outname = f"{exchg}.{code}.csv"
            df[csv_cols].to_csv(f"{DATADIR}/{outname}", index=False)
            print(f"✅ {len(df)}条 → {outname}")
        except Exception as e:
            print(f"❌ 异常: {e}")

# ===== Step 2: 转换CSV → DSB =====
def convert_to_dsb():
    os.makedirs(DSBDIR, exist_ok=True)
    
    # WtDtHelper trans_csv_bars 需要: csv_folder, bin_folder, period
    # 但这是个C++函数，我们需要用ctypes调用
    
    # 先检查有没有现成的Python绑定
    print("📦 转换CSV → DSB...")
    
    # 使用ctypes直接调用libWtDtHelper.so
    import ctypes
    
    dll_path = f"{WT_BIN}/WtDtPorter/libWtDtHelper.so"
    try:
        helper = ctypes.CDLL(dll_path)
    except Exception as e:
        print(f"❌ 加载WtDtHelper失败: {e}")
        # 尝试用LD_LIBRARY_PATH加载
        os.environ['LD_LIBRARY_PATH'] = f"{WT_BIN}/WtDtPorter:{WT_BIN}"
        helper = ctypes.CDLL(dll_path, mode=ctypes.RTLD_GLOBAL)
    
    # 定义回调函数类型
    LOG_CALLBACK = ctypes.CFUNCTYPE(None, ctypes.c_char_p)
    
    def log_callback(msg):
        if msg:
            print(f"  [WT] {msg.decode('utf-8', errors='ignore')}")
    
    log_cb = LOG_CALLBACK(log_callback)
    
    # 调用 trans_csv_bars(csvFolder, binFolder, period, cbLogger)
    helper.trans_csv_bars.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, LOG_CALLBACK]
    helper.trans_csv_bars.restype = None
    
    csv_dir = DATADIR.encode('utf-8')
    bin_dir = DSBDIR.encode('utf-8')
    period = b"d"
    
    helper.trans_csv_bars(csv_dir, bin_dir, period, log_cb)
    print("✅ DSB转换完成")
    
    # 验证
    dsb_files = [f for f in os.listdir(DSBDIR) if f.endswith('.dsb')]
    print(f"📁 DSB文件: {len(dsb_files)}个")
    for f in sorted(dsb_files):
        size = os.path.getsize(f"{DSBDIR}/{f}")
        print(f"  - {f} ({size:,} bytes)")

# ===== Step 3: 配置并运行WtBtRunner =====
def run_backtest():
    """为每只龙头股配置并运行回测"""
    
    results = []
    
    for code, name, exchg in DRAGONS:
        std_code = f"{exchg}.{code}"
        dsb_file = f"{DSBDIR}/{std_code}.d"
        dsb_path = f"{DSBDIR}/{std_code}.d"
        
        # 检查DSB目录是否存在
        if not os.path.isdir(dsb_path) and not os.path.isfile(f"{DSBDIR}/{std_code}.dsb"):
            print(f"⚠️ {name}({code}) DSB数据不存在，跳过")
            results.append({'code': code, 'name': name, 'exchg': exchg, 'error': 'no dsb'})
            continue
        
        print(f"\n🔄 回测 {name}({std_code})...")
        
        # 创建回测输出目录
        bt_out = f"{OUTDIR}/{code}_{name}"
        os.makedirs(bt_out, exist_ok=True)
        
        # 写回测配置
        config = f"""replayer:
    basefiles:
        commodity: {BTDIR}/common/commodities.json
        contract: {BTDIR}/common/contracts.json
        holiday: {BTDIR}/common/holidays.json
        hot: {BTDIR}/common/hots.json
        session: {BTDIR}/common/sessions.json
    fees: {BTDIR}/common/fees.json
    cta_stime: 202401010930
    cta_etime: 202605011500
    stime: 202401010930
    etime: 202605011500
    mode: dsb
    path: {DSBDIR}/
env:
    mocker: cta
    slippage: 1
cta:
    module: {WT_BIN}/WtCtaStraFact/libWtCtaStraFact.so
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
        cfg_path = f"{bt_out}/config.yaml"
        with open(cfg_path, 'w') as f:
            f.write(config)
        
        # 写log配置
        logcfg = f"""{{"outputs": ["console", "{bt_out}/logs"]}}"""
        logcfg_path = f"{bt_out}/logcfg.json"
        with open(logcfg_path, 'w') as f:
            f.write(logcfg)
        
        # 运行WtBtRunner
        bt_runner = f"{WT_BIN}/WtBtRunner/WtBtRunner"
        env = os.environ.copy()
        env['LD_LIBRARY_PATH'] = f"{WT_BIN}/WtCtaStraFact:{WT_BIN}/WtDtPorter:{WT_BIN}"
        
        try:
            proc = subprocess.run(
                [bt_runner, cfg_path],
                cwd=bt_out,
                env=env,
                capture_output=True,
                text=True,
                timeout=120
            )
            
            # 解析输出
            stdout = proc.stdout
            stderr = proc.stderr
            
            # 保存日志
            with open(f"{bt_out}/stdout.log", 'w') as f:
                f.write(stdout)
            with open(f"{bt_out}/stderr.log", 'w') as f:
                f.write(stderr)
            
            # 查找交易记录
            trade_files = []
            for root, dirs, files in os.walk(bt_out):
                for fname in files:
                    if 'trade' in fname.lower() or 'signal' in fname.lower() or 'fndrl' in fname.lower():
                        trade_files.append(os.path.join(root, fname))
            
            result = {
                'code': code,
                'name': name,
                'exchg': exchg,
                'std_code': std_code,
                'exit_code': proc.returncode,
                'trade_files': trade_files,
                'stdout': stdout[-2000:] if stdout else '',
                'stderr': stderr[-2000:] if stderr else '',
            }
            results.append(result)
            
            if proc.returncode == 0:
                print(f"✅ {name} 回测完成")
            else:
                print(f"❌ {name} 回测失败 (exit={proc.returncode})")
                if stderr:
                    # 只打印关键错误
                    for line in stderr.split('\n'):
                        if 'error' in line.lower() or 'fatal' in line.lower() or 'fail' in line.lower():
                            print(f"  💥 {line.strip()}")
            
        except subprocess.TimeoutExpired:
            print(f"⏰ {name} 超时(120s)")
            results.append({'code': code, 'name': name, 'error': 'timeout'})
        except Exception as e:
            print(f"❌ {name} 异常: {e}")
            results.append({'code': code, 'name': name, 'error': str(e)})
    
    return results

if __name__ == '__main__':
    print("=" * 60)
    print("🐉 WonderTrader 强生战法 - 10大龙头股回测")
    print("=" * 60)
    
    print("\n📌 Step 1: 下载数据")
    download_data()
    
    print("\n📌 Step 2: 转换DSB")
    convert_to_dsb()
    
    print("\n📌 Step 3: 运行回测")
    results = run_backtest()
    
    print("\n" + "=" * 60)
    print("📊 回测结果汇总")
    print("=" * 60)
    for r in results:
        if 'error' in r:
            print(f"❌ {r['name']}({r['code']}): {r['error']}")
        else:
            print(f"  {r['name']}({r['code']}): exit={r['exit_code']}")
