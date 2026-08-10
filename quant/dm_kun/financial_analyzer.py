
import pandas as pd
import os
import glob
import logging
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class FinancialAnalyzer:
    def __init__(self, financial_data_dir=None, analyst_data_dir=None):
        self.financial_data_dir = financial_data_dir or r'D:\股票数据\A股数据\stock-fin-data-xbx'
        self.analyst_data_dir = analyst_data_dir or r'D:\股票数据\A股数据\stock-analyst-ranking'
        self.column_mapping = {}
        # Key metrics mapping (hardcoded for stability based on exploration)
        self.metrics_map = {
            'revenue': 'R_revenue@xbx',  # 营业收入
            'net_profit_parent': 'R_np_atoopc@xbx',  # 归母净利润
            'total_equity_parent': 'B_total_equity_atoopc@xbx'  # 归母所有者权益
        }

    def get_financial_file(self, stock_code):
        """Find the financial data file for a stock."""
        # Pattern: stock_code_*.csv inside stock_code folder
        pattern = os.path.join(self.financial_data_dir, stock_code, f"{stock_code}_*.csv")
        files = glob.glob(pattern)
        if files:
            return files[0]
        return None

    def get_analyst_file(self, stock_code):
        """Find the analyst data file for a stock."""
        # Pattern: stock-analyst-rank-*.csv
        # Usually it's one big file or per stock? 
        # Wait, the user provided specific python files earlier: stock-analyst-ranking.py
        # But for data, the user said "读取对于股票的数据".
        # Let's assume there is a directory or file pattern.
        # In previous turns, I used: 
        # file_pattern = os.path.join(r'D:\股票数据\A股数据\stock-analyst-rank-04', f"stock-analyst-rank-{code}.csv")
        # I should check if that directory exists or where it is.
        # User said "我刚添加了财务数据地址和分析师评级的地址".
        # Let's stick to the directory provided in previous context or passed as arg.
        pass

    def read_financial_data(self, stock_code):
        """Read financial data and return a DataFrame with standardized columns."""
        file_path = self.get_financial_file(stock_code)
        if not file_path:
            logging.warning(f"No financial file found for {stock_code}")
            return None

        try:
            # Read header first to check columns
            # The file has a meta line at row 1, header at row 2.
            # So we skip 1 row.
            df = pd.read_csv(file_path, encoding='gbk', skiprows=1, low_memory=False)
            
            # Filter for key columns
            cols_to_keep = ['report_date', 'publish_date'] + list(self.metrics_map.values())
            
            # Check which columns exist
            existing_cols = [c for c in cols_to_keep if c in df.columns]
            
            if not existing_cols:
                return None
                
            df_subset = df[existing_cols].copy()
            
            # Rename columns to friendly names
            rename_map = {v: k for k, v in self.metrics_map.items()}
            df_subset = df_subset.rename(columns=rename_map)
            
            # Sort by report date
            df_subset['report_date'] = pd.to_datetime(df_subset['report_date'], format='%Y%m%d', errors='coerce')
            df_subset = df_subset.sort_values('report_date')
            
            return df_subset
            
        except Exception as e:
            logging.error(f"Error reading financial data for {stock_code}: {e}")
            return None

    def calculate_single_quarter_metrics(self, df):
        """Calculate single quarter revenue and net profit."""
        if df is None or df.empty:
            return None
            
        # Ensure date is datetime
        df['year'] = df['report_date'].dt.year
        df['month'] = df['report_date'].dt.month
        
        # Calculate Single Quarter Data
        # Logic: 
        # Q1 (03-31): Value
        # Semi (06-30): Value - Q1
        # Q3 (09-30): Value - Semi
        # Annual (12-31): Value - Q3
        
        # Create shifted columns for calculation
        # We need to shift by 1 within the same year
        
        df['revenue_sq'] = df['revenue']
        df['net_profit_parent_sq'] = df['net_profit_parent']
        
        # Iterate to calculate differences
        # A vectorized approach:
        # Group by year. Shift 1. If previous is available, subtract.
        # But we must ensure the previous one is the immediate previous quarter (e.g. Q2 - Q1).
        
        # Simple approach:
        # If month == 3: SQ = Cumulative
        # If month > 3: SQ = Cumulative - Previous Cumulative (if Previous exists and is same year)
        
        # Let's use a loop for safety or careful vectorization
        # Vectorized:
        # Get prev_cumulative where year is same and month is prev quarter
        
        # Sort just in case
        df = df.sort_values('report_date')
        
        sq_revenue = []
        sq_profit = []
        
        for i in range(len(df)):
            curr = df.iloc[i]
            year = curr['year']
            month = curr['month']
            
            curr_rev = curr['revenue']
            curr_prof = curr['net_profit_parent']
            
            if month == 3:
                sq_revenue.append(curr_rev)
                sq_profit.append(curr_prof)
            else:
                # Find previous record for same year
                # This assumes data is complete. If Q1 is missing, Q2 SQ will be wrong (it will be cumulative).
                # So we search for the specific previous quarter record.
                prev_month_candidates = [3, 6, 9]
                target_prev_month = 0
                if month == 6: target_prev_month = 3
                elif month == 9: target_prev_month = 6
                elif month == 12: target_prev_month = 9
                
                prev_record = df[(df['year'] == year) & (df['month'] == target_prev_month)]
                
                if not prev_record.empty:
                    prev_rev = prev_record.iloc[0]['revenue']
                    prev_prof = prev_record.iloc[0]['net_profit_parent']
                    
                    # Ensure values are float
                    curr_rev = float(curr_rev) if pd.notnull(curr_rev) else 0.0
                    prev_rev = float(prev_rev) if pd.notnull(prev_rev) else 0.0
                    curr_prof = float(curr_prof) if pd.notnull(curr_prof) else 0.0
                    prev_prof = float(prev_prof) if pd.notnull(prev_prof) else 0.0

                    sq_revenue.append(curr_rev - prev_rev)
                    sq_profit.append(curr_prof - prev_prof)
                else:
                    # If missing previous quarter, we can't accurately calculate SQ.
                    sq_revenue.append(None)
                    sq_profit.append(None)
        
        df['revenue_sq'] = sq_revenue
        df['net_profit_parent_sq'] = sq_profit
        
        return df

    def calculate_growth_metrics(self, df):
        """Calculate YoY growth for single quarter metrics."""
        if df is None or df.empty:
            return None
            
        # Self-join to find previous year's same quarter
        # Create a temp column for join key: year-1, month
        
        df['prev_year_key'] = (df['year'] - 1).astype(str) + '-' + df['month'].astype(str)
        df['current_key'] = df['year'].astype(str) + '-' + df['month'].astype(str)
        
        # Create a lookup dict
        lookup_rev = dict(zip(df['current_key'], df['revenue_sq']))
        lookup_prof = dict(zip(df['current_key'], df['net_profit_parent_sq']))
        
        yoy_rev = []
        yoy_prof = []
        
        for i in range(len(df)):
            key = df.iloc[i]['prev_year_key']
            curr_rev = df.iloc[i]['revenue_sq']
            curr_prof = df.iloc[i]['net_profit_parent_sq']
            
            prev_rev = lookup_rev.get(key)
            prev_prof = lookup_prof.get(key)
            
            if prev_rev is not None and prev_rev != 0 and pd.notnull(curr_rev):
                yoy_rev.append((curr_rev - prev_rev) / abs(prev_rev) * 100)
            else:
                yoy_rev.append(None)
                
            if prev_prof is not None and prev_prof != 0 and pd.notnull(curr_prof):
                yoy_prof.append((curr_prof - prev_prof) / abs(prev_prof) * 100)
            else:
                yoy_prof.append(None)
                
        df['revenue_sq_yoy'] = yoy_rev
        df['net_profit_sq_yoy'] = yoy_prof
        
        return df

    def get_analyst_metrics(self, stock_code):
        """Get analyst metrics."""
        # Default empty result
        empty_res = {
            '研报数': 0,
            '买入评级': 0,
            '增持评级': 0,
            '调高数': 0,
            '调低数': 0,
            '维持数': 0,
            '超预期数': 0,
            '分析师分': 0.0
        }
        
        try:
            # File path: stock_code.csv in analyst_data_dir
            file_path = os.path.join(self.analyst_data_dir, f"{stock_code}.csv")
            
            if not os.path.exists(file_path):
                # Try glob if exact match fails, though usually it's exact
                pattern = os.path.join(self.analyst_data_dir, f"*{stock_code}*.csv")
                files = glob.glob(pattern)
                if files:
                    file_path = files[0]
                else:
                    return empty_res
            
            # Read CSV
            # Columns of interest: 发布日期, 报告名称, 最新投资评级, 调整方向
            df = pd.read_csv(file_path, encoding='gbk', skiprows=1, 
                             usecols=['发布日期', '报告名称', '最新投资评级', '调整方向'],
                             on_bad_lines='skip')
            
            if df.empty:
                return empty_res
                
            df['发布日期'] = pd.to_datetime(df['发布日期'], errors='coerce')
            
            # Filter for last 180 days (approx 6 months)
            # Use max date in file as anchor or current time?
            # Assuming analysis on latest data, using today is safer to avoid old data being treated as new
            # But if today is 2026 and file is 2025, it works.
            # If system time is correct (2026-02-03), this is fine.
            cutoff_date = datetime.now() - timedelta(days=180)
            
            # If the file is old, we might get 0. That's correct.
            recent_df = df[df['发布日期'] >= cutoff_date].copy()
            
            if recent_df.empty:
                # Fallback: if no recent data, maybe check last 30 reports regardless of time?
                # No, stick to time window for relevance.
                return empty_res
            
            # 1. Rating Counts
            rating_keywords = {
                '买入': ['买入', 'Buy', '积极买入', '强烈买入', '长线买入', '优于大市', '跑赢行业'],
                '增持': ['增持', '推荐', '强烈推荐', '谨慎增持', '审慎增持', '跑赢大市']
            }
            
            recent_df['最新投资评级'] = recent_df['最新投资评级'].fillna('')
            buy_count = 0
            overweight_count = 0
            
            for kw in rating_keywords['买入']:
                buy_count += recent_df['最新投资评级'].str.contains(kw, case=False, na=False).sum()
            
            for kw in rating_keywords['增持']:
                overweight_count += recent_df['最新投资评级'].str.contains(kw, case=False, na=False).sum()
                
            # 2. Adjustment Counts
            adj_keywords = {
                '调高': ['调高', '上调', '提升'],
                '调低': ['调低', '下调', '降低'],
                '维持': ['维持', '保持', '不变']
            }
            
            recent_df['调整方向'] = recent_df['调整方向'].fillna('')
            up_count = 0
            down_count = 0
            maintain_count = 0
            
            for kw in adj_keywords['调高']:
                up_count += recent_df['调整方向'].str.contains(kw, case=False, na=False).sum()
            for kw in adj_keywords['调低']:
                down_count += recent_df['调整方向'].str.contains(kw, case=False, na=False).sum()
            for kw in adj_keywords['维持']:
                maintain_count += recent_df['调整方向'].str.contains(kw, case=False, na=False).sum()
                
            # 3. Super Expectation
            surprise_keywords = [
                "超预期", "大超", "远超", "创新高", "新纪录", "高增", "高增长", 
                "快速", "提速", "加速", "优异", "亮眼", "强劲", "大幅", "显著提升", "显著超出", "放量", "爆发"
            ]
            recent_df['报告名称'] = recent_df['报告名称'].fillna('')
            surprise_pattern = '|'.join(surprise_keywords)
            surprise_count = recent_df['报告名称'].str.contains(surprise_pattern, case=False, regex=True, na=False).sum()
            
            # 4. Score Calculation
            # Weights: 
            # Report Count: 0.1
            # Buy: 1.0
            # Overweight: 0.5
            # Up: 2.0
            # Surprise: 2.0
            # Down: -1.0
            score = (len(recent_df) * 0.1) + (buy_count * 1.0) + (overweight_count * 0.5) + \
                    (up_count * 2.0) + (surprise_count * 2.0) - (down_count * 1.0)
            
            return {
                '研报数': len(recent_df),
                '买入评级': int(buy_count),
                '增持评级': int(overweight_count),
                '调高数': int(up_count),
                '调低数': int(down_count),
                '维持数': int(maintain_count),
                '超预期数': int(surprise_count),
                '分析师分': round(score, 1)
            }
            
        except Exception as e:
            logging.error(f"Error getting analyst metrics for {stock_code}: {e}")
            return empty_res

    def get_latest_metrics(self, stock_code):
        """Get the latest available financial metrics for a stock."""
        df = self.read_financial_data(stock_code)
        if df is None or df.empty:
            return {}
            
        df = self.calculate_single_quarter_metrics(df)
        df = self.calculate_growth_metrics(df)
        
        # Get latest row with valid data (some reports might be previews/incomplete?)
        # Usually we just take the last row
        latest = df.iloc[-1]
        
        return {
            'report_date': latest['report_date'],
            'revenue_sq': latest['revenue_sq'],
            'net_profit_sq': latest['net_profit_parent_sq'],
            'revenue_sq_yoy': latest['revenue_sq_yoy'],
            'net_profit_sq_yoy': latest['net_profit_sq_yoy']
        }

if __name__ == "__main__":
    # Test
    analyzer = FinancialAnalyzer()
    # Replace with a valid stock code for testing
    print(analyzer.get_latest_metrics('sz000063'))
