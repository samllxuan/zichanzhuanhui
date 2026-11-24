"""Read the live repaint queue and deliver locally generated images to a specific run."""
import argparse
from contextlib import ExitStack, contextmanager
import fcntl
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

if __package__:
    from .repaint_inputs import select_source
else:
    from repaint_inputs import select_source

ROOT = Path(__file__).resolve().parents[1]
MAX_ACTIVE_PROJECTS = 2


@contextmanager
def project_slot():
    directory = ROOT / "runs" / ".repaint-slots"
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(MAX_ACTIVE_PROJECTS):
        with (directory / f"{index}.lock").open("a") as slot:
            try:
                fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            yield True
            return
    yield False


def configuration():
    values = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            values[name.strip()] = value.strip().strip("\"'")
    return {**values, **os.environ}


class Queue:
    def __init__(self, config):
        self.base = config["REPAINT_API_BASE_URL"].rstrip("/")
        if urlsplit(self.base).scheme != "https":
            raise ValueError("需要 HTTPS 平台地址")
        self.org = config["REPAINT_API_KEY"]

    def request(self, path, method="GET", **kwargs):
        headers = {"Authorization": f"Bearer {self.org}"}
        if method in {"PATCH", "POST"}:
            headers["X-Operator"] = "partner-studio"
        if method == "POST":
            headers["Content-Type"] = "image/png"
        response = requests.request(method, self.base + path, headers=headers,
                                    allow_redirects=False, timeout=(12, 120), **kwargs)
        if not 200 <= response.status_code < 300:
            try:
                error = response.json()["error"]
                code = str(error["code"])
                if (error.get("details") or {}).get("candidateSaved"):
                    code += " candidateSaved=true"
            except (ValueError, TypeError, KeyError):
                code = ""
            if response.status_code == 429:
                code += " Retry-After=" + response.headers.get("Retry-After", "unknown")
            raise RuntimeError(f"HTTP {response.status_code} {code} {path}")
        return response.json()

    def pages(self, path, **filters):
        params = {"limit": 200, **filters}
        seen = set()
        while True:
            page = self.request(path, params=dict(params))
            yield from page["items"]
            cursor = page["nextCursor"]
            if cursor is None:
                return
            if not cursor or cursor in seen:
                raise ValueError("无效或重复的分页游标")
            seen.add(cursor)
            params["cursor"] = cursor

    def assets(self, project, categories=("character", "scene", "prop"), *, primary_only=False, repaint_inputs=False):
        if not categories or not set(categories) <= {"character", "scene", "prop"}:
            raise ValueError("资产类型必须为 character、scene 或 prop")
        path = "/projects/" + quote(project, safe="") + "/assets"
        rows = {}
        for row in self.pages(path, types=",".join(categories)):
            if row["type"] not in categories:
                continue
            asset = row["assetId"]
            if asset in rows and rows[asset] != row:
                raise ValueError("分页期间资产资料发生变化，请重新读取")
            rows[asset] = row
        if repaint_inputs:
            selected, skipped = [], []
            for row in rows.values():
                url, reason = select_source(row)
                if reason:
                    skipped.append({"assetId": row["assetId"], "type": row["type"], "reason": reason})
                else:
                    selected.append({**row, "repaintInputUrl": url})
            return {"projectId": project, "total": len(selected), "items": selected, "skipped": skipped}
        if primary_only:
            rows = {key: row for key, row in rows.items()
                    if ((row.get("primaryImage") or {}).get("url") or "").strip()}
        return {"projectId": project, "total": len(rows), "items": list(rows.values())}

    def pending(self):
        pending, resume = [], []
        for row in self.pages("/projects"):
            project = row["projectId"]
            entry = {"projectId": project, "name": row.get("name")}
            if row["repaintStatus"] == 1:
                pending.append(entry)
            elif row["repaintStatus"] == 3:
                # Only resume runs this installation actually started.
                folder = self.workdir(project)
                if folder.exists() and any(folder.glob("*/started")):
                    task = self.detail(project)
                    run = task.get("runId")
                    if run and task.get("status") == 3 and (self.workdir(project, run) / "started").exists():
                        resume.append({**entry, "runId": run})
        return {"scope": "organization", "pending": pending, "resume": resume}

    @staticmethod
    def workdir(project, run=None):
        for part in (project,) if run is None else (project, run):
            if not part or part in (".", "..") or Path(part).name != part:
                raise ValueError("无效的项目或轮次编号")
        folder = ROOT / "runs" / project / "auto"
        return folder if run is None else folder / run

    def detail(self, project):
        detail = self.request("/projects/" + quote(project, safe=""))
        return detail["repaintTask"] or {}

    @contextmanager
    def start(self, project, run):
        folder = self.workdir(project, run)
        folder.mkdir(parents=True, exist_ok=True)
        with ExitStack() as stack:
            lock = stack.enter_context((folder / "worker.lock").open("a"))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield {"working": False, "reason": "already-running"}
                return
            if not stack.enter_context(project_slot()):
                yield {"working": False, "reason": "capacity-full"}
                return
            task = self.detail(project)
            marker = folder / "started"
            if task.get("runId") != run or task.get("status") not in (1, 3):
                yield {"working": False, "reason": "stale-run"}
                return
            if task["status"] == 3 and not marker.exists():
                yield {"working": False, "reason": "started-elsewhere"}
                return
            if task["status"] == 1:
                # Persist intent before PATCH so a lost response can be reconciled.
                marker.touch()
                result = self.request("/projects/" + quote(project, safe=""), "PATCH",
                                      json={"repaintStatus": 3, "runId": run})
                if result.get("status") != 3 or result.get("runId") != run:
                    raise RuntimeError("开工回执未匹配当前轮次，请回读项目详情")
            yield {"working": True, "runId": run, "status": 3}

    def deliver(self, project, run, asset, file):
        task = self.detail(project)
        if task.get("runId") != run:
            raise ValueError("本轮任务已变更，请重新取单")
        item = next((i for i in task["items"] if i["assetId"] == asset), None)
        if item is None:
            raise ValueError("资产已移出本轮")
        if item["done"]:
            return {"done": True, "variantId": item["variantId"], "alreadyDelivered": True}
        if task["status"] != 3:
            raise ValueError("本轮尚未开工或已结束")
        route = "/projects/{}/assets/{}".format(quote(project, safe=""), quote(asset, safe=""))
        current = self.request(route)
        if not ((current.get("primaryImage") or {}).get("url") or "").strip():
            return {"assetId": asset, "done": False, "skipped": "missing-primary-image"}
        if file.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("回传文件超过 10 MiB")
        with file.open("rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                raise ValueError("回传文件须为 PNG")
            stream.seek(0)
            receipt = self.request(route + "/candidates", "POST",
                                   params={"setAsPrimary": "true"}, data=stream)
        if not (receipt.get("done") is True and receipt.get("runId") == run
                and receipt.get("assetId") == asset and receipt.get("variantId")):
            raise ValueError("回传结果需重新读取任务详情核对")
        return receipt


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="cmd", required=True)
    commands.add_parser("poll")
    commands.add_parser("task").add_argument("project")
    assets = commands.add_parser("assets", help="按类型分页读取全集资产")
    assets.add_argument("project")
    assets.add_argument("--types", default="character,scene,prop", help="用逗号分隔资产类型")
    selection = assets.add_mutually_exclusive_group()
    selection.add_argument("--primary-only", action="store_true", help="只返回有定妆图 URL 的待选素材")
    selection.add_argument("--repaint-inputs", action="store_true", help="选择已定妆资产的 sourceImage 原图，列出跳过原因")
    start = commands.add_parser("start", help="持有本轮独占锁至输入换行或关闭会话")
    start.add_argument("project")
    start.add_argument("run")
    send = commands.add_parser("deliver")
    for field in ("project", "run", "asset"):
        send.add_argument(field)
    send.add_argument("file", type=Path)
    args = parser.parse_args()
    queue = Queue(configuration())
    if args.cmd == "poll":
        result = queue.pending()
    elif args.cmd == "start":
        if not sys.stdin.isatty():
            parser.error("start 需要持续打开的交互终端")
        with queue.start(args.project, args.run) as lease:
            print(json.dumps(lease), flush=True)
            if lease["working"]:
                sys.stdin.readline()
        return
    elif args.cmd == "task":
        result = queue.detail(args.project)
    elif args.cmd == "assets":
        result = queue.assets(args.project, args.types.split(","), primary_only=args.primary_only,
                              repaint_inputs=args.repaint_inputs)
    else:
        result = queue.deliver(args.project, args.run, args.asset, args.file)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
