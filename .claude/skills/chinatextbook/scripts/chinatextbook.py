#!/usr/bin/env python3
"""ChinaTextbook 全库检索与下载工具。

覆盖 TapXWorld/ChinaTextbook 的全部目录：
小学 / 初中 / 高中 / 小学（五•四学制）/ 初中（五•四学制）/ 大学 / 中考真题。

标准库实现，无第三方依赖。

索引来源优先级：
1. 本地缓存（24 小时内有效）
2. GitHub Trees API
3. 技能自带的 assets/index.json.gz 快照（离线或 API 不可达时）
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO = "TapXWorld/ChinaTextbook"
BRANCH = "master"
TREE_API = f"https://api.github.com/repos/{REPO}/git/trees/{BRANCH}?recursive=1"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"

SKILL_DIR = Path(os.environ.get("CLAUDE_SKILL_DIR", Path(__file__).resolve().parent.parent))
BUNDLED_INDEX = SKILL_DIR / "assets" / "index.json.gz"
TAXONOMY_PATH = SKILL_DIR / "assets" / "taxonomy.json"

CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "chinatextbook"
CACHE_PATH = CACHE_DIR / "tree.json"
CACHE_TTL = 24 * 60 * 60

DEFAULT_OUT = Path.home() / "Downloads" / "ChinaTextbook"
SUPPORTED_FILE_RE = re.compile(r"\.(?:pdf(?:\.\d+)?|djvu)$", re.IGNORECASE)
PART_RE = re.compile(r"\.pdf\.(\d+)$", re.IGNORECASE)
MERGE_FOLDER_RE = re.compile(r"(.+\.pdf)_merge_folder/.+\.pdf\.\d+$", re.IGNORECASE)

EXERCISE_ROOT = "学数学最重要的刷习题在这里"

# --------------------------------------------------------------------------
# 词表
# --------------------------------------------------------------------------

STAGES = ["小学", "初中", "高中", "小学（五•四学制）", "初中（五•四学制）", "大学", EXERCISE_ROOT]

# 五四学制的多种写法
WU_SI_MARKERS = ("五四学制", "五·四学制", "五•四学制", "五4学制", "54学制", "五四制", "五四")

STAGE_ALIASES = {
    "小学": ["小学", "小", "初小", "小学部"],
    "初中": ["初中", "初级中学", "中学", "初"],
    "高中": ["高中", "高级中学", "普高", "高"],
    "大学": ["大学", "本科", "高校", "大一", "大二", "大三", "大四", "专科"],
}

# 年级 -> 学段推断（六•三学制，全国多数地区）
GRADE_TO_STAGE = {
    "一年级": "小学", "二年级": "小学", "三年级": "小学",
    "四年级": "小学", "五年级": "小学", "六年级": "小学",
    "七年级": "初中", "八年级": "初中", "九年级": "初中",
}

# 五•四学制：小学 5 年（一~五年级），初中 4 年（六~九年级）。
# 六年级在五•四学制里属于初中，和六•三学制相反，必须单独映射。
WU_SI_GRADE_TO_STAGE = {
    "一年级": "小学（五•四学制）", "二年级": "小学（五•四学制）",
    "三年级": "小学（五•四学制）", "四年级": "小学（五•四学制）",
    "五年级": "小学（五•四学制）",
    "六年级": "初中（五•四学制）", "七年级": "初中（五•四学制）",
    "八年级": "初中（五•四学制）", "九年级": "初中（五•四学制）",
}

# 口语年级写法 -> 规范年级
GRADE_ALIASES = {
    "初一": "七年级", "初二": "八年级", "初三": "九年级",
    "一年": "一年级", "二年": "二年级", "三年": "三年级",
    "四年": "四年级", "五年": "五年级", "六年": "六年级",
    "七年": "七年级", "八年": "八年级", "九年": "九年级",
    "1年级": "一年级", "2年级": "二年级", "3年级": "三年级",
    "4年级": "四年级", "5年级": "五年级", "6年级": "六年级",
    "7年级": "七年级", "8年级": "八年级", "9年级": "九年级",
}

# 紧凑写法：九上 -> 九年级 上册
COMPACT_GRADE_RE = re.compile(r"(?<![年第])([一二三四五六七八九1-9])([上下])(?![册学期年])")
CN_DIGIT = {"1": "一", "2": "二", "3": "三", "4": "四", "5": "五",
            "6": "六", "7": "七", "8": "八", "9": "九"}

# 高中年级 -> 常见册别提示（仅作为弱提示，不作硬过滤）
SENIOR_GRADE = {"高一": "必修", "高二": "选择性必修", "高三": "选择性必修"}

# 学科同义词。键为仓库中的真实目录名。
SUBJECT_ALIASES = {
    "语文": ["语文", "国文", "中文"],
    "数学": ["数学", "数", "算数", "算术"],
    "英语": ["英语", "英文", "外语"],
    "物理": ["物理"],
    "化学": ["化学"],
    "生物学": ["生物学", "生物"],
    "历史": ["历史", "史"],
    "地理": ["地理"],
    "地理图册": ["地理图册", "地图册", "填充图册", "地理地图册", "图册"],
    "人文地理": ["人文地理"],
    "道德与法治": ["道德与法治", "政治", "品德", "思想品德", "道法"],
    "思想政治": ["思想政治", "政治", "思政"],
    "科学": ["科学"],
    "音乐": ["音乐", "乐理", "唱歌"],
    "美术": ["美术", "画画", "绘画"],
    "艺术": ["艺术"],
    "体育与健康": ["体育与健康", "体育", "体健"],
    "信息技术": ["信息技术", "信息", "计算机", "编程"],
    "通用技术": ["通用技术"],
    "俄语": ["俄语", "俄文"],
    "日语": ["日语", "日文"],
    "语文·书法练习指导": ["书法", "书法练习", "书法练习指导", "写字"],
    "高等数学": ["高等数学", "高数", "微积分"],
    "线性代数": ["线性代数", "线代"],
    "离散数学": ["离散数学", "离散"],
    "概率论": ["概率论", "概率", "概率统计", "概率论与数理统计", "数理统计"],
}

UNIVERSITY_SUBJECTS = {"高等数学", "线性代数", "离散数学", "概率论"}

# 册别 / 模块
VOLUME_ALIASES = {
    "上册": ["上册", "上"],
    "下册": ["下册", "下"],
    "全一册": ["全一册", "全册"],
}
MODULE_RE = re.compile(r"(选择性必修|必修|选修)\s*([一二三四五1-5])?")

# 材料类型
MATERIAL_WORDS = {
    "教师用书": ["教师用书", "教参", "教师参考", "教师"],
    "习题": ["习题", "练习", "课后题"],
    "答案": ["答案", "全解", "解析", "题解"],
    "指南": ["指南", "辅导", "学习辅导"],
}
# 默认降权的非主教材词（用户没主动要时）
NON_MAIN_WORDS = ("教师用书", "教参", "习题全解", "全解", "答案", "解析",
                  "学习辅导", "辅导", "指南", "附册", "活动手册")

# 出版社/版本口语别名 -> 仓库目录里的短名
PUBLISHER_ALIASES = {
    "人教": "人教版", "人民教育": "人教版", "PEP": "人教版", "pep": "人教版",
    "统编": "统编版", "部编": "统编版", "部编版": "统编版",
    "苏教": "苏教版", "江苏教育": "苏教版",
    "译林": "译林版",
    "北师大": "北师大版", "北京师范": "北师大版",
    "沪教": "沪教版", "上海教育": "沪教版",
    "浙教": "浙教版", "粤教": "粤教版",
    "外研": "外研社版", "外研社": "外研社版", "外语教学与研究": "外研社版",
    "鲁教": "鲁教版", "山东教育": "鲁教版",
    "鲁科": "鲁科版", "青岛": "青岛版",
    "冀教": "冀教版", "河北教育": "冀教版",
    "湘教": "湘教版", "湖南教育": "湘教版",
    "教科": "教科版", "教育科学": "教科版",
    "北京": "北京版", "华东师大": "华东师大版",
    "西南师大": "西南师大版", "重庆大学": "重庆大学版",
    "人音": "人音版", "人美": "人美版",
    "苏少": "苏少版", "湘美": "湘美版", "赣美": "赣美版",
    "接力": "接力社版", "科普": "科普版", "中图": "中图版",
    "闽教": "闽教版", "川教": "川教版", "晋教": "晋教版",
    "鄂教": "鄂教版", "桂教": "桂教版", "辽海": "辽海版",
}

# 音乐记谱 / 英语起点
NOTATION_WORDS = {"五线谱": "五线谱", "简谱": "简谱"}
START_POINT_RE = re.compile(r"([一三])年级起点")

# 省份（用于中考真题检索与版本排序提示）
PROVINCES = [
    "北京", "天津", "上海", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
]
CITY_TO_PROVINCE = {
    "南京": "江苏", "苏州": "江苏", "无锡": "江苏", "徐州": "江苏",
    "杭州": "浙江", "宁波": "浙江", "温州": "浙江",
    "广州": "广东", "深圳": "广东", "东莞": "广东", "佛山": "广东",
    "合肥": "安徽", "芜湖": "安徽",
    "武汉": "湖北", "长沙": "湖南", "郑州": "河南", "济南": "山东",
    "青岛": "山东", "成都": "四川", "西安": "陕西", "福州": "福建",
    "厦门": "福建", "南昌": "江西", "昆明": "云南", "贵阳": "贵州",
    "哈尔滨": "黑龙江", "沈阳": "辽宁", "大连": "辽宁", "长春": "吉林",
    "石家庄": "河北", "太原": "山西", "南宁": "广西", "海口": "海南",
    "兰州": "甘肃", "银川": "宁夏", "乌鲁木齐": "新疆", "呼和浩特": "内蒙古",
}

# 省份版本偏好（仅用于排序加权，不是官方选用结论）
PROVINCE_PREFERENCES = {
    "江苏": ["苏教版", "译林版", "苏科版"],
    "上海": ["沪教版", "沪科技版", "沪音版"],
    "北京": ["北京版", "北师大版", "外研社版"],
    "广东": ["粤教版", "北师大版", "粤教粤科版"],
    "浙江": ["浙教版", "浙科版", "浙人美版"],
    "山东": ["鲁教版", "鲁科版", "青岛版"],
    "河北": ["冀教版", "冀少版", "冀美版"],
    "湖南": ["湘教版", "湘美版", "湘科版"],
    "福建": ["闽教版"], "四川": ["川教版"], "山西": ["晋教版"],
    "湖北": ["鄂教版"], "广西": ["桂教版", "桂美版"],
    "辽宁": ["辽海版"], "重庆": ["重庆大学版", "西南师大版"],
}
# 国家统编科目：默认优先统编版
UNIFIED_SUBJECTS = {"语文", "历史", "道德与法治", "思想政治"}

YEAR_RE = re.compile(r"(20\d{2}|(?<!\d)\d{2}(?=年|[省市]?中考))")


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def normalize(text: str) -> str:
    """统一全角/半角、大小写，并压掉分隔符差异。"""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("·", "").replace("•", "").replace("・", "")
    text = re.sub(r"[\s_\-—－()（）\[\]【】《》、,，.。:：]+", "", text)
    return text.lower()


WU_SI_DIR_RE = re.compile(r"[（(]\s*五\s*[•·・]?\s*四\s*学制\s*[）)]")


def is_wu_si_dir(stage: str) -> bool:
    """目录名是否属于五•四学制平行体系。"""
    return bool(WU_SI_DIR_RE.search(stage))


def base_stage(stage: str) -> str:
    """小学（五•四学制） -> 小学；初中（五•四学制） -> 初中。"""
    return WU_SI_DIR_RE.sub("", stage).strip()


def raw_url(path: str) -> str:
    return RAW_BASE + urllib.parse.quote(path)


def part_number(path: str) -> int:
    match = PART_RE.search(path)
    return int(match.group(1)) if match else 0


def expand_compact(text: str) -> str:
    """九上 -> 九年级 上册；高一 -> 高中 高一。"""
    def repl(match: re.Match) -> str:
        grade, half = match.group(1), match.group(2)
        grade = CN_DIGIT.get(grade, grade)
        return f"{grade}年级 {half}册 "

    text = COMPACT_GRADE_RE.sub(repl, text)
    for alias, canon in GRADE_ALIASES.items():
        if alias in text:
            text = text.replace(alias, f"{alias} {canon} ")
    return text


# --------------------------------------------------------------------------
# 索引加载
# --------------------------------------------------------------------------

def _filter_paths(paths: list[str]) -> list[str]:
    return [
        p for p in paths
        if SUPPORTED_FILE_RE.search(p) and not p.startswith(".cache/")
    ]


def load_bundled() -> list[str]:
    if not BUNDLED_INDEX.exists():
        raise SystemExit(f"缺少内置索引快照：{BUNDLED_INDEX}")
    with gzip.open(BUNDLED_INDEX, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def fetch_tree_api() -> list[str]:
    request = urllib.request.Request(
        TREE_API, headers={"User-Agent": "chinatextbook-skill", "Accept": "application/vnd.github+json"}
    )
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("truncated"):
        raise ValueError("GitHub 返回的树被截断")
    return _filter_paths([
        item["path"] for item in data.get("tree", []) if item.get("type") == "blob"
    ])


def load_index(refresh: bool = False, offline: bool = False) -> tuple[list[str], str]:
    """返回 (paths, source)。source 用于向用户说明索引新鲜度。"""
    if not refresh and CACHE_PATH.exists():
        age = time.time() - CACHE_PATH.stat().st_mtime
        if age < CACHE_TTL:
            try:
                return json.loads(CACHE_PATH.read_text(encoding="utf-8")), "cache"
            except (json.JSONDecodeError, OSError):
                pass

    if not offline:
        try:
            paths = fetch_tree_api()
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(paths, ensure_ascii=False), encoding="utf-8")
            return paths, "github-api"
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError) as exc:
            print(f"[提示] GitHub API 不可用（{exc}），改用内置索引快照。", file=sys.stderr)

    return _filter_paths(load_bundled()), "bundled-snapshot"


def load_taxonomy() -> dict:
    if TAXONOMY_PATH.exists():
        return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    return {}


# --------------------------------------------------------------------------
# 分组：把拆分件还原成一个逻辑条目
# --------------------------------------------------------------------------

def group_parts(paths: list[str]) -> dict[str, list[str]]:
    """两种拆分约定：
    1. X.pdf_merge_folder/X.pdf.N
    2. 同级兄弟文件 X.pdf.N
    """
    groups: dict[str, list[str]] = {}
    for path in paths:
        merge = MERGE_FOLDER_RE.match(path)
        if merge:
            base = merge.group(1)
        else:
            base = PART_RE.sub(".pdf", path)
        groups.setdefault(base, []).append(path)

    resolved: dict[str, list[str]] = {}
    for base, parts in groups.items():
        if base in parts:
            resolved[base] = [base]          # 存在完整单文件
        else:
            resolved[base] = sorted(parts, key=part_number)   # 数值序，避免 .10 排到 .2 前
    return resolved


# --------------------------------------------------------------------------
# 查询解析
# --------------------------------------------------------------------------

@dataclass
class Profile:
    stage: str | None = None
    wu_si: bool = False
    subject: str | None = None
    grade: str | None = None
    volume: str | None = None
    module: str | None = None
    publishers: list[str] = field(default_factory=list)
    notation: str | None = None
    start_point: str | None = None
    province: str | None = None
    year: str | None = None
    exam: bool = False
    materials: list[str] = field(default_factory=list)
    university: bool = False
    notes: list[str] = field(default_factory=list)


def parse_query(query: str, stage_override: str | None = None,
                province_override: str | None = None) -> Profile:
    raw = query
    text = expand_compact(query)
    norm = normalize(text)
    profile = Profile()

    # 中考真题
    if "中考" in raw or "真题" in raw or "试卷" in raw or "模拟" in raw:
        profile.exam = True
        profile.notes.append("识别为中考真题/试卷检索")

    # 五四学制
    if any(marker in raw.replace(" ", "") for marker in WU_SI_MARKERS):
        profile.wu_si = True
        profile.notes.append("识别为五•四学制")

    # 年级
    for alias, canon in GRADE_ALIASES.items():
        if alias in text:
            profile.grade = canon
            break
    if not profile.grade:
        match = re.search(r"([一二三四五六七八九])年级", text)
        if match:
            profile.grade = f"{match.group(1)}年级"

    # 学段
    if stage_override:
        profile.stage = stage_override
    else:
        for stage, aliases in STAGE_ALIASES.items():
            if any(normalize(a) in norm for a in aliases if len(a) > 1):
                profile.stage = stage
                break
        if not profile.stage and profile.grade:
            # 五•四学制的六年级属于初中，不能套用六•三学制的映射
            table = WU_SI_GRADE_TO_STAGE if profile.wu_si else GRADE_TO_STAGE
            profile.stage = table.get(profile.grade)
            if profile.wu_si and profile.stage:
                profile.notes.append(f"{profile.grade} 在五•四学制下属于 {profile.stage}")
        if not profile.stage and re.search(r"高[一二三]", raw):
            profile.stage = "高中"

    if re.search(r"高[一二三]", raw) and not profile.stage:
        profile.stage = "高中"

    # 学科（优先长词匹配，避免"地理"吃掉"地理图册"）
    best_subject, best_len = None, 0
    for subject, aliases in SUBJECT_ALIASES.items():
        for alias in aliases:
            if normalize(alias) in norm and len(alias) > best_len:
                best_subject, best_len = subject, len(alias)
    profile.subject = best_subject

    if profile.subject in UNIVERSITY_SUBJECTS:
        profile.university = True
        if not profile.stage:
            profile.stage = "大学"

    # 册别 / 模块
    for volume, aliases in VOLUME_ALIASES.items():
        if any(a in text for a in aliases if len(a) > 1):
            profile.volume = volume
            break
    module = MODULE_RE.search(raw)
    if module:
        profile.module = module.group(0).replace(" ", "")

    # 版本
    for alias, canon in sorted(PUBLISHER_ALIASES.items(), key=lambda kv: -len(kv[0])):
        if normalize(alias) in norm:
            if canon not in profile.publishers:
                profile.publishers.append(canon)
    for stage_pub in re.findall(r"([一-龥]{1,6}版)", raw):
        if stage_pub not in profile.publishers:
            profile.publishers.append(stage_pub)

    # 记谱法 / 起点
    for word, canon in NOTATION_WORDS.items():
        if word in raw:
            profile.notation = canon
    start = START_POINT_RE.search(raw)
    if start:
        profile.start_point = f"{start.group(1)}年级起点"

    # 省市
    if province_override:
        profile.province = province_override
    else:
        for city, province in CITY_TO_PROVINCE.items():
            if city in raw:
                profile.province = province
                profile.notes.append(f"{city} -> {province}")
                break
        if not profile.province:
            for province in PROVINCES:
                if province in raw:
                    profile.province = province
                    break

    # 年份
    year = YEAR_RE.search(raw)
    if year:
        profile.year = year.group(1)

    # 材料类型
    for material, aliases in MATERIAL_WORDS.items():
        if any(a in raw for a in aliases):
            profile.materials.append(material)

    return profile


def preferred_publishers(profile: Profile) -> list[str]:
    if profile.publishers:
        return []
    prefs: list[str] = []
    if profile.subject in UNIFIED_SUBJECTS:
        prefs.append("统编版")
    if profile.province:
        prefs.extend(PROVINCE_PREFERENCES.get(profile.province, []))
    prefs.append("人教版")
    return prefs


# --------------------------------------------------------------------------
# 检索
# --------------------------------------------------------------------------

@dataclass
class Result:
    path: str
    parts: list[str]
    score: int
    reasons: list[str]

    @property
    def split(self) -> bool:
        return not (len(self.parts) == 1 and self.parts[0] == self.path)

    @property
    def url(self) -> str:
        return raw_url(self.path)


def publisher_dir_matches(directory: str, wanted: str) -> bool:
    """`人教版` 应命中 `人教版-人民教育出版社`、`人教版（A版）...`。"""
    head = normalize(directory.split("-")[0])
    return normalize(wanted) in head or head.startswith(normalize(wanted))


def score_path(base: str, profile: Profile, tokens: list[str]) -> tuple[int, list[str]] | None:
    parts = base.split("/")
    stage = parts[0]
    subject = parts[1] if len(parts) > 1 else ""
    haystack = normalize(base)
    filename = parts[-1]

    score = 0
    reasons: list[str] = []

    # --- 学段 ---
    is_wu_si = is_wu_si_dir(stage)
    if profile.exam:
        if stage != EXERCISE_ROOT:
            return None
        score += 400
        reasons.append("中考真题目录")
    elif stage == EXERCISE_ROOT:
        return None      # 没要真题就不返回试卷

    if profile.stage and not profile.exam:
        if profile.stage == "大学":
            if stage != "大学":
                return None
            score += 300
            reasons.append("大学教材")
        else:
            # 用基础学段比较，让五•四与六•三两条平行体系同时进入排序，
            # 再由下面的学制加减分决定优劣，而不是直接过滤掉一条。
            if base_stage(stage) != base_stage(profile.stage):
                return None
            score += 200
            reasons.append(f"学段：{stage}")
            # 五四学制是独立平行体系，必须双向匹配
            if profile.wu_si and not is_wu_si:
                score -= 400
            elif is_wu_si and not profile.wu_si:
                score -= 300
                reasons.append("非五•四学制请求，降权五•四版本")
            elif profile.wu_si and is_wu_si:
                score += 250
                reasons.append("五•四学制匹配")
    elif is_wu_si and not profile.wu_si:
        score -= 300

    # --- 学科 ---
    if profile.subject:
        if subject == profile.subject:
            score += 500
            reasons.append(f"学科：{subject}")
        elif normalize(profile.subject) in normalize(subject):
            score += 300
            reasons.append(f"学科近似：{subject}")
        elif profile.subject in UNIVERSITY_SUBJECTS and subject == profile.subject:
            score += 300
        else:
            score -= 250

    # --- 年级 ---
    if profile.grade:
        if profile.grade in base:
            score += 260
            reasons.append(f"年级：{profile.grade}")
        else:
            score -= 200

    # --- 册别 / 模块 ---
    if profile.volume:
        if profile.volume in filename:
            score += 220
            reasons.append(f"册别：{profile.volume}")
        elif "全一册" in filename:
            score += 40
        else:
            score -= 180
    if profile.module:
        if normalize(profile.module) in normalize(filename):
            score += 240
            reasons.append(f"模块：{profile.module}")
        else:
            score -= 120

    # --- 版本 ---
    pub_dir = parts[2] if len(parts) > 3 else ""
    if profile.publishers:
        if any(publisher_dir_matches(pub_dir, p) for p in profile.publishers):
            score += 600
            reasons.append(f"指定版本：{pub_dir}")
        else:
            score -= 300
    else:
        for rank, pref in enumerate(preferred_publishers(profile), 1):
            if pub_dir and publisher_dir_matches(pub_dir, pref):
                bump = max(30, 180 - rank * 30)
                score += bump
                source = f"{profile.province}偏好" if profile.province else "默认偏好"
                reasons.append(f"{source}：{pref}")
                break

    # --- 记谱法 / 起点 ---
    if profile.notation:
        if profile.notation in base:
            score += 300
            reasons.append(f"记谱法：{profile.notation}")
        elif any(n in base for n in NOTATION_WORDS):
            score -= 250
    if profile.start_point:
        if profile.start_point in base:
            score += 300
            reasons.append(f"起点：{profile.start_point}")
        elif "年级起点" in base:
            score -= 200

    # --- 省份 / 年份（真题） ---
    if profile.exam:
        if profile.province and profile.province in base:
            score += 300
            reasons.append(f"省份：{profile.province}")
        for city, province in CITY_TO_PROVINCE.items():
            if profile.province == province and city in base:
                score += 200
                break
        if profile.year:
            short = profile.year[-2:]
            if profile.year in base or re.search(rf"(?<!\d){short}(?!\d)", filename):
                score += 250
                reasons.append(f"年份：{profile.year}")

    # --- 材料类型 ---
    wants_non_main = bool(profile.materials)
    has_non_main = any(w in filename for w in NON_MAIN_WORDS)
    if wants_non_main:
        if any(m in filename or any(a in filename for a in MATERIAL_WORDS[m])
               for m in profile.materials):
            score += 320
            reasons.append(f"材料类型：{'/'.join(profile.materials)}")
        elif not has_non_main:
            score -= 150
    elif has_non_main:
        score -= 320
        reasons.append("降权非主教材（教师用书/答案/全解）")

    # --- 自由词 ---
    matched = sum(1 for token in tokens if token in haystack)
    score += matched * 60
    if tokens and matched == len(tokens):
        score += 200
        reasons.append("全部关键词命中")

    # 短路径优先（更可能是主教材）
    score -= len(base) // 12

    if score <= 0 and matched == 0:
        return None
    return score, reasons


def free_tokens(query: str, profile: Profile) -> list[str]:
    text = expand_compact(query)
    consumed = [profile.subject or "", profile.grade or "", profile.volume or "",
                profile.module or "", profile.province or "", profile.stage or ""]
    consumed += profile.publishers + list(WU_SI_MARKERS)
    for word in consumed:
        if word:
            text = text.replace(word, " ")
    tokens = []
    for chunk in re.split(r"[\s，,、/]+", text):
        chunk = normalize(chunk)
        if len(chunk) >= 2 and chunk not in {"年级", "教材", "课本", "下载", "电子版", "教科书"}:
            tokens.append(chunk)
    return tokens


def search(paths: list[str], query: str, limit: int = 10,
           stage: str | None = None, province: str | None = None) -> tuple[list[Result], Profile]:
    profile = parse_query(query, stage_override=stage, province_override=province)
    tokens = free_tokens(query, profile)

    if not any([profile.subject, profile.grade, profile.publishers,
                profile.exam, profile.module, tokens]):
        raise SystemExit("请至少提供学科、年级、版本或书名关键词。")

    results: list[Result] = []
    for base, parts in group_parts(paths).items():
        scored = score_path(base, profile, tokens)
        if scored is None:
            continue
        score, reasons = scored
        results.append(Result(base, parts, score, reasons))

    results.sort(key=lambda r: (-r.score, len(r.path), r.path))
    return results[:limit], profile


def confident(results: list[Result]) -> bool:
    if not results:
        return False
    if len(results) == 1:
        return results[0].score > 300
    return results[0].score > 400 and results[0].score - results[1].score >= 150


# --------------------------------------------------------------------------
# 浏览（覆盖全库的目录导航）
# --------------------------------------------------------------------------

def browse(paths: list[str], prefix: str, limit: int) -> None:
    prefix = prefix.strip("/")
    depth = len(prefix.split("/")) if prefix else 0
    children: dict[str, int] = {}
    for path in paths:
        if prefix and not path.startswith(prefix + "/"):
            continue
        parts = path.split("/")
        if len(parts) <= depth:
            continue
        child = parts[depth]
        children[child] = children.get(child, 0) + 1

    if not children:
        print(f"没有匹配的目录：{prefix or '/'}")
        return

    print(f"{prefix or '(根目录)'}  —  {len(children)} 项")
    for name, count in sorted(children.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]:
        marker = "📄" if SUPPORTED_FILE_RE.search(name) else "📁"
        print(f"  {marker} {name}  ({count})")


# --------------------------------------------------------------------------
# 下载
# --------------------------------------------------------------------------

def download_file(url: str, destination: Path, overwrite: bool = False) -> None:
    if destination.exists() and not overwrite:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "chinatextbook-skill"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    partial.replace(destination)


def download_result(result: Result, out_dir: Path, overwrite: bool = False) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / Path(result.path).name

    if not result.split:
        download_file(result.url, final, overwrite=overwrite)
        return final

    numbers = [part_number(p) for p in result.parts]
    if numbers != list(range(1, len(numbers) + 1)):
        raise ValueError(f"拆分件编号不连续：{numbers}（可能上游缺件，拒绝合并）")

    if final.exists() and not overwrite:
        return final

    temp: list[Path] = []
    for part in result.parts:
        part_path = out_dir / Path(part).name
        download_file(raw_url(part), part_path, overwrite=overwrite)
        temp.append(part_path)

    partial = final.with_name(final.name + ".part")
    with partial.open("wb") as merged:
        for part_path in temp:
            with part_path.open("rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    merged.write(chunk)
    partial.replace(final)

    for part_path in temp:
        try:
            part_path.unlink()
        except OSError:
            pass
    return final


# --------------------------------------------------------------------------
# 输出
# --------------------------------------------------------------------------

def print_results(results: list[Result], profile: Profile, explain: bool, as_json: bool) -> None:
    if as_json:
        print(json.dumps([
            {"path": r.path, "url": r.url, "score": r.score,
             "split": r.split, "parts": r.parts, "reasons": r.reasons}
            for r in results
        ], ensure_ascii=False, indent=2))
        return

    if not results:
        print("没有找到候选。可以放宽条件，或用 browse 子命令浏览目录。")
        return

    if explain and profile.notes:
        for note in profile.notes:
            print(f"[解析] {note}")

    for index, result in enumerate(results, 1):
        flag = f"  [拆分 {len(result.parts)} 件]" if result.split else ""
        print(f"{index}. {result.path}{flag}")
        if explain:
            print(f"   得分 {result.score}｜{'；'.join(result.reasons) or '关键词匹配'}")


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------

def cmd_search(args) -> int:
    paths, source = load_index(refresh=args.refresh, offline=args.offline)
    results, profile = search(paths, args.query, args.limit, args.stage, args.province)
    if args.explain:
        print(f"[索引] {source}（{len(paths)} 个文件）")
    print_results(results, profile, args.explain, args.json)
    return 0


def cmd_browse(args) -> int:
    paths, source = load_index(refresh=args.refresh, offline=args.offline)
    browse(paths, args.path or "", args.limit)
    return 0


def cmd_download(args) -> int:
    paths, source = load_index(refresh=args.refresh, offline=args.offline)
    results, profile = search(paths, args.query, max(args.limit, 5), args.stage, args.province)
    if not results:
        print("没有找到候选，已中止下载。")
        return 1

    if args.pick:
        if args.pick > len(results):
            print(f"序号超出范围：{args.pick} > {len(results)}")
            return 1
        chosen = results[args.pick - 1]
    elif args.auto:
        if not confident(results):
            print("候选不够明确，拒绝自动下载。请人工确认：")
            print_results(results, profile, True, False)
            return 1
        chosen = results[0]
    else:
        print("候选列表（用 --pick N 选择，或 --auto 在高置信时自动下载）：")
        print_results(results, profile, args.explain, False)
        return 0

    out_dir = Path(args.out).expanduser() if args.out else DEFAULT_OUT
    print(f"正在下载：{chosen.path}")
    if chosen.split:
        print(f"  拆分为 {len(chosen.parts)} 件，将按序合并。")
    final = download_result(chosen, out_dir, overwrite=args.overwrite)
    print(f"完成：{final}")
    print(f"上游：{chosen.path}")
    print(f"来源：{chosen.url}")
    return 0


def cmd_coverage(args) -> int:
    paths, source = load_index(refresh=args.refresh, offline=args.offline)
    grouped = group_parts(paths)
    stages: dict[str, dict[str, int]] = {}
    for base in grouped:
        parts = base.split("/")
        if len(parts) < 2:
            continue
        stages.setdefault(parts[0], {})
        stages[parts[0]][parts[1]] = stages[parts[0]].get(parts[1], 0) + 1

    print(f"索引来源：{source}")
    print(f"文件总数：{len(paths)}；合并后条目：{len(grouped)}\n")
    for stage in sorted(stages, key=lambda s: -sum(stages[s].values())):
        total = sum(stages[stage].values())
        print(f"■ {stage}（{total} 册）")
        for subject, count in sorted(stages[stage].items(), key=lambda kv: -kv[1]):
            print(f"    {subject}：{count}")
        print()
    return 0


def cmd_self_test(args) -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{name} {detail}".strip())

    # 拆分件分组：两种约定 + 数值序
    sample = [
        "a/b/c/X.pdf_merge_folder/X.pdf.1",
        "a/b/c/X.pdf_merge_folder/X.pdf.2",
        "a/b/c/X.pdf_merge_folder/X.pdf.10",
        "d/e/Y.pdf.1",
        "d/e/Y.pdf.2",
        "f/g/Z.pdf",
    ]
    groups = group_parts(sample)
    check("merge_folder 分组", "a/b/c/X.pdf" in groups)
    check("数值序", [part_number(p) for p in groups["a/b/c/X.pdf"]] == [1, 2, 10],
          str(groups["a/b/c/X.pdf"]))
    check("兄弟拆分件分组", groups.get("d/e/Y.pdf") == ["d/e/Y.pdf.1", "d/e/Y.pdf.2"])
    check("单文件不误判", groups.get("f/g/Z.pdf") == ["f/g/Z.pdf"])

    # 紧凑写法
    check("九上展开", "九年级" in expand_compact("九上英语") and "上册" in expand_compact("九上英语"),
          expand_compact("九上英语"))

    # 查询解析
    p = parse_query("江苏 九上 译林 英语")
    check("解析学科", p.subject == "英语", str(p.subject))
    check("解析年级", p.grade == "九年级", str(p.grade))
    check("解析学段", p.stage == "初中", str(p.stage))
    check("解析版本", "译林版" in p.publishers, str(p.publishers))
    check("解析省份", p.province == "江苏", str(p.province))

    p2 = parse_query("山东 五四学制 六年级 数学 上册")
    check("五四学制识别", p2.wu_si is True)
    # 回归：五•四学制的六年级属于初中，不能按六•三学制归到小学
    check("五四六年级归初中", p2.stage == "初中（五•四学制）", str(p2.stage))
    check("六三六年级归小学", parse_query("六年级 数学 上册").stage == "小学")
    check("base_stage 归一", base_stage("初中（五•四学制）") == "初中")
    check("is_wu_si_dir", is_wu_si_dir("小学（五•四学制）") and not is_wu_si_dir("小学"))

    p3 = parse_query("人音版 五线谱 三年级 音乐")
    check("记谱法识别", p3.notation == "五线谱", str(p3.notation))
    check("音乐学科", p3.subject == "音乐", str(p3.subject))

    p4 = parse_query("2018 北京 中考 数学 真题")
    check("真题识别", p4.exam is True)
    check("真题年份", p4.year == "2018", str(p4.year))

    p5 = parse_query("初中 地理图册")
    check("地理图册优先于地理", p5.subject == "地理图册", str(p5.subject))

    p6 = parse_query("高中 数学 人教A版 选择性必修 第一册")
    check("模块识别", p6.module and "选择性必修" in p6.module, str(p6.module))

    # 版本目录匹配
    check("短名命中长目录", publisher_dir_matches("人教版-人民教育出版社", "人教版"))
    check("短名命中主编变体", publisher_dir_matches("人美版（主编：杨力）-人民美术出版社", "人美版"))
    check("不误命中", not publisher_dir_matches("苏教版-江苏凤凰教育出版社", "人教版"))

    # 真实索引联调
    try:
        paths = _filter_paths(load_bundled())
    except SystemExit as exc:
        failures.append(f"内置索引加载失败：{exc}")
        paths = []

    if paths:
        check("索引规模", len(paths) > 2000, str(len(paths)))
        stage_set = {p.split("/")[0] for p in paths}
        for expected in ("小学", "初中", "高中", "大学", EXERCISE_ROOT):
            check(f"覆盖学段 {expected}", expected in stage_set)
        check("覆盖五四学制", any("四学制" in s for s in stage_set), str(stage_set))

        subjects = {p.split("/")[1] for p in paths if len(p.split("/")) > 1}
        for expected in ("音乐", "美术", "体育与健康", "通用技术", "信息技术",
                         "地理图册", "俄语", "日语", "科学", "艺术"):
            check(f"覆盖学科 {expected}", expected in subjects)

        # 端到端检索
        cases = [
            ("初中 九年级 英语 译林", "译林"),
            ("小学 三年级 语文", "语文"),
            ("高中 通用技术", "通用技术"),
            ("初中 音乐 人音版", "音乐"),
            ("大学 高等数学 同济 第七版", "高等数学"),
            ("2018 北京 中考 数学", "中考"),
        ]
        for query, expect in cases:
            try:
                results, _ = search(paths, query, 5)
            except SystemExit as exc:
                failures.append(f"检索异常 {query}：{exc}")
                continue
            check(f"检索 “{query}”", bool(results) and expect in results[0].path,
                  results[0].path if results else "无结果")

    if failures:
        print("self-test 失败：")
        for failure in failures:
            print(f"  ✗ {failure}")
        return 1

    print("self-test passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chinatextbook",
        description="检索并下载 TapXWorld/ChinaTextbook 全库公开教材文件。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("--refresh", action="store_true", help="强制刷新索引")
        p.add_argument("--offline", action="store_true", help="只用内置索引快照")
        p.add_argument("--limit", type=int, default=10)

    s = sub.add_parser("search", help="按自然语言检索")
    s.add_argument("query")
    s.add_argument("--stage", help="强制学段：小学/初中/高中/大学")
    s.add_argument("--province", help="强制省份")
    s.add_argument("--explain", action="store_true")
    s.add_argument("--json", action="store_true")
    common(s)
    s.set_defaults(func=cmd_search)

    b = sub.add_parser("browse", help="浏览目录树")
    b.add_argument("path", nargs="?", default="", help="如 高中/物理")
    common(b)
    b.set_defaults(func=cmd_browse)

    d = sub.add_parser("download", help="下载教材")
    d.add_argument("query")
    d.add_argument("--stage")
    d.add_argument("--province")
    d.add_argument("--pick", type=int, help="选择第 N 个候选")
    d.add_argument("--auto", action="store_true", help="高置信时自动下载")
    d.add_argument("--out", help=f"输出目录，默认 {DEFAULT_OUT}")
    d.add_argument("--overwrite", action="store_true")
    d.add_argument("--explain", action="store_true")
    common(d)
    d.set_defaults(func=cmd_download)

    c = sub.add_parser("coverage", help="打印全库覆盖统计")
    common(c)
    c.set_defaults(func=cmd_coverage)

    t = sub.add_parser("self-test", help="运行内置检查")
    common(t)
    t.set_defaults(func=cmd_self_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
