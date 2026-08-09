"""
信息简报 - 仅分类 (无贝叶斯, 无打分, 无联合)

只做: 4 路读取 + 标签/分类整理, 给你看分类结果。
不打分, 不预测, 不联合。

用法: python agent/info_brief_classify.py
"""
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from db.storage import get_cls_news, get_policy_news, get_research_reports


# ── 标签分类 (基于 title 关键词, 扩展版) ──────────────────
TAGS = {
    # ── 科技 ──
    "半导体": ["半导体", "芯片", "存储", "光刻", "中芯", "晶圆", "DRAM", "NAND", "GPU", "封测", "设备", "材料", "光模块", "光通信", "PCB", "模拟", "功率", "IC", "EDA", "先进封装", "HBM"],
    "AI": ["AI", "人工智能", "大模型", "千问", "DeepSeek", "OpenAI", "VLA", "世界模型", "智能体", "AGI", "AIGC", "生成式", "具身智能", "算力", "数据中心", "云计算", "服务器", "IDC", "Token"],
    "人形机器人": ["人形机器人", "机器人", "减速器", "丝杠", "灵巧手"],
    "通信/5G/6G": ["5G", "6G", "通信", "运营商", "移动", "联通", "电信", "基站", "光缆", "卫星", "北斗", "星链"],
    "软件/互联网": ["软件", "SaaS", "互联网", "电商", "腾讯", "阿里", "百度", "京东", "美团", "拼多多", "字节", "抖音", "微信", "鸿蒙", "操作系统"],

    # ── 新能源 ──
    "新能源车": ["新能源车", "电动汽车", "锂电池", "电池", "宁德", "比亚迪", "锂电", "充电桩", "整车", "理想", "蔚来", "小鹏", "特斯拉", "锂矿", "正极", "负极", "隔膜", "电解液"],
    "光伏": ["光伏", "硅料", "硅片", "隆基", "通威", "阳光电源", "逆变器", "组件", "电池片", "多晶硅", "钙钛矿", "HJT", "TOPCon"],
    "风电/储能": ["风电", "风机", "海上风电", "储能", "电芯", "宁王"],
    "新能源": ["氢能", "燃料电池", "核电", "光伏", "风电", "新能源"],

    # ── 资源/材料 ──
    "有色金属": ["有色", "铜", "铝", "锌", "镍", "钴", "锂", "稀土", "钨", "钼", "黄金", "白银", "白银"],
    "钢铁": ["钢铁", "螺纹钢", "铁矿石", "钢厂", "板材", "特钢"],
    "煤炭": ["煤炭", "焦煤", "焦炭", "动力煤", "煤价", "煤化工"],
    "化工": ["化工", "石化", "化纤", "聚酯", "PTA", "MDI", "钛白粉", "纯碱", "尿素"],
    "原油/天然气": ["原油", "油价", "石油", "天然气", "OPEC", "沙特", "页岩油"],

    # ── 制造业 ──
    "军工/国防": ["军工", "国防", "军费", "航空", "导弹", "雷达", "船舶", "军工股", "国防部", "北约", "中航"],
    "工程机械": ["工程机械", "挖掘机", "三一", "徐工", "中联", "重卡", "卡车"],
    "汽车": ["汽车", "整车", "汽零", "汽车零部件", "汽车以旧换新", "经销商", "上汽", "广汽", "长城", "吉利", "奇瑞", "比亚迪"],
    "造船/航运/港口": ["造船", "船舶", "航运", "海运", "港口", "集装箱", "出口", "外贸", "运价", "BDI", "中远海运"],

    # ── 消费 ──
    "消费": ["消费", "白酒", "茅台", "五粮液", "家电", "以旧换新", "消费券", "消费股", "消费板块"],
    "食品/饮料": ["食品", "饮料", "调味品", "海天", "伊利", "蒙牛", "双汇", "三全", "安井"],
    "服装/化妆品/医美": ["服装", "纺服", "美妆", "化妆品", "医美", "玻尿酸", "爱美客", "珀莱雅"],
    "零售/电商": ["零售", "超市", "百货", "电商", "直播", "跨境电商"],
    "旅游/酒店/航空": ["旅游", "酒店", "景区", "航空", "机场", "免税", "出境游", "OTA", "携程"],

    # ── 医药 ──
    "医药": ["医药", "创新药", "医疗器械", "生物", "恒瑞", "药明", "百济", "信达", "CXO", "CRO", "CDMO", "中药", "中医药", "集采", "医保"],
    "疫苗/生物": ["疫苗", "mRNA", "PD-1", "单抗", "双抗", "ADC", "细胞治疗", "基因"],

    # ── 金融 ──
    "银行": ["银行", "央行", "降准", "LPR", "招行", "工行", "建行", "农行", "中行", "理财", "净息差", "不良率", "信贷", "存款准备金", "公开市场操作", "逆回购", "MLF"],
    "保险": ["保险", "平安", "人寿", "太保", "新华", "保费"],
    "证券/券商": ["券商", "证券", "中信证券", "华泰", "国君", "海通", "广发", "招商证券", "东财", "同花顺", "融资融券", "两融", "北向", "南向"],
    "地产": ["地产", "房地产", "楼市", "房价", "万科", "保利", "碧桂园", "融创", "契税", "首付", "限购", "房贷", "保交楼", "城投"],
    "金融科技": ["金融科技", "FinTech", "支付", "跨境支付", "数字货币", "稳定币", "区块链", "数字人民币"],

    # ── 农业/食品/其他 ──
    "农业/养殖": ["农业", "粮食", "种业", "猪", "生猪", "猪肉", "猪价", "牧原", "温氏", "新希望", "水产", "鸡", "鸭", "饲料"],
    "传媒/影视/游戏": ["传媒", "影视", "电影", "票房", "电视剧", "游戏", "米哈游", "腾讯游戏", "网易游戏", "完美世界", "三七互娱"],
    "教育": ["教育", "培训", "新东方", "好未来", "中公教育", "教培", "K12"],
    "环保/碳中和": ["环保", "碳中和", "碳达峰", "ESG", "减排", "新能源车补贴", "光伏补贴"],
    "知识产权/数据要素": ["知识产权", "数据要素", "数据局", "数据交易", "信创", "国产化"],

    # ── 通用事件 ──
    "减持": ["减持", "股东减持", "拟减持", "清仓减持", "减持计划"],
    "回购": ["回购", "股票回购", "增持回购"],
    "增持": ["增持", "股东增持", "自购"],
    "重组": ["重组", "吸并", "吸收合并", "并购", "要约收购", "借壳"],
    "ST/退市": ["ST", "*ST", "退市", "摘牌", "退市风险", "面值退市"],
    "立案/监管": ["立案", "监管函", "警示函", "处罚", "调查", "稽查", "证监会", "交易所", "问询", "关注函", "监管处罚"],
    "业绩": ["业绩预增", "业绩预减", "扭亏", "首亏", "续亏", "业绩快报", "年报", "一季报", "半年报", "三季报"],
    "中标/合同": ["中标", "重大合同", "签署协议", "战略合作", "签约", "采购合同"],
    "海外/外资": ["美股", "美光", "英伟达", "苹果", "台积电", "三星", "原油", "美元", "日元", "欧元", "欧洲", "港股", "恒生", "韩国", "日本", "印度", "越南", "巴西", "中东", "俄罗斯", "乌克兰", "巴菲特", "伯克希尔", "高盛", "摩根"],
    "宏观/政策": ["宏观", "GDP", "CPI", "PPI", "PMI", "社融", "M2", "美联储", "鲍威尔", "沃什", "降息", "加息", "缩表", "扩表", "美债", "美债收益率", "关税", "制裁", "出口管制"],
    "IPO/上市": ["IPO", "上市", "招股", "首发", "注册制", "北交所", "科创板", "创业板"],
    "外资/北向": ["北向", "北向资金", "陆股通", "沪深港通", "外资", "QFII", "RQFII"],
}


