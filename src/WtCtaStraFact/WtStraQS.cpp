#include "WtStraQS.h"

#include "../Includes/ICtaStraCtx.h"
#include "../Includes/WTSContractInfo.hpp"
#include "../Includes/WTSVariant.hpp"
#include "../Includes/WTSDataDef.hpp"
#include "../Share/decimal.h"

extern const char* FACT_NAME;

#include "../Share/fmtlib.h"

WtStraQS::WtStraQS(const char* id)
	: CtaStrategy(id)
	, _inPosition(false)
	, _costPrice(0)
	, _stopLossPrice(0)
	, _holdDays(0)
	, _atrMultiplier(2.5)
	, _atrPeriod(14)
{
}

WtStraQS::~WtStraQS()
{
}

const char* WtStraQS::getFactName()
{
	return FACT_NAME;
}

const char* WtStraQS::getName()
{
	return "ZGe";
}

bool WtStraQS::init(WTSVariant* cfg)
{
	if (cfg == NULL)
		return false;

	_code = cfg->getCString("code");
	_period = cfg->getCString("period");
	_count = cfg->getUInt32("count");
	_lots = cfg->getUInt32("lots");
	_isstk = cfg->getBoolean("stock");

	// V2优化参数 - 有默认值
	WTSVariant* params = cfg->get("params");
	if (params)
	{
		_stopLossRatio = params->getDouble("stop_loss_ratio");
		_takeProfitRatio = params->getDouble("take_profit_ratio");
		_b1JThreshold = params->getDouble("b1_j_threshold");
		_minScore = params->getInt32("min_score");
		_holdDaysForSell = params->getInt32("hold_days_for_sell");
		_holdDaysForTrend = params->getInt32("hold_days_for_trend");
	}
	else
	{
		// 默认V2参数
		_stopLossRatio = 0.93;
		_takeProfitRatio = 1.20;
		_b1JThreshold = 10.0;
		_minScore = 4;
		_holdDaysForSell = 5;
		_holdDaysForTrend = 3;
	}

	return true;
}

// ============= 工具函数 =============

double WtStraQS::calcEMA(const double* data, int len, int period)
{
	if (len == 0) return 0;
	double multiplier = 2.0 / (period + 1);
	double ema = data[0];
	for (int i = 1; i < len; i++)
	{
		ema = data[i] * multiplier + ema * (1 - multiplier);
	}
	return ema;
}

double WtStraQS::calcMA(const double* data, int len, int period)
{
	if (len < period) return 0;
	double sum = 0;
	for (int i = len - period; i < len; i++)
		sum += data[i];
	return sum / period;
}

double WtStraQS::calcBBI(const double* closes, int len)
{
	if (len < 24) return 0;
	double ma3 = calcMA(closes, len, 3);
	double ma6 = calcMA(closes, len, 6);
	double ma12 = calcMA(closes, len, 12);
	double ma24 = calcMA(closes, len, 24);
	return (ma3 + ma6 + ma12 + ma24) / 4.0;
}

void WtStraQS::calcKDJ(const double* highs, const double* lows, const double* closes,
                         int len, double& k, double& d, double& j)
{
	int n = 9;
	k = 50.0;
	d = 50.0;

	for (int i = n; i < len; i++)
	{
		double hh = highs[i], ll = lows[i];
		for (int j2 = i - n + 1; j2 <= i; j2++)
		{
			if (highs[j2] > hh) hh = highs[j2];
			if (lows[j2] < ll) ll = lows[j2];
		}
		double rsv = (hh == ll) ? 50.0 : (closes[i] - ll) / (hh - ll) * 100.0;
		k = (2.0 / 3.0) * k + (1.0 / 3.0) * rsv;
		d = (2.0 / 3.0) * d + (1.0 / 3.0) * k;
	}
	j = 3.0 * k - 2.0 * d;
}

