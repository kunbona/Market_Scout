"""
拉取申万一级行业官方指数日K线并缓存
====================================
用法:
    python quant/fetch_sw_industry_index.py [--limit 2000] [--refresh]

数据源: westockdata (腾讯自选股) 板块指数通道, 代码 pt01801801 系列 = 申万一级行业官方指数。
    npx -y westock-data-skillhub@1.0.5 kline pt01801010,pt01801150 --period day --limit N

为什么用官方指数: 自建"今日成分回溯"指数有幸存者偏差+新股涨幅注入(医药案例:
自建等权后复权 5y位置 83.5% vs 官方申万 40.9%), 官方指数每天用"当时"的成分和
权重编制, 无回溯偏差。详见 2026-08-21 诊断。

缓存: quant/data/sw_industry_index.parquet (长表: date, code, name, open, close, high, low)
    --refresh 时全量重拉; 默认增量(仅当本地最后一根K线落后于接口最新日期时重拉)。
"""
import subprocess
import sys
import argparse
from pathlib import Path
import pandas as pd

QUANT = Path("/Users/kun/Documents/market-radar/quant")
CACHE = QUANT / "data" / "sw_industry_index.parquet"
NPM = "npx"
PKG = "westock-data-skillhub@1.0.5"
CHUNK = 8  # 每次批量查询的代码数

# 31 个申万一级行业(2021版) -> westockdata 板块指数代码
# 除 电子/汽车 由规律推断(pt01801+申万三位码)并经成分股验证外, 其余 29 个均由
# `search <行业名> --type sector` 逐一查得(2026-08-21)。
SW_INDUSTRY_CODES = {
    "农林牧渔": "pt01801010", "基础化工": "pt01801030", "钢铁": "pt01801040",
    "有色金属": "pt01801050", "电子": "pt01801080", "家用电器": "pt01801110",
    "食品饮料": "pt01801120", "纺织服饰": "pt01801130", "轻工制造": "pt01801140",
    "医药生物": "pt01801150", "公用事业": "pt01801160", "交通运输": "pt01801170",
    "房地产": "pt01801180", "商贸零售": "pt01801200", "社会服务": "pt01801210",
    "综合": "pt01801230", "建筑材料": "pt01801710", "建筑装饰": "pt01801720",
    "电力设备": "pt01801730", "国防军工": "pt01801740", "计算机": "pt01801750",
    "传媒": "pt01801760", "通信": "pt01801770", "银行": "pt01801780",
    "非银金融": "pt01801790", "汽车": "pt01801880", "机械设备": "pt01801890",
    "煤炭": "pt01801950", "石油石化": "pt01801960", "环保": "pt01801970",
    "美容护理": "pt01801980",
}


def fetch_klines(codes: list[str], limit: int) -> pd.DataFrame:
    """批量拉取日K线, 解析 markdown 表格 -> DataFrame(date, code, open, close, high, low, volume, amount)。
    注意: 批量(>=2个代码)返回含代码列(| ptXXX | date | ...), 单代码返回不含代码列
    (| date | open | last | ...), 两种格式都要兼容。
    volume/amount 单位: 成交量(手/股数, 指数为成分合计)、成交额(元)。"""
    cmd = [NPM, "-y", PKG, "kline", ",".join(codes), "--period", "day", "--limit", str(limit)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600).stdout
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not (line.startswith("| pt") or line.startswith("| 2")):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 7:
            continue
        if cells[0].startswith("pt"):
            code, cells = cells[0], cells[1:]  # 批量格式: 去掉代码列, 剩 date/open/close/...
        elif len(codes) == 1:
            code = codes[0]                    # 单代码格式: 无代码列, 行首即日期
        else:
            continue
        try:
            rows.append({
                "date": pd.Timestamp(cells[0]),
                "code": code,
                "open": float(cells[1]),
                "close": float(cells[2]),
                "high": float(cells[3]),
                "low": float(cells[4]),
                "volume": float(cells[5]),
                "amount": float(cells[6]),
            })
        except (ValueError, IndexError):
            continue
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2000, help="每行业拉取的K线根数(上限2000)")
    ap.add_argument("--refresh", action="store_true", help="忽略增量判断, 全量重拉")
    args = ap.parse_args()

    names = list(SW_INDUSTRY_CODES)
    code2name = {v: k for k, v in SW_INDUSTRY_CODES.items()}

    # 增量判断: 本地缓存已含接口最新交易日则跳过; 落后时按缺口大小自适应 limit
    # (缺几天就只拉几十根, 而非每次全量 2000 根——实测单次调用固定开销约1.4s,
    #  数据量对耗时影响小, 但小 limit 对免费接口更友好)。
    if CACHE.exists() and not args.refresh:
        old = pd.read_parquet(CACHE)
        old_max = old["date"].max()
        probe = fetch_klines([SW_INDUSTRY_CODES[names[0]]], 1)
        if not probe.empty and probe["date"].max() <= old_max:
            print(f"[跳过] 缓存已是最新(本地最新={old_max.date()}, 接口最新={probe['date'].max().date()})")
            print(f"[缓存] {CACHE}")
            return
        # 缺口(自然日) -> 拉取根数: 交易日≈自然日*5/7, 留足余量, 下限30根
        if not probe.empty:
            gap_days = (probe["date"].max() - old_max).days
            args.limit = max(30, int(gap_days * 5 / 7) + 15)
            print(f"[增量] 本地最新={old_max.date()}, 接口最新={probe['date'].max().date()}, "
                  f"缺口{gap_days}自然日 -> 每行业拉 {args.limit} 根")

    frames = []
    codes = [SW_INDUSTRY_CODES[n] for n in names]
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        df = fetch_klines(chunk, args.limit)
        got = df["code"].nunique() if not df.empty else 0
        print(f"[拉取] {i+1}-{min(i+CHUNK, len(codes))}/{len(codes)}: {got}/{len(chunk)} 个行业成功")
        if df.empty:
            continue
        frames.append(df)
    if not frames:
        print("[失败] 未取到任何数据", file=sys.stderr)
        sys.exit(1)

    new = pd.concat(frames, ignore_index=True)
    new["name"] = new["code"].map(code2name)
    # 合并去重(同日同代码保留新数据)
    if CACHE.exists():
        old = pd.read_parquet(CACHE)
        old = old[~old.set_index(["code", "date"]).index.isin(new.set_index(["code", "date"]).index)]
        new = pd.concat([old, new], ignore_index=True)
    new = new.sort_values(["name", "date"]).reset_index(drop=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    new.to_parquet(CACHE, index=False)

    print(f"[输出] {CACHE}")
    cnt = new.groupby("name")["close"].count()
    rng = new.groupby("name")["date"].agg(["min", "max"])
    print(f"[汇总] {new['name'].nunique()} 个行业, 共 {len(new)} 根日K")
    for n in names:
        if n in cnt.index:
            print(f"  {n:6s} {cnt[n]:5d} 根  {rng.loc[n,'min'].date()} ~ {rng.loc[n,'max'].date()}")
        else:
            print(f"  {n:6s} 缺失!")


if __name__ == "__main__":
    main()