def tag(title: str) -> str:
    """返回 title 命中的第一个标签, 没有则 '其他'。"""
    for tag_name, kws in TAGS.items():
        if any(kw in title for kw in kws):
            return tag_name
    return "其他"


# ── 公告分类 (关键类别 vs 次要类别) ─────────────────
# 关键类别: 用户关注 (业绩/回购/合同/监管/技术/增发/减持/调研)
# 次要类别: 质押/高管变动/股东大会 等 (保留分类但降级展示)
NOTICE_CATS = {
    # 关键 (按重要性排序)
    "业绩": ["业绩预增", "业绩预减", "扭亏", "首亏", "续亏", "业绩快报", "半年度业绩", "年度报告", "一季报", "三季报", "业绩更正", "业绩预披露", "业绩公告"],
    "回购": ["回购", "股票回购", "股份回购", "回购报告书", "回购预案", "竞价回购"],
    "合同/中标": ["中标", "重大合同", "签署协议", "战略合作", "签约", "采购合同", "框架协议", "销售合同", "施工合同", "工程合同", "经营合同"],
    "监管处罚": ["立案", "监管函", "警示函", "处罚", "调查", "稽查", "问询", "关注函", "监管措施", "监管工作", "现场检查"],
    "技术/产品": ["新产品", "新技术", "技术认证", "技术突破", "投产", "获批", "许可", "资质", "专利", "获授", "临床试验", "获准上市", "通过认证"],
    "增发/再融资": ["增发", "非公开发行", "定增", "配股", "可转债", "可交换债", "发行股份", "募集说明书", "募投项目"],
    "减持": ["减持", "股东减持", "拟减持", "清仓减持", "减持计划", "减持完成", "减持结果"],
    "调研": ["机构调研", "投资者调研", "接待调研", "调研活动", "特定对象调研", "分析师调研", "现场参观", "线上交流", "业绩说明会"],
    "重组并购": ["重组", "吸并", "吸收合并", "并购", "要约收购", "控制权变更", "借壳", "重大资产重组"],

    # 次要 (用户不重点关注, 但保留分类)
    "质押": ["质押", "解除质押", "股份质押", "延期购回", "股权质押"],
    "ST/退市": ["ST", "*ST", "退市", "摘牌", "退市风险", "面值退市"],
    "高管变动": ["董事长", "总经理", "高管", "董秘", "独立董事", "审计委员会", "监事会主席", "高管辞职", "高管任命"],
    "股东大会": ["股东大会决议", "股东大会通知", "召开股东大会", "临时股东大会", "年度股东大会"],
    "分红/送转": ["分红", "派息", "送股", "转增", "权益分派"],
    "股份冻结": ["股份冻结", "股份解冻", "股东股份被冻结", "股份被司法冻结"],
    "海外/产业链": ["海外大单", "产业链", "扩产", "海外项目", "海外收购", "出海", "海外经营合同"],
    "其他实质": [],
}