int WtStraQS::calcScore(double curClose, double prevClose, double curBBI,
                          double prevJ, double prevD, double curJ,
                          double curVol, double prevVol, double ma20)
{
	int score = 0;
	// 1. 收盘上升
	if (curClose > prevClose) score++;
	// 2. 不破BBI
	if (curClose >= curBBI) score++;
	// 3. 无巨量真阴
	bool isTrueYin = (curClose < prevClose) && (curVol > prevVol * 1.1);
	if (!isTrueYin) score++;
	// 4. 趋势向上
	if (ma20 > 0 && curClose > ma20) score++;
	// 5. J没死叉 (J下穿D为死叉)
	if (!(prevJ > prevD && curJ < prevD)) score++;
	return score;
}

// ============= 信号检测 =============

bool WtStraQS::checkB1(double zge, double dage, double j, int score)
{
	return (zge > dage * 0.99) && (j <= _b1JThreshold) && (score >= _minScore);
}

bool WtStraQS::checkB2(double zge, double dage, double j, int score,
                          double curVol, double volMA4, double yestChange, double todayChange,
                          const double* jArr, int jLen)
{
	if (j >= 80) return false;
	if (zge <= dage) return false;
	if (score < 3) return false;

	// 近3日内J<15
	bool jBelow15 = false;
	for (int i = max(0, jLen - 3); i < jLen; i++)
	{
		if (jArr[i] < 15) { jBelow15 = true; break; }
	}
	if (!jBelow15) return false;

	if (curVol <= volMA4 * 1.25) return false;
	if (yestChange >= 5.5) return false;
	if (todayChange <= 3.8) return false;

	return true;
}

bool WtStraQS::checkB3(double zge, double dage, double j, int score,
                          double curVol, double prevVol, double prevPrevVol,
                          double yestChange, double todayChange,
                          const double* jArr, int jLen)
{
	if (zge <= dage) return false;
	if (score < 3) return false;

	// 近4日内J<15
	bool jBelow15 = false;
	for (int i = max(0, jLen - 4); i < jLen; i++)
	{
		if (jArr[i] < 15) { jBelow15 = true; break; }
	}
	if (!jBelow15) return false;

	// 昨日放量
	if (prevVol <= prevPrevVol) return false;
	// 昨涨>3%
	if (yestChange <= 3.0) return false;
	// 今涨<4%
	if (todayChange >= 4.0) return false;

	// 真突破
	double volRatio = (prevVol > 0) ? curVol / prevVol : 1;
	bool shrinkHalf = volRatio < 0.6;
	bool flatVol = volRatio > 0.9;
	bool trueBreak = todayChange > 0 || (todayChange > -3 && (shrinkHalf || flatVol));
	if (!trueBreak) return false;

	return true;
}

// ============= 生命周期 =============

void WtStraQS::on_session_begin(ICtaStraCtx* ctx, uint32_t uTDate)
{
	std::string newMonCode = _isstk ? _code : ctx->stra_get_rawcode(_code.c_str());
	if (newMonCode != _moncode)
	{
		if (!_moncode.empty())
		{
			double curPos = ctx->stra_get_position(_moncode.c_str());
			if (!decimal::eq(curPos, 0))
			{
				ctx->stra_log_info(fmt::format("主力换月, 老主力{}[{}]清理", _moncode, curPos).c_str());
				ctx->stra_set_position(_moncode.c_str(), 0, "switchout");
				ctx->stra_set_position(newMonCode.c_str(), curPos, "switchin");
			}
		}
		_moncode = newMonCode;
	}

	// 持仓天数递增
	if (_inPosition)
		_holdDays++;
}

void WtStraQS::on_init(ICtaStraCtx* ctx)
{
	ctx->stra_sub_ticks(_code.c_str());

	WTSKlineSlice* kline = ctx->stra_get_bars(_code.c_str(), _period.c_str(), _count, true);
	if (kline)
		kline->release();

	// 注册指标和图表
	ctx->set_chart_kline(_code.c_str(), _period.c_str());
	ctx->register_index("ZGe", 0);
	ctx->register_index_line("ZGe", "zge_line", 0);
	ctx->register_index_line("ZGe", "dage_line", 0);
	ctx->register_index_line("ZGe", "bbi", 0);
}

void WtStraQS::on_tick(ICtaStraCtx* ctx, const char* stdCode, WTSTickData* newTick)
{
	// 盘中不做额外处理
}

