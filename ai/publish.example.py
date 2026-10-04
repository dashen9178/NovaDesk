#!/usr/bin/env python3
"""
把新版本发布到 SeaTable 的「novadesk」base。

    python publish.py 手机  D:\\novadesk-share\\NovaDesk-2.7.apk
    python publish.py 电脑  D:\\novadesk-share\\NovaDesk.exe
    python publish.py 手机 --list

★ 这个脚本使用写权限 API。复制本模板为 publish.py，再把
  YOUR_SEATABLE_WRITE_TOKEN 替换成只保存在本机的写权限令牌。
  publish.py 已在 .gitignore 中排除，不能提交到 GitHub。
"""
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

HOST = "https://cloud.seatable.cn"
# ★ 写权限 API —— 只在本机使用，不要提交
UPLOAD_TOKEN = "YOUR_SEATABLE_WRITE_TOKEN"

TABLES = ("手机", "电脑")


def req(url, method="GET", data=None, headers=None, timeout=60):
    h = dict(headers or {})
    r = urllib.request.Request(url, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def jreq(url, method="GET", obj=None, headers=None, timeout=60):
    h = dict(headers or {})
    body = None
    if obj is not None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        h["Content-Type"] = "application/json"
    st, raw = req(url, method, body, h, timeout)
    txt = raw.decode("utf-8", "replace") if raw else ""
    if st >= 400:
        raise SystemExit("HTTP %d %s\n%s" % (st, url, txt[:500]))
    return json.loads(txt) if txt.strip() else {}


def base_token():
    """用写权限 API 换 base token。"""
    return jreq(HOST + "/api/v2.1/dtable/app-access-token/",
                headers={"Authorization": "Token " + UPLOAD_TOKEN})


def multipart(fields, filename, filebytes, fieldname="file"):
    boundary = "----NovaDesk" + uuid.uuid4().hex
    out = []
    for k, v in fields.items():
        out.append(("--%s\r\n" % boundary).encode())
        out.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode())
        out.append(str(v).encode("utf-8") + "\r\n")
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    out.append(("--%s\r\n" % boundary).encode())
    out.append(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                % (fieldname, filename)).encode())
    out.append(("Content-Type: %s\r\n\r\n" % ctype).encode())
    out.append(filebytes + "\r\n")
    out.append(("--%s--\r\n" % boundary).encode())
    return b"".join(out), "multipart/form-data; boundary=" + boundary


def upload(local_path):
    name = os.path.basename(local_path)
    data = open(local_path, "rb").read()

    link = jreq(HOST + "/api/v2.1/dtable/app-upload-link/",
                headers={"Authorization": "Token " + UPLOAD_TOKEN})
    up = link["upload_link"]
    parent_dir = link.get("parent_path", "/")
    rel = link.get("file_relative_path") or link.get("img_relative_path") or "/"
    ws = link.get("workspace_id")

    body, ctype = multipart(
        {"parent_dir": parent_dir, "relative_path": rel}, name, data)
    st, raw = req(up + "?ret-json=1", "POST", body,
                  {"Content-Type": ctype,
                   "Authorization": "Token " + UPLOAD_TOKEN})
    if st >= 400:
        raise SystemExit("上传失败 HTTP %d\n%s"
                         % (st, raw.decode("utf-8", "replace")[:400]))

    url = "/workspace/%s%s%s/%s" % (ws, parent_dir, rel, name)
    print("    上传成功 %s (%.2f MB)" % (name, len(data) / 1048576.0))
    return url, len(data), name


def add_row(base, table, version, file_url, size, fname, note=""):
    uuid_ = base["dtable_uuid"]
    server = base.get("dtable_server") or (HOST + "/api-gateway/")
    if not server.endswith("/"):
        server += "/"
    url = server + "api/v2/dtables/%s/rows/" % uuid_
    row = {
        "版本": version if not note else (version + "\n" + note),
        "软件": [{
            "name": fname,
            "size": size,
            "type": "file",
            "url": file_url,
            "upload_time": datetime.now(timezone.utc).isoformat(),
        }],
    }
    r = jreq(url, "POST", {"table_name": table, "rows": [row]},
             headers={"Authorization": "Bearer " + base["access_token"]})
    print("    写进「%s」表：inserted=%s" % (table, r.get("inserted_row_count")))


def list_rows(base, table):
    uuid_ = base["dtable_uuid"]
    server = base.get("dtable_server") or (HOST + "/api-gateway/")
    if not server.endswith("/"):
        server += "/"
    url = server + "api/v2/dtables/%s/rows/?table_name=%s&convert_keys=true" % (
        uuid_, urllib.parse.quote(table))
    r = jreq(url, headers={"Authorization": "Bearer " + base["access_token"]})
    rows = r.get("rows", [])
    print("== 表「%s」：%d 行" % (table, len(rows)))
    for row in rows:
        v = row.get("版本", "")
        f = row.get("软件")
        fname = f[0].get("name") if isinstance(f, list) and f else "(空)"
        print("   %-12s %s" % (str(v).replace("\n", " / ")[:40], fname))


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    table = sys.argv[1]
    if table not in TABLES:
        print("表名只能是：%s" % " / ".join(TABLES))
        return 1

    base = base_token()
    print("== base：%s (%s)" % (base.get("dtable_name"), base.get("dtable_uuid")))

    if sys.argv[2] == "--list":
        for t in TABLES:
            list_rows(base, t)
        return 0

    path = sys.argv[2]
    if not os.path.exists(path):
        print("找不到文件：%s" % path)
        return 1

    import re
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", os.path.basename(path))
    if m:
        version = m.group(1)
    else:
        gradle = os.path.abspath(os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "android", "app", "build.gradle.kts"))
        try:
            gv = re.search(r'versionName\s*=\s*"([^"]+)"',
                           open(gradle, encoding="utf-8").read())
            version = gv.group(1) if gv else ""
        except OSError:
            version = ""
        if not version:
            print("文件名里没有版本号，也读不到 build.gradle.kts；"
                  "请用：publish.py %s <文件> <版本号>" % table)
            return 1
    note = " ".join(sys.argv[3:])

    print("== 发布 %s → 表「%s」" % (version, table))
    url, size, fname = upload(path)
    print("    文件路径 %s" % url)
    add_row(base, table, version, url, size, fname, note)
    print("== 完成。打开软件就会看到更新提示。")
    return 0


if __name__ == "__main__":
    import urllib.parse
    sys.exit(main())