# 关键类别 (排序靠前, 时效性加权)
KEY_CATS = ["业绩", "回购", "合同/中标", "监管处罚", "技术/产品", "增发/再融资", "减持", "调研", "重组并购", "股份冻结"]
# 次要类别 (排序靠后)
MINOR_CATS = ["质押", "ST/退市", "高管变动", "股东大会", "分红/送转", "海外/产业链", "其他实质"]

# 流程性: 标准化公告, 没实质内容
NOISE_KW = [
    "董事会决议", "独立董事专门会议", "股东大会决议", "监事会决议",
    "会议通知", "章程修订", "工商变更", "提示性公告", "进展公告",
    "停牌核查", "股票交易异常波动", "关于召开", "关于举行",
    # 第N届董事会第N次会议决议 (例: "第五届董事会第十次会议决议公告")
    "届董事会", "届监事会", "届股东大会", "届董事会第", "届监事会第",
]


def classify_notice(title: str) -> tuple[str, bool]:
    """返回 (分类, 是否实质)。流程性返回 (其他, False)。"""
    if any(kw in title for kw in NOISE_KW):
        return "流程性", False
    for cat, kws in NOTICE_CATS.items():
        if any(kw in title for kw in kws):
            return cat, True
    return "其他实质", True  # 算实质, 但具体类型未明