void WtStraQS::on_schedule(ICtaStraCtx* ctx, uint32_t curDate, uint32_t curTime)
{
	WTSKlineSlice* kline = ctx->stra_get_bars(_code.c_str(), _period.c_str(), _count, true);
	if (kline == NULL || kline->size() < 120)
	{
		if (kline) kline->release();
		return;
	}

	int len = (int)kline->size();

	// 提取数据
	std::vector<double> closes(len), highs(len), lows(len), volumes(len);
	for (int i = 0; i < len; i++)
	{
		const WTSBarStruct* bar = kline->at(i);
		closes[i] = bar->close;
		highs[i] = bar->high;
		lows[i] = bar->low;
		volumes[i] = bar->vol;
	}

	// ============= 计算指标 =============

	// 强生白线 = EMA(EMA(C,10),10)
	std::vector<double> ema1(len);
	ema1[0] = closes[0];
	for (int i = 1; i < len; i++)
		ema1[i] = closes[i] * (2.0/11.0) + ema1[i-1] * (9.0/11.0);

	std::vector<double> zgeArr(len);
	zgeArr[0] = ema1[0];
	for (int i = 1; i < len; i++)
		zgeArr[i] = ema1[i] * (2.0/11.0) + zgeArr[i-1] * (9.0/11.0);
	double zge = zgeArr[len-1];
	double prevZge = zgeArr[len-2];

	// 大哥线 = (MA14+MA28+MA57+MA114)/4
	double dage = 0;
	if (len >= 114)
		dage = (calcMA(closes.data(), len, 14) + calcMA(closes.data(), len, 28) +
		        calcMA(closes.data(), len, 57) + calcMA(closes.data(), len, 114)) / 4.0;
	else
		dage = calcMA(closes.data(), len, 14); // fallback
	double prevDage = 0;
	if (len >= 115)
		prevDage = (calcMA(closes.data(), len-1, 14) + calcMA(closes.data(), len-1, 28) +
		           calcMA(closes.data(), len-1, 57) + calcMA(closes.data(), len-1, 114)) / 4.0;

	// BBI
	double bbi = calcBBI(closes.data(), len);

	// MA20
	double ma20 = calcMA(closes.data(), len, 20);

	// KDJ
	double k, d, j;
	calcKDJ(highs.data(), lows.data(), closes.data(), len, k, d, j);

	// 计算前一根的KDJ
	double prevK, prevD, prevJ;
	calcKDJ(highs.data(), lows.data(), closes.data(), len-1, prevK, prevD, prevJ);

	// J数组 (最近5根)
	double jArr[5];
	for (int i = 0; i < 5 && (len - 5 + i) >= 0; i++)
	{
		double tmpK, tmpD, tmpJ;
		calcKDJ(highs.data(), lows.data(), closes.data(), len - 5 + i, tmpK, tmpD, tmpJ);
		jArr[i] = tmpJ;
	}

	// 当前价格
	double curClose = closes[len-1];
	double prevClose = closes[len-2];
	double prevPrevClose = (len >= 3) ? closes[len-3] : prevClose;
	double curVol = volumes[len-1];
	double prevVol = volumes[len-2];
	double prevPrevVol = (len >= 3) ? volumes[len-3] : prevVol;

	// 评分
	int score = calcScore(curClose, prevClose, bbi, prevJ, prevD, j, curVol, prevVol, ma20);

	// 涨跌幅
	double todayChange = (prevClose > 0) ? (curClose / prevClose - 1.0) * 100.0 : 0;
	double yestChange = (prevPrevClose > 0) ? (prevClose / prevPrevClose - 1.0) * 100.0 : 0;

	// 成交量均线
	double volMA4 = 0;
	{
		int cnt = 0;
		for (int i = max(0, len-5); i < len-1; i++)
		{ volMA4 += volumes[i]; cnt++; }
		volMA4 = (cnt > 0) ? volMA4 / cnt : curVol;
	}

	// ATR计算 (14日)
	double atr = 0;
	{
		double trSum = 0;
		int atrLen = min(len, (int)_atrPeriod + 1);
		for (int i = len - atrLen + 1; i < len; i++)
		{
			double tr1 = highs[i] - lows[i];
			double tr2 = fabs(highs[i] - closes[i-1]);
			double tr3 = fabs(lows[i] - closes[i-1]);
			trSum += max(tr1, max(tr2, tr3));
		}
		atr = trSum / (atrLen - 1);
	}

	// 趋势判断
	bool trendUp = zge > dage;

	// ============= 交易逻辑 =============

	uint32_t trdUnit = _isstk ? 100 : 1;

	if (!_inPosition)
	{
		// 开仓条件: 趋势向上 + 评分 >= 3 + 有信号
		if (trendUp && score >= 3)
		{
			bool b1 = checkB1(zge, dage, j, score);
			bool b2 = checkB2(zge, dage, j, score, curVol, volMA4, yestChange, todayChange, jArr, 5);
			bool b3 = checkB3(zge, dage, j, score, curVol, prevVol, prevPrevVol, yestChange, todayChange, jArr, 5);

			if (b1 || b2 || b3)
			{
				const char* signalName = b1 ? "B1" : (b2 ? "B2" : "B3");
				ctx->stra_enter_long(_moncode.c_str(), _lots * trdUnit, signalName, 0, 0);
				_costPrice = curClose;
				_stopLossPrice = curClose - _atrMultiplier * atr;
				_holdDays = 0;
				_inPosition = true;

				ctx->stra_log_info(fmt::format("强生买入[{}]: 价格={:.2f}, 评分={}, J={:.1f}, 强生={:.2f}, 大哥={:.2f}",
					signalName, curClose, score, j, zge, dage).c_str());
				ctx->add_chart_mark(curClose, "wt-mark-buy", signalName);
			}
		}
	}
	else
	{
		// 平仓逻辑
		std::string sellReason;
		bool shouldSell = false;

		// 1. 死叉清仓 (任何时候)
		if (prevZge >= prevDage && zge < dage)
		{
			sellReason = "死叉清仓";
			shouldSell = true;
		}
		// 2. 破止损 (任何时候)
		else if (curClose < _stopLossPrice)
		{
			sellReason = "破止损";
			shouldSell = true;
		}
		// 2b. 亏损超10%强制走
		else if (curClose / _costPrice < 0.90)
		{
			sellReason = "亏损超10%";
			shouldSell = true;
		}
		// 2c. 跌破大哥线
		else if (curClose < dage && prevClose >= prevDage)
		{
			sellReason = "破大哥线";
			shouldSell = true;
		}
		// 3. 止盈 (任何时候)
		else if (curClose / _costPrice >= _takeProfitRatio)
		{
			sellReason = "止盈";
			shouldSell = true;
		}
		// 4. 评分过低 (持仓 >= N天)
		else if (_holdDays >= _holdDaysForSell && score <= 1)
		{
			sellReason = "评分过低";
			shouldSell = true;
		}
		// 5. 趋势转弱 (持仓 >= N天)
		else if (_holdDays >= _holdDaysForTrend && !trendUp)
		{
			sellReason = "趋势转弱";
			shouldSell = true;
		}

		if (shouldSell)
		{
			double pnl = (curClose / _costPrice - 1.0) * 100.0;
			ctx->stra_exit_long(_moncode.c_str(), _lots * trdUnit, sellReason.c_str(), 0, 0);

			ctx->stra_log_info(fmt::format("强生卖出[{}]: 价格={:.2f}, 成本={:.2f}, 收益={:.2f}%, 持仓{}天, 评分={}",
				sellReason, curClose, _costPrice, pnl, _holdDays, score).c_str());
			ctx->add_chart_mark(curClose, "wt-mark-sell", sellReason.c_str());

			_inPosition = false;
			_costPrice = 0;
			_stopLossPrice = 0;
			_holdDays = 0;
		}
	}

	// 设置指标值用于图表显示
	ctx->set_index_value("ZGe", "zge_line", zge);
	ctx->set_index_value("ZGe", "dage_line", dage);
	ctx->set_index_value("ZGe", "bbi", bbi);

	kline->release();
}
