"""Company-name and recruitment-domain matching before a result reaches the Agent."""

import re


def normalize_company(value):
    return re.sub(r"[\s·（）()]+", "", value or "").casefold()


# Explicit aliases, not a guess that every investment belongs to the same employer.
# Group recruitment source: https://talent.alibaba.com/ (includes Amap and Taotian).
ALIASES = [
    (
        {"阿里", "阿里巴巴", "阿里巴巴集团", "alibaba"},
        ("阿里", "alibaba", "高德", "amap", "淘天", "淘宝", "天猫", "taobao", "tmall"),
    ),
    ({"哔哩哔哩", "bilibili", "b站"}, ("哔哩哔哩", "bilibili", "b站")),
    ({"腾讯", "tencent"}, ("腾讯", "tencent")),
    ({"字节", "字节跳动", "bytedance"}, ("字节", "bytedance")),
    ({"快手", "kuaishou"}, ("快手", "kuaishou")),
    ({"小红书", "xiaohongshu"}, ("小红书", "xiaohongshu")),
    ({"美团", "meituan"}, ("美团", "meituan")),
    ({"华为", "huawei"}, ("华为", "huawei")),
    ({"京东", "jd"}, ("京东", "jd.com")),
]


def blacklist_match(blacklist, *evidence):
    raw = " ".join(str(x or "") for x in evidence).casefold()
    text = normalize_company(raw)
    for entry in re.split(r"[\n,，;；]+", blacklist or ""):
        name = normalize_company(entry)
        if not name:
            continue
        terms = next((aliases for roots, aliases in ALIASES if name in roots), (name,))
        for term in terms:
            # Short English names must not match part of another company's name.
            if re.fullmatch(r"[a-z0-9.]+", term) and not re.search(r"\s", entry):
                if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", raw):
                    return entry.strip()
            elif term in text:
                return entry.strip()
    return ""
