// NovaDesk 桌面壳 —— "导出成 exe"的实现
#include "nv_exebridge.h"

#include <shlobj.h>
#include <filesystem>
#include <algorithm>

namespace fs = std::filesystem;

static std::string ArgAt(const std::vector<std::string>& a, size_t i) {
    return i < a.size() ? a[i] : std::string();
}

std::string NvExeBridge::DoInfo() {
    return NvJson()
        .Bool("available", true)
        .Str("appName", NvUtf8(m_self.appName))
        .Str("exportDir", NvUtf8(NvExportDir()))
        .Str("self", NvUtf8(m_self.exePath))
        .Num("shellSize", (long long)m_self.shellSize)
        .Done();
}

std::string NvExeBridge::DoExport(const std::string& appNameRaw,
                                  const std::string& html) {
    // 去掉文件名里不能用的字符
    std::string safe;
    for (char c : appNameRaw) {
        if (c == '\\' || c == '/' || c == ':' || c == '*' || c == '?' ||
            c == '"' || c == '<' || c == '>' || c == '|' || c == '\r' || c == '\n') {
            safe += '_';
        } else {
            safe += c;
        }
    }
    // 先转宽字符再按字符数截断 —— 直接按字节截会把汉字切坏
    std::wstring wName = NvWide(safe);
    while (!wName.empty() && wName.front() == L' ') wName.erase(wName.begin());
    while (!wName.empty() && wName.back() == L' ') wName.pop_back();
    if (wName.size() > 20) wName = wName.substr(0, 20);
    if (wName.empty()) wName = L"我的应用";

    if (html.empty()) return NvFail("没有可打包的内容");
    if (m_self.shellSize == 0) {
        return NvFail("这个 exe 尾部没有页脚信息，没法当模具用");
    }

    fs::path out = fs::path(NvExportDir()) / (wName + L".exe");

    // 同名文件先挪开：万一写到一半失败，也不至于把用户上一版毁掉
    std::error_code ec;
    fs::path bak = out;
    bak += L".old";
    bool hadOld = false;
    if (fs::exists(out, ec)) {
        fs::remove(bak, ec);
        ec.clear();
        fs::rename(out, bak, ec);
        if (ec) {
            // 挪不动（比如原文件被占用、或者同名目录挡着）就**直接放弃**。
            // 硬着头皮往下写的话，NvExportExe 会 truncate 掉 out ——
            // 用户原来那份能用的 exe 就没了，而"还原"也无从还原。
            return NvFail("原文件占用中或无法改名，先关掉它再试：" + NvUtf8(bak.wstring()));
        }
        hadOld = true;
    }

    if (!NvExportExe(m_self.exePath, m_self.shellSize, html, wName, out.wstring())) {
        // 失败要把现场收拾干净：删掉半成品，把上一版改回来。
        // 不然用户桌面上留一个打不开的 exe，而好的那个躺在 xxx.exe.old 里。
        std::error_code e3;
        fs::remove(out, e3);
        if (hadOld) {
            fs::rename(bak, out, e3);
            return NvFail("打包失败（写文件或者自检没过），已把原来的文件放回去");
        }
        return NvFail("打包失败（写文件或者自检没过）");
    }

    // 成了就把备份删掉
    if (hadOld) {
        std::error_code e4;
        fs::remove(bak, e4);
    }

    uint64_t size = 0;
    {
        std::error_code e2;
        size = (uint64_t)fs::file_size(out, e2);
    }

    return NvOk(NvJson()
        .Str("path", NvUtf8(out.wstring()))
        .Str("name", NvUtf8(wName))
        .Num("size", (long long)size)
        .Body());
}

std::string NvExeBridge::DoReveal(const std::string& path) {
    std::wstring w = NvWide(path);
    if (w.empty()) return NvFail("路径为空");
    std::error_code ec;
    if (!fs::exists(fs::path(w), ec)) return NvFail("找不到这个文件");

    // explorer /select 会直接选中它，比单纯打开目录好找
    std::wstring args = L"/select,\"" + w + L"\"";
    HINSTANCE r = ShellExecuteW(nullptr, L"open", L"explorer.exe", args.c_str(),
                                nullptr, SW_SHOWNORMAL);
    if ((INT_PTR)r <= 32) return NvFail("打不开资源管理器");
    return NvOk("");
}

std::string NvExeBridge::Call(const std::string& method,
                              const std::vector<std::string>& args) {
    try {
        if (method == "info")   return DoInfo();
        if (method == "export") return DoExport(ArgAt(args, 0), ArgAt(args, 1));
        if (method == "reveal") return DoReveal(ArgAt(args, 0));
        return NvFail("未知方法：" + method);
    } catch (const std::exception& e) {
        return NvFailErr(e);
    } catch (...) {
        return NvFail("未知错误");
    }
}
