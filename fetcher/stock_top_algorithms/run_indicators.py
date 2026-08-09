"""
指标计算运行器

统一执行以下任务：
1. 调用API拉取所有新数据
2. 计算所有已配置的指标
3. 生成周期信号（牛熊定位 + 逃顶/抄底）

使用方法：
    python run_indicators.py
"""

import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
import pandas as pd

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from program.api import fetch_all_data
from program.api.load_xbx_data import load_xbx_data
from program.indicators import (
    IndicatorManager,
    EquityPremiumIndicator,
    PEValuationIndicator,
    PricePrecentileIndicator,
    MarketTurnoverPercentileIndicator,
    MA250BiasIndicator,
    MarketCrowdednessIndicator,
    BelowNetAssetIndicator,
    HerdingRateIndicator,
)
from config import (
    ENABLED_INDICATORS,
    RAW_DATA_DIR,
    DATA_START_DATE
)


def fetch_all_api_data() -> Dict[str, Any]:
    """调用API拉取所有新数据
    
    Returns:
        API调用结果字典
    """
    print("=" * 80)
    print("步骤 1/3: 调用API拉取所有新数据")
    print("=" * 80)
    print()
    
    try:
        # 注意：这里我们不需要显式调用 load_xbx_data，因为 fetch_all_data 默认已集成
        result = fetch_all_data(
            save_raw=True,
            save_processed=True,
            equity_bond_spread_code="000300.SH",
            stock_index_pe_symbols=["上证50", "沪深300", "中证500", "中证1000"],
            bond_zh_us_rate_start_date="20050101",
            stock_zh_index_daily_symbols=["sh000001", "sz399006", "sh000300", "sz399303", "sh000852", "sh000688"]
        )
        
        print(f"✓ API调用完成")
        print(f"  执行时间: {result['timestamp']}")
        print(f"  全部成功: {result['success']}")
        print(f"  成功数量: {result['summary']['success_count']}")
        print(f"  失败数量: {result['summary']['failed_count']}")
        print()
        
        # 显示失败的API（如果有）
        if result['summary']['failed_count'] > 0:
            print("⚠ 以下API调用失败:")
            for key, value in result['results'].items():
                if not value.get('success', False):
                    print(f"  - {key}: {value.get('error', '未知错误')}")
            print()
        
        return result
        
    except Exception as e:
        print(f"❌ API调用失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'results': {},
            'summary': {'success_count': 0, 'failed_count': 0}
        }


