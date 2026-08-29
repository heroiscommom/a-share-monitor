#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 GitHub Git Data API 推送本地提交（绕过 git 协议的间歇性断网）"""
import subprocess
import json
import base64
import sys

REPO = "heroiscommom/a-share-monitor"
MAX_RETRY = 5


def gh(*args, input=None, method=None):
    cmd = ["gh", "api"]
    if method:
        cmd += ["-X", method]
    cmd += list(args)
    if input is not None:
        cmd += ["--input", "-"]
    for i in range(MAX_RETRY):
        p = subprocess.run(cmd, input=input, capture_output=True, text=True)
        if p.returncode == 0:
            return p.stdout.strip()
        if "503" in p.stderr or "504" in p.stderr or "in time" in p.stderr or "timeout" in p.stderr.lower():
            print(f"  (retry {i + 1} after 503)", file=sys.stderr)
            import time
            time.sleep(6 * (i + 1))
            continue
        print("gh error:", p.stderr[-500:], file=sys.stderr)
        sys.exit(1)
    print("gh error: 重试耗尽", file=sys.stderr)
    sys.exit(1)


def git(*args, binary=False):
    p = subprocess.run(["git"] + list(args), capture_output=True)
    if p.returncode != 0:
        print("git error:", p.stderr.decode()[-300:], file=sys.stderr)
        sys.exit(1)
    return p.stdout if binary else p.stdout.decode()


def main():
    commits = sys.argv[1:] or ["2650f35", "5807177"]
    # 远端当前 ref 与 tree
    ref_sha = json.loads(gh(f"repos/{REPO}/git/refs/heads/main"))["object"]["sha"]
    base_tree = json.loads(gh(f"repos/{REPO}/git/commits/{ref_sha}"))["tree"]["sha"]
    print(f"[remote] ref={ref_sha[:8]} tree={base_tree[:8]}")

    parent = ref_sha
    for C in commits:
        msg = git("log", "-1", "--format=%s%n%n%b", C).strip()
        name_status = git("diff-tree", "-r", "--no-commit-id", "--name-status", C)
        files = []
        for line in name_status.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                files.append((parts[0], parts[1]))  # (status, path)

        tree_entries = []
        for status, path in files:
            if status == "D":
                tree_entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
                continue
            content = git("show", f"{C}:{path}", binary=True)
            mode = git("ls-tree", C, path).split()[0]
            blob = json.loads(gh(
                f"repos/{REPO}/git/blobs",
                input=json.dumps({"content": base64.b64encode(content).decode(), "encoding": "base64"}),
            ))["sha"]
            tree_entries.append({"path": path, "mode": mode, "type": "blob", "sha": blob})
            print(f"  blob {path} {blob[:8]}")

        new_tree = json.loads(gh(
            f"repos/{REPO}/git/trees",
            input=json.dumps({"base_tree": base_tree, "tree": tree_entries}),
        ))["sha"]
        new_commit = json.loads(gh(
            f"repos/{REPO}/git/commits",
            input=json.dumps({"message": msg, "tree": new_tree, "parents": [parent]}),
        ))["sha"]
        gh(f"repos/{REPO}/git/refs/heads/main", input=json.dumps({"sha": new_commit, "force": False}), method="PATCH")
        print(f"[pushed] {C} -> {new_commit} ({msg.splitlines()[0]})")
        parent, base_tree = new_commit, new_tree

    print("\n✅ 全部推送完成")


if __name__ == "__main__":
    main()
