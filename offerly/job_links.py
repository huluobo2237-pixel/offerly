"""Keep known public job routes current without changing their job identity."""

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def application_job_url(url: str) -> str:
    """Repair the obsolete XHS query route emitted by the collection dependency.

    The live site's anchors use /campus/position/<id>; the old query route now
    renders the entire listing. Only this verified host/route/id is rewritten.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url  # The API caller still rejects malformed URLs normally.
    if (
        parts.scheme != "https"
        or parts.netloc.lower() != "job.xiaohongshu.com"
        or not re.fullmatch(r"/(campus|social|intern)/position/?", parts.path)
    ):
        return url
    query = parse_qsl(parts.query, keep_blank_values=True)
    ids = [value for key, value in query if key == "positionId"]
    if len(ids) != 1 or not re.fullmatch(r"\d{1,30}", ids[0]):
        return url
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path.rstrip("/") + "/" + ids[0],
            urlencode([(key, value) for key, value in query if key != "positionId"]),
            parts.fragment,
        )
    )