def register_all_indicators(manager: IndicatorManager) -> None:
    """注册所有需要计算的指标
    
    Args:
        manager: 指标管理器实例
    """
    print("=" * 80)
    print("步骤 2/3: 注册并计算所有指标")
    print("=" * 80)
    print()
    
    # 注册股权溢价指数指标
    if "hs300_equity_premium" in ENABLED_INDICATORS:
        try:
            equity_premium = EquityPremiumIndicator(
                name="hs300_equity_premium",
                high_threshold=0.06,
                low_threshold=0.03,
                save_processed=True
            )
            manager.register(equity_premium, name="hs300_equity_premium")
            print(f"✓ 已注册指标: hs300_equity_premium")
        except Exception as e:
            print(f"❌ 注册指标 hs300_equity_premium 失败: {str(e)}")
    
    # 注册PE估值指标（根据config中的权重配置）
    pe_symbols = [
        ("hs300_pe_valuation", "沪深300"),
        ("sz50_pe_valuation", "上证50"),
        ("zz500_pe_valuation", "中证500"),
        ("zz1000_pe_valuation", "中证1000"),
    ]
    
    for indicator_name, symbol in pe_symbols:
        if indicator_name in ENABLED_INDICATORS:
            try:
                pe_valuation = PEValuationIndicator(
                    name=indicator_name,
                    pe_type=None,  # 使用config中的默认PE类型
                    save_processed=True
                )
                manager.register(pe_valuation, name=indicator_name)
                print(f"✓ 已注册指标: {indicator_name} (指数: {symbol})")
            except Exception as e:
                print(f"❌ 注册指标 {indicator_name} 失败: {str(e)}")

    # 注册价格分位数指标
    if "price_percentile" in ENABLED_INDICATORS:
        try:
            price_percentile = PricePrecentileIndicator(
                name="price_percentile",
                save_processed=True
            )
            manager.register(price_percentile, name="price_percentile")
            print(f"✓ 已注册指标: price_percentile")
        except Exception as e:
            print(f"❌ 注册指标 price_percentile 失败: {str(e)}")

    # 注册全市场换手率分位数指标
    if "market_turnover_percentile" in ENABLED_INDICATORS:
        try:
            market_turnover = MarketTurnoverPercentileIndicator(
                name="market_turnover_percentile"
            )
            manager.register(market_turnover, name="market_turnover_percentile")
            print(f"✓ 已注册指标: market_turnover_percentile")
        except Exception as e:
            print(f"❌ 注册指标 market_turnover_percentile 失败: {str(e)}")

    # 注册全市场MA250 Bias指标
    if "ma250_bias" in ENABLED_INDICATORS:
        try:
            ma250_bias = MA250BiasIndicator(
                name="ma250_bias",
                save_processed=True
            )
            manager.register(ma250_bias, name="ma250_bias")
            print(f"✓ 已注册指标: ma250_bias")
        except Exception as e:
            print(f"❌ 注册指标 ma250_bias 失败: {str(e)}")

    # 注册大盘拥挤度指标
    if "market_crowdedness" in ENABLED_INDICATORS:
        try:
            market_crowdedness = MarketCrowdednessIndicator(
                name="market_crowdedness",
                save_processed=True
            )
            manager.register(market_crowdedness, name="market_crowdedness")
            print(f"✓ 已注册指标: market_crowdedness")
        except Exception as e:
            print(f"❌ 注册指标 market_crowdedness 失败: {str(e)}")

    # 注册破净率指标
    if "below_net_asset" in ENABLED_INDICATORS:
        try:
            below_net_asset = BelowNetAssetIndicator(
                symbol="全部A股",
                name="below_net_asset",
                save_processed=True
            )
            manager.register(below_net_asset, name="below_net_asset")
            print(f"✓ 已注册指标: below_net_asset")
        except Exception as e:
            print(f"❌ 注册指标 below_net_asset 失败: {str(e)}")

    # 注册抱团率指标
    if "herding_rate" in ENABLED_INDICATORS:
        try:
            herding_rate = HerdingRateIndicator(
                name="herding_rate",
                save_processed=True
            )
            manager.register(herding_rate, name="herding_rate")
            print(f"✓ 已注册指标: herding_rate")
        except Exception as e:
            print(f"❌ 注册指标 herding_rate 失败: {str(e)}")

    print()
    print(f"总共注册了 {len(manager.list_indicators())} 个指标")
    print()


def load_shared_stock_data() -> Optional[pd.DataFrame]:
    """加载共享的股票数据，优先使用Parquet"""
    print("正在加载共享股票数据...")
    parquet_path = RAW_DATA_DIR / "xbx_stock_data.parquet"
    
    # 定义指标计算所需的最小列集 (Column Pruning)
    required_cols = ['交易日期', '股票代码', '收盘价_复权', '成交额', '流通市值']
    
    if parquet_path.exists():
        try:
            print(f"从Parquet文件加载数据: {parquet_path}")
            # 只读取必需列
            df = pd.read_parquet(parquet_path, columns=required_cols)
            
            # 类型优化 (Downcasting)
            # 股票代码转 category
            df['股票代码'] = df['股票代码'].astype('category')
            
            # 浮点数转 float32
            float_cols = ['收盘价_复权', '成交额', '流通市值']
            for col in float_cols:
                if col in df.columns:
                    df[col] = df[col].astype('float32')
            
            return df
        except Exception as e:
            print(f"加载Parquet文件失败: {e}")
    
    # Fallback to load_xbx_data
    print("尝试从原始CSV文件加载数据...")
    # 使用配置的数据开始日期，确保有足够历史数据用于计算
    result = load_xbx_data(start_date=DATA_START_DATE, save_to_file=True)
    if result['success']:
        df = result['data']
        # 对fallback数据也进行同样的列裁剪和类型优化
        if df is not None:
             # 确保列存在
            existing_cols = [c for c in required_cols if c in df.columns]
            df = df[existing_cols].copy()
            
            if '股票代码' in df.columns:
                df['股票代码'] = df['股票代码'].astype('category')
                
            float_cols = ['收盘价_复权', '成交额', '流通市值']
            for col in float_cols:
                if col in df.columns:
                    df[col] = df[col].astype('float32')
        return df
    else:
        print(f"加载股票数据失败: {result.get('error')}")
        return None