# ── 政策分类 (覆盖发改委/证监会/沪深/财新/媒体观点) ──
POLICY_CATS = {
    # 部委一手政策
    "产业规划/意见": ["规划", "意见", "指导", "方案", "行动计划", "实施方案", "指导意见"],
    "资本市场制度": ["期权", "期货", "上市规则", "交易规则", "减持", "分红", "回购", "IPO", "再融资", "退市", "注册制"],
    "价格/能源": ["价格", "成品油", "油价", "电价", "气价", "煤价", "新能源补贴", "可再生能源"],
    "监管处罚": ["监管", "立案", "处罚", "调查", "稽查"],
    "工作会议": ["会议", "座谈", "调研", "讲话", "致辞", "会见", "举行", "通报", "新闻发布", "发布会"],
    "信用/民营": ["信用", "民营"],
    "金融/财政": ["金融", "财政", "央行", "银保监", "保险", "信托", "理财", "票据", "LPR", "降准", "公开市场操作", "健全金融机构治理"],
    "国资改革": ["国资", "国企改革", "央企", "国企整合"],
    "环保/碳中和": ["环保", "碳中和", "碳达峰", "减排", "生态日", "海水淡化", "可再生能源", "新型电力", "输配电", "中欧班列"],
    "能源/电力": ["成品油", "电价", "输配电价", "电网", "电力市场", "充电", "换电", "氢能", "燃料电池", "核电", "电力系统", "电力现货"],
    "AI/科技产业": ["人工智能", "AI", "机器人", "世界人工智能大会", "算力", "大模型", "智能网联", "集成电路", "半导体"],
    "培训/学习/课题": ["培训", "研讨班", "惩防", "课题研究", "课题入选", "财务造假", "省部级课题"],
    "政务公开/财务": ["部门决算", "决算", "预算公开", "政务公开", "年报", "年报披露"],
    "跨境/开放": ["人民币国债期货", "港交所", "港股通", "跨境", "对外开放", "外资准入", "中欧", "外资"],
    "执法/合作": ["两地执法", "执法合作", "联合执法", "跨境监管", "国际监管"],
    "稽查/立案": ["稽查", "编造传播", "虚假信息", "行政处罚", "立案调查", "证监会同意", "期权注册", "期货注册"],

    # 财新/媒体观点
    "财新时政/法律": ["善终", "抢救", "一审", "二审", "获刑", "落马", "双开", "立案审查", "留置", "判决", "司法", "检察", "法院", "死刑", "无期", "判刑"],
    "财新反腐": ["反腐", "落马", "双开", "留置", "审查调查", "严重违纪", "开除党籍"],
    "财新国际/外交": ["外交", "制裁", "签证", "大使", "联合国", "WTO", "中美", "中俄", "中欧", "中印", "美俄", "美伊", "美巴", "北约", "G20", "G7"],
    "财新港股/中概": ["港股", "港股通", "恒生", "中概", "二次上市", "双重上市", "宇树", "MiniMax", "纳入港股通"],
    "财新公司/商业": ["上汽", "比亚迪", "宁德", "茅台", "雪佛兰", "退出中国", "上汽通用", "合资续约", "车企", "新势力", "财报", "业绩"],
    "财新金融/税收": ["征税", "税收", "税务", "个税", "增值税", "保险收益", "境外收入", "缴交"],
    "财新科技/AI": ["世界模型", "游戏", "AI", "3D建模", "具身", "多模态", "算力", "大模型"],
    "财新能源/环境": ["SAF", "可持续航空", "新能源", "光伏", "风电", "气候", "碳", "减排", "石油", "煤炭", "天然气"],
    "财新社会/民生": ["教育", "高考", "艺考", "中考", "医疗", "养老", "三胎", "生育", "消费", "物价"],
    "财新市场评论": ["火线评论", "评论", "观察", "深度", "聚焦", "解读", "T早报", "财新闻", "周报"],
}


