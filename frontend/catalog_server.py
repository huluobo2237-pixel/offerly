"""Public, read-only shared job API. Collection and credentials stay on the owner service."""

import argparse
from pathlib import Path

from aiohttp import web
from pydantic import ValidationError

from .job_catalog import JobCatalog
from .job_search import SearchInput


def make_catalog_app(root: Path):
    catalog = JobCatalog(root / "job-search")
    app = web.Application(client_max_size=8192)

    async def jobs(request):
        try:
            query = SearchInput.model_validate(dict(request.query))
        except (ValueError, ValidationError):
            raise web.HTTPBadRequest(text="岗位筛选条件不正确。") from None
        response = web.json_response(catalog.search(query).model_dump())
        response.headers.update(
            {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        )
        return response

    app.router.add_get("/api/jobs", jobs)
    return app


def main():
    from offerly.paths import DATA

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8997)
    args = parser.parse_args()
    web.run_app(
        make_catalog_app(args.data_dir), host=args.host, port=args.port, access_log=None
    )


if __name__ == "__main__":
    main()