def calculate_all_indicators(manager: IndicatorManager) -> Dict[str, Any]:
    """计算所有已注册的指标
    
    Args:
        manager: 指标管理器实例
        
    Returns:
        指标计算结果字典
    """
    print("开始计算所有指标...")
    print()

    # 预先加载共享数据，避免每个指标重复加载占用内存
    shared_stock_data = load_shared_stock_data()

    # 刷新抱团率原始数据（从 XBX 自动生成 herding_rate_daily.csv，
    # 与其他 XBX 依赖指标同批更新，消除对外部预计算文件的依赖）
    if shared_stock_data is not None:
        try:
            from program.indicators.herding_rate_source import generate_herding_daily
            generate_herding_daily(stock_data=shared_stock_data, save=True)
        except Exception as e:
            print(f"⚠ 抱团率原始数据刷新失败（将沿用已有文件）: {e}")

    # 刷新市场广度数据（站上MA60占比 + 历史分位）。
    # 这是周期信号的"背离分类器"输入，不进等权打分，故不在 manager 注册，
    # 直接产出 breadth_above_ma60.csv 供 cycle_signal runner 消费。
    if shared_stock_data is not None:
        try:
            from program.indicators.breadth_above_ma_indicator import load_and_compute_breadth
            load_and_compute_breadth(stock_data=shared_stock_data, save=True)
        except Exception as e:
            print(f"⚠ 市场广度数据刷新失败（周期信号将降级，不做广度分类）: {e}")
    
    # 准备指标特定参数
    indicator_specific_params = {
        "hs300_equity_premium": {
            "symbol": "沪深300",
            "bond_start_date": "20050101"
        },
        "hs300_pe_valuation": {
            "symbol": "沪深300"
        },
        "sz50_pe_valuation": {
            "symbol": "上证50"
        },
        "zz500_pe_valuation": {
            "symbol": "中证500"
        },
        "zz1000_pe_valuation": {
            "symbol": "中证1000"
        },
    }
    
    # 计算所有指标
    # 将共享数据注入到common_params中
    common_params = {}
    if shared_stock_data is not None:
        common_params["stock_data"] = shared_stock_data

    results = manager.calculate_all(
        common_params=common_params,
        indicator_specific_params=indicator_specific_params
    )
    
    # 释放内存
    if shared_stock_data is not None:
        del shared_stock_data
        import gc
        gc.collect()
    
    # 显示每个指标的计算结果
    print("各指标计算结果:")
    print("-" * 80)
    for indicator_name, result in results.items():
        if result.extra_data and result.extra_data.get("error", False):
            print(f"❌ {indicator_name}: 计算失败 - {result.message}")
        else:
            percentile = result.risk_percentage / 100.0
            print(f"✓ {indicator_name}:")
            print(f"    风险百分比: {result.risk_percentage:.2f}%")
            print(f"    风险等级: {result.risk_level.value}")
            print(f"    分位数: {percentile:.4f}")
            if result.message:
                print(f"    提示: {result.message}")
    print()
    
    return results


def build_and_calculate():
    """注册并计算所有启用的指标

    生产链路只用等权周期分数 B（cycle_signal），不再计算 config 加权综合得分。
    本函数负责注册 ENABLED_INDICATORS 中的指标、计算并写出 processed CSV，
    这些 CSV 即周期信号的输入。

    Returns:
        tuple: (manager, results)
            - manager: IndicatorManager 实例
            - results: 指标计算结果字典
    """
    manager = IndicatorManager()
    register_all_indicators(manager)

    if len(manager.list_indicators()) == 0:
        print("❌ 没有注册任何指标，请检查config.py中的ENABLED_INDICATORS配置")
        return manager, {}

    results = calculate_all_indicators(manager)

    return manager, results


def generate_visualization():
    """生成牛熊周期定位图 + 逃顶/抄底信号（统一后唯一产出）"""
    print("=" * 80)
    print("步骤 3/3: 生成周期信号图（牛熊定位 + 逃顶/抄底）")
    print("=" * 80)
    from program.cycle_signal.runner import run_cycle_signal_step
    run_cycle_signal_step()


def main():
    """主函数"""
    print()
    print("=" * 80)
    print("指标计算运行器")
    print("=" * 80)
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 步骤1: 调用API拉取数据
    api_result = fetch_all_api_data()
    
    # 即使部分API失败，也继续执行指标计算
    if api_result['summary']['success_count'] == 0:
        print("⚠ 警告: 所有API调用都失败了，但将继续尝试计算指标（使用已有数据）")
        print()
    
    # 步骤2: 注册并计算指标（写出 processed CSV，供周期信号消费）
    manager, results = build_and_calculate()
    
    if not results:
        return
    
    # 步骤3: 生成周期信号图（统一后唯一产出）
    generate_visualization()
    
    # 总结
    print("=" * 80)
    print("执行完成")
    print("=" * 80)
    print()
    
    print(f"✓ 参与计算的指标: {len(results)} 个")
    
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠ 用户中断执行")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ 执行失败: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