def classify_policy(title: str) -> str:
    for cat, kws in POLICY_CATS.items():
        if any(kw in title for kw in kws):
            return cat
    return "其他"


# ── 研报分类 ─────────────────────────────────────
def classify_research(qtype: int, title: str) -> str:
    if qtype == 0:
        return "个股"
    if qtype == 1:
        return "行业"
    if qtype == 2:
        return "宏观"
    if qtype == 3:
        return "策略"
    return "其他"


# ── 加载 ─────────────────────────────────────
def load_flash(hours: int = 24):
    rows = get_cls_news(limit=2000)
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    items = []
    for r in rows:
        pt = r.get("pub_time", "")
        if pt and pt >= cutoff:
            try:
                pt_dt = datetime.strptime(pt, "%Y-%m-%d %H:%M:%S")
                days_old = (now.date() - pt_dt.date()).days
            except Exception:
                days_old = 99
            items.append({"title": r.get("title", ""), "source": r.get("source", ""), "pub_time": pt, "tag": tag(r.get("title", "")), "days_old": days_old})
    return items


def load_policy(days: int = 3):
    rows = get_policy_news(limit=5000)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    items = []
    for r in rows:
        if r.get("source") == "巨潮公告":
            continue
        pt = r.get("pub_time", "")
        if pt and pt >= cutoff:
            title = r.get("title", "")
            try:
                pt_dt = datetime.strptime(pt, "%Y-%m-%d %H:%M:%S")
                days_old = (now.date() - pt_dt.date()).days
            except Exception:
                days_old = 99
            items.append({"title": title, "source": r.get("source", ""), "pub_time": pt, "cat": classify_policy(title), "days_old": days_old})
    return items


def load_notice(days: int = 3):
    rows = get_policy_news(limit=5000)
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    now = datetime.now()
    items = []
    for r in rows:
        if r.get("source") != "巨潮公告":
            continue
        pt = r.get("pub_time", "")
        if not pt or pt < cutoff:
            continue
        title = r.get("title", "")
        cat, is_real = classify_notice(title)
        # 计算 days_old (0=今天, 1=昨天, 2=前天)
        try:
            pt_dt = datetime.strptime(pt, "%Y-%m-%d %H:%M:%S")
            days_old = (now.date() - pt_dt.date()).days
        except Exception:
            days_old = 99
        items.append({"title": title, "source": "巨潮公告", "pub_time": pt, "cat": cat, "is_real": is_real, "days_old": days_old})
    return items


def load_research(days: int = 3):
    rows = get_research_reports(limit=500)
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    now = datetime.now()
    items = []
    for r in rows:
        pd = r.get("publish_date", "")
        if not pd or pd < cutoff_date:
            continue
        title = r.get("title", "")
        qtype = r.get("qtype", 0)
        try:
            pd_dt = datetime.strptime(pd, "%Y-%m-%d")
            days_old = (now.date() - pd_dt.date()).days
        except Exception:
            days_old = 99
        items.append({"title": title, "source": r.get("org_name", ""), "pub_time": pd, "cat": classify_research(qtype, title), "tag": tag(title), "days_old": days_old})
    return items


# ── 整理 (按 cat/tag 聚合) ─────────────────────
def group_by(items, key, sort_by_recency=False):
    """按 key 聚合, 默认按数量降序, sort_by_recency=True 时类内按 days_old 升序"""
    groups = defaultdict(list)
    for it in items:
        groups[it[key]].append(it)
    result = {}
    for cat, group in groups.items():
        if sort_by_recency:
            result[cat] = sorted(group, key=lambda x: (x.get("days_old", 99), x.get("pub_time", "")))
        else:
            result[cat] = group
    return dict(sorted(result.items(), key=lambda x: -len(x[1])))  # 整体按数量降序


