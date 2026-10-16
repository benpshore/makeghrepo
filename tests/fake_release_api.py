"""Stateful GitHub release API model; invoked only as an inert fake gh in tests.

Legacy uses creation date first and semantic version only as a tie-break,
matching GitHub's historical policy. This model makes no stronger guarantee.
Draft-by-tag REST requests fail; GraphQL lookup and numeric REST IDs work.
"""

import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path


def main():
    args = sys.argv[1:]
    state = Path("state.json")
    data = json.loads(state.read_text())
    with Path("requests.jsonl").open("a") as requests:
        requests.write(json.dumps(args) + "\n")

    def save():
        state.write_text(json.dumps(data))

    def fields():
        values = {}
        for index, arg in enumerate(args):
            if arg in {"-f", "-F"}:
                key, value = args[index + 1].split("=", 1)
                values[key] = json.loads(value) if arg == "-F" else value
        return values

    def add_tag(tag, source):
        oid = hashlib.sha1(tag.encode(), usedforsecurity=False).hexdigest()
        data["objects"][oid] = {"tag": tag, "source": source}
        return oid

    def publish(tag, mode):
        release = data["releases"][tag]
        assert release["assets"]
        release["draft"] = False
        data["publications"] += 1
        if mode == "true":
            data["latest"] = tag
        elif mode == "legacy":
            stable = {
                name: (item["created_at"], tuple(map(int, name[1:].split("."))))
                for name, item in data["releases"].items()
                if not item["draft"]
                and not item["prerelease"]
                and re.fullmatch(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", name)
            }
            data["latest"] = max(stable, key=stable.get)
        else:
            assert mode == "false"

    def complete_publication(tag, mode):
        if other := os.environ.get("PUBLISH_BEFORE_PATCH"):
            # Another publication lands after verification but before this
            # API request. The server's decision sees that current state.
            publish(other, "true")
        publish(tag, mode)
        save()
        if os.environ.get("LOSE_PUBLICATION_RESPONSE"):
            sys.exit(1)

    if args[0] == "api":
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        endpoint = next(arg for arg in args[1:] if arg.startswith("repos/"))
        if method == "POST" and endpoint.endswith("/git/tags"):
            request = fields()
            assert request["type"] == "commit"
            print(add_tag(request["tag"], request["object"]))
        elif method == "POST" and endpoint.endswith("/git/refs"):
            request = fields()
            tag = request["ref"].removeprefix("refs/tags/")
            assert tag not in data["tags"]
            assert data["objects"][request["sha"]]["tag"] == tag
            data["tags"][tag] = request["sha"]
            data["tag_creations"] += 1
        elif method == "GET" and "/git/ref/tags/" in endpoint:
            tag = endpoint.split("/git/ref/tags/", 1)[1]
            if tag not in data["tags"]:
                sys.exit(1)
            print(json.dumps({"object": {"type": "tag", "sha": data["tags"][tag]}}))
        elif method == "GET" and "/git/tags/" in endpoint:
            source = data["objects"][endpoint.rsplit("/", 1)[1]]["source"]
            print(json.dumps({"object": {"type": "commit", "sha": source}}))
        elif method == "GET" and "/releases/tags/" in endpoint:
            tag = endpoint.split("/releases/tags/", 1)[1]
            release = copy.deepcopy(data["releases"][tag])
            if release["draft"]:
                print(
                    "GET release by tag only supports published releases (HTTP 404)",
                    file=sys.stderr,
                )
                sys.exit(1)
            if os.environ.get("BAD_REMOTE_DIGEST"):
                release["assets"][0]["digest"] = "sha256:incorrect"
            print(json.dumps(release))
        elif method == "GET" and re.fullmatch(r"repos/[^/]+/[^/]+/releases/[0-9]+", endpoint):
            release_id = int(endpoint.rsplit("/", 1)[1])
            release = copy.deepcopy(
                next(v for v in data["releases"].values() if v["id"] == release_id)
            )
            if os.environ.get("BAD_RELEASE_ID"):
                release["id"] = release_id + 1
            if os.environ.get("BAD_RELEASE_TAG"):
                release["tag_name"] = "v99.0.0"
            if os.environ.get("BAD_REMOTE_DIGEST"):
                release["assets"][0]["digest"] = "sha256:incorrect"
            print(json.dumps(release))
        elif method == "GET" and endpoint.endswith("/releases?per_page=100"):
            assert "--paginate" in args and "--slurp" in args
            if os.environ.get("FAIL_CATALOG"):
                sys.exit(1)
            releases = sorted(
                copy.deepcopy(list(data["releases"].values())),
                key=lambda r: r["created_at"],
                reverse=True,
            )
            mode = os.environ.get("BAD_CATALOG")
            if mode == "duplicate_id":
                releases.append(releases[0])
            elif mode == "duplicate_tag":
                releases.append(dict(releases[0], id=9999))
            elif mode == "malformed_id":
                releases[0]["id"] = "not-a-number"
            elif mode == "malformed_state":
                releases[0]["draft"] = "false"
            elif mode == "malformed_tag":
                releases.append(dict(releases[0], id=9999, tag_name="v01.2.0", draft=False))
            elif mode == "missing_candidate":
                releases = [r for r in releases if r["tag_name"] != "v" + os.environ["VERSION"]]
            pages = [releases[i : i + 100] for i in range(0, len(releases), 100)] or [[]]
            print(json.dumps({"unexpected": pages} if mode == "malformed_pages" else pages))
        elif method == "PATCH" and "/releases/" in endpoint:
            request = fields()
            release_id = int(endpoint.rsplit("/", 1)[1])
            tag = next(k for k, v in data["releases"].items() if v["id"] == release_id)
            assert request["tag_name"] == tag
            assert request["draft"] is False
            complete_publication(tag, request.get("make_latest", "true"))
        else:
            raise AssertionError(args)
    else:
        assert args[0] == "release"
        command, tag = args[1:3]
        assert tag == "v" + os.environ["VERSION"]
        if command == "view":
            if tag not in data["releases"]:
                sys.exit(1)
            release = data["releases"][tag]
            if "--jq" in args:
                print(str(release["draft"]).lower())
            else:
                assert args[args.index("--json") + 1] == "databaseId,tagName"
                lookup = {"databaseId": release["id"], "tagName": release["tag_name"]}
                if os.environ.get("BAD_LOOKUP_ID"):
                    lookup["databaseId"] = "../other"
                if os.environ.get("BAD_LOOKUP_TAG"):
                    lookup["tagName"] = "v99.0.0"
                print(json.dumps(lookup))
        elif command == "create":
            assert "--draft" in args and "--verify-tag" in args
            assert tag in data["tags"] and tag not in data["releases"]
            data["releases"][tag] = {
                "id": len(data["releases"]) + 1,
                "tag_name": tag,
                "draft": True,
                "prerelease": False,
                "created_at": "2026-01-02T00:00:00Z",
                "assets": [],
            }
            data["release_creations"] += 1
        elif command == "upload":
            release = data["releases"][tag]
            assert release["draft"]
            assets = []
            for name in args[3:]:
                if name == "--clobber":
                    continue
                path = Path(name)
                content = path.read_bytes()
                assets.append(
                    {
                        "name": path.name,
                        "size": len(content),
                        "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
                    }
                )
            release["assets"] = assets
            data["uploads"] += 1
            save()
            if os.environ.get("FAIL_UPLOAD"):
                sys.exit(1)
        elif command == "edit":
            # Model the old command too: a regression must change latest and
            # fail behavior assertions, not merely reject an unknown command.
            assert "--draft=false" in args
            complete_publication(tag, "false" if "--latest=false" in args else "true")
        else:
            raise AssertionError(args)
    save()


if __name__ == "__main__":
    main()
