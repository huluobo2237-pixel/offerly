"""Exclude known job aggregators; employer ownership still needs source evidence."""

from urllib.parse import urlsplit


def application_url_allowed(url):
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    blocked = (
        "nowcoder.com",
        "zhipin.com",
        "liepin.com",
        "zhaopin.com",
        "51job.com",
        "yingjiesheng.com",
        "shixiseng.com",
        "haitou.cc",
        "jobui.com",
        "offershow.cn",
        "gaoxiaojob.com",
        "zhaopin.cn",
    )
    return bool(host) and not (
        any(host == domain or host.endswith("." + domain) for domain in blocked)
        or host.endswith((".edu.cn", ".edu", ".ac.cn"))
    )