def group_notice_by_priority(items):
    """
    公告特殊处理: 关键类靠前 + 类内按 days_old 升序 (新→旧)
    """
    key_groups = {}
    minor_groups = {}
    for cat, group in group_by(items, "cat", sort_by_recency=True).items():
        if cat in KEY_CATS:
            key_groups[cat] = group
        else:
            minor_groups[cat] = group
    # 关键类按 KEY_CATS 顺序排
    key_ordered = {c: key_groups[c] for c in KEY_CATS if c in key_groups}
    minor_ordered = {c: minor_groups[c] for c in MINOR_CATS if c in minor_groups}
    other_ordered = {c: v for c, v in key_groups.items() if c not in KEY_CATS}
    return {**key_ordered, **other_ordered, **minor_ordered}


# ── 打印 ─────────────────────────────────────
def print_group(title, items, key, top_n=10, priority=False, sort_by_recency=False):
    print(f"\n{'='*60}")
    print(f"  {title}  ·  总 {len(items)} 条")
    print('='*60)
    if priority:
        groups = group_notice_by_priority(items)
    else:
        groups = group_by(items, key, sort_by_recency=sort_by_recency)
    for cat, group in groups.items():
        marker = "🔑" if (priority and cat in KEY_CATS) else "  "
        print(f"\n  {marker}[{cat}]  {len(group)} 条")
        for it in group[:top_n]:
            t = it["title"][:80]
            pt = it.get("pub_time", "")[:16]
            src = it.get("source", "")
            days_old = it.get("days_old", 99)
            if days_old == 0:
                age_label = "今天"
            elif days_old == 1:
                age_label = "昨天"
            elif days_old == 2:
                age_label = "前天"
            else:
                age_label = f"{days_old}天前"
            print(f"    [{age_label}] {pt} · {src[:10]}")
            print(f"    {t}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3, help="政策/公告/研报时间窗 (日)")
    parser.add_argument("--hours", type=int, default=24, help="快讯时间窗 (小时)")
    args = parser.parse_args()

    print(f"\n📊 信息简报 - 仅分类 (无贝叶斯, 无打分, 无联合)")
    print(f"   时间窗: 快讯 {args.hours}h / 政策/公告/研报 {args.days}日\n")

    flash = load_flash(args.hours)
    policy = load_policy(args.days)
    notice = load_notice(args.days)
    research = load_research(args.days)

    # 公告: 流程性/实质 分开
    real_notice = [it for it in notice if it["is_real"]]
    noise_notice = [it for it in notice if not it["is_real"]]
    print(f"\n公告: 总 {len(notice)} 条, 实质 {len(real_notice)} 条, 流程性 {len(noise_notice)} 条 (砍)")

    print_group("📡 财经快讯 (近 24h, 全部, 不卡阈值)  · 类内按新→旧", flash, "tag", top_n=5, sort_by_recency=True)
    print_group("🏛️ 政策动态 (近 3 日, 全部)  · 类内按新→旧", policy, "cat", top_n=5, sort_by_recency=True)
    print_group("📋 公告要点 (近 3 日, 实质, 流程性已砍)  · 关键类靠前 + 时效性", real_notice, "cat", top_n=5, priority=True)
    print_group("📑 研报观点 (近 3 日)  · 类内按新→旧", research, "cat", top_n=5, sort_by_recency=True)

    # 联合: 只统计, 不解释
    print(f"\n\n{'='*60}")
    print("  🔗 跨路同主题计数 (仅看数字, 不分析)")
    print('='*60)
    flash_tags = set(it["tag"] for it in flash)
    research_tags = set(it["tag"] for it in research)
    overlap = flash_tags & research_tags
    print(f"\n  财经快讯标签: {len(flash_tags)} 种")
    print(f"  研报标签:     {len(research_tags)} 种")
    print(f"  重叠:         {len(overlap)} 种")
    for t in sorted(overlap):
        f_count = sum(1 for it in flash if it["tag"] == t)
        r_count = sum(1 for it in research if it["tag"] == t)
        print(f"    [{t}] 快讯 {f_count} 条, 研报 {r_count} 条")


if __name__ == "__main__":
    main()
