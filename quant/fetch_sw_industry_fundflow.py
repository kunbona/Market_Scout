"""
拉取申万一级行业资金流并缓存
==============================
用法:
    python quant/fetch_sw_industry_fundflow.py [--refresh]

数据源: westockdata (腾讯自选股) `fund flow pt01801XXX` —— 申万一级行业板块级
日度资金流(单位: 元): 主力流入/流出/净额、散户流入/流出。

单位与口径已三重校验(2026-08-21):
    1. 主力净额 + 散户净额 = 0 (零和, 每笔成交买卖必有对手盘, 偏差<0.01元);
    2. (主力In+Out+散户In+Out)/2 = 申万指数当日成交额 (比值 0.995~1.001),
       即每笔成交记一次买一次卖, 主力参与占比约 26%~46% (正常大单占比);
    3. 接口 ClosePrice 与申万指数缓存收盘价逐点一致 (偏差 0.0000)。
    主力净额/成交额 在 ±5% 量级, 展示换算成亿。

注意: 接口返回列不固定(早期日期缺 BlockNetFlow/JumboNetFlow 等列, 批量含 symbol
列, 单代码不含), 一律按表头名解析, 不按位置。

缓存: quant/data/sw_industry_fundflow.parquet
    长表: date, code, name, main_in, main_out, main_net, retail_in, retail_out
    --refresh 全量重拉(默认 2025-08-01 起, 覆盖 120 日窗口 + 余量);
    默认增量(缓存最新日落后于接口时, 从缓存最新日前 3 天重叠补拉)。
"""
import subprocess
import sys
import argparse
from pathlib import Path
import pandas as pd

QUANT = Path(__file__).resolve().parent
sys.path.insert(0, str(QUANT))
from fetch_sw_industry_index import SW_INDUSTRY_CODES  # noqa: E402  (同一份 31 行业代码表, 不重复维护)

CACHE = QUANT / "data" / "sw_industry_fundflow.parquet"
NPM = "npx"
PKG = "westock-data-skillhub@1.0.5"
CHUNK = 8  # 每次批量查询的代码数
DEFAULT_START = "2025-08-01"  # 全量起点: 覆盖 120 日窗口 + 余量

# 接口列名 -> 缓存列名(单位均为元)
KEEP = {"MainInFlow": "main_in", "MainOutFlow": "main_out",
        "MainNetFlow": "main_net", "RetailInFlow": "retail_in",
        "RetailOutFlow": "retail_out"}


def fetch_fundflow(codes: list[str], start: str, end: str) -> pd.DataFrame:
    """批量拉取区间资金流, 解析 markdown 表格 -> DataFrame(date, code, main_*, retail_*)。
    返回列不固定(批量含 symbol 列、早期日期缺部分列), 按表头名取数。"""
    cmd = [NPM, "-y", PKG, "fund", "flow", ",".join(codes),
           "--start", start, "--end", end]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=600).stdout
    lines = [l for l in out.splitlines() if l.strip().startswith("|")]
    # 表头行: 含 date 与 Main 列的行
    header_idx = next((i for i, l in enumerate(lines)
                       if "date" in l and "Main" in l), None)
    if header_idx is None:
        return pd.DataFrame()
    header = [c.strip() for c in lines[header_idx].strip("|").split("|")]
    rows = []
    for l in lines[header_idx + 1:]:
        cells = [c.strip() for c in l.strip("|").split("|")]
        if len(cells) != len(header):
            continue
        rec = dict(zip(header, cells))
        code = rec.get("code") or rec.get("symbol")
        if not code or not str(code).startswith("pt"):
            continue
        try:
            date = pd.Timestamp(rec.get("date"))
        except Exception:
            continue
        if pd.isna(date):
            continue
        row = {"date": date, "code": code}
        for src, dst in KEEP.items():
            v = rec.get(src)
            try:
                row[dst] = float(v) if v not in (None, "", "-") else None
            except (ValueError, TypeError):
                row[dst] = None
        rows.append(row)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="忽略增量判断, 全量重拉")
    args = ap.parse_args()

    names = list(SW_INDUSTRY_CODES)
    code2name = {v: k for k, v in SW_INDUSTRY_CODES.items()}
    today = pd.Timestamp.today().strftime("%Y-%m-%d")

    start = DEFAULT_START
    if CACHE.exists() and not args.refresh:
        old = pd.read_parquet(CACHE)
        old_max = old["date"].max()
        probe = fetch_fundflow([SW_INDUSTRY_CODES[names[0]]], today, today)
        if not probe.empty and probe["date"].max() <= old_max:
            print(f"[跳过] 缓存已是最新(本地最新={old_max.date()})")
            print(f"[缓存] {CACHE}")
            return
        # 增量: 从缓存最新日前 3 天重叠补拉(防数据源补录/修正)
        start = (old_max - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        print(f"[增量] 本地最新={old_max.date()}, 从 {start} 补拉")

    frames = []
    codes = [SW_INDUSTRY_CODES[n] for n in names]
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        df = fetch_fundflow(chunk, start, today)
        got = df["code"].nunique() if not df.empty else 0
        print(f"[拉取] {i+1}-{min(i+CHUNK, len(codes))}/{len(codes)}: {got}/{len(chunk)} 个行业成功")
        if not df.empty:
            frames.append(df)
    if not frames:
        print("[失败] 未取到任何数据", file=sys.stderr)
        sys.exit(1)

    new = pd.concat(frames, ignore_index=True)
    new["name"] = new["code"].map(code2name)
    if CACHE.exists():
        old = pd.read_parquet(CACHE)
        old = old[~old.set_index(["code", "date"]).index.isin(
            new.set_index(["code", "date"]).index)]
        new = pd.concat([old, new], ignore_index=True)
    new = new.sort_values(["name", "date"]).reset_index(drop=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    new.to_parquet(CACHE, index=False)

    print(f"[输出] {CACHE}")
    cnt = new.groupby("name")["main_net"].count()
    rng = new.groupby("name")["date"].agg(["min", "max"])
    print(f"[汇总] {new['name'].nunique()} 个行业, 共 {len(new)} 行")
    missing = [n for n in names if n not in cnt.index]
    for n in names:
        if n in cnt.index:
            print(f"  {n:6s} {cnt[n]:5d} 天  {rng.loc[n, 'min'].date()} ~ {rng.loc[n, 'max'].date()}")
    if missing:
        print(f"[缺失] {missing}", file=sys.stderr)


if __name__ == "__main__":
    main()
