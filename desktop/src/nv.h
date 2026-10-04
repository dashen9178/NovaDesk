// NovaDesk 桌面壳 —— 公共声明
//
// 这个壳一件事干两遍：
//   1. 它自己就是 NovaDesk.exe（尾部嵌着 NovaDesk.html）
//   2. 它也是"导出 exe"用的模具 —— 导出时拷一份、把用户做的东西追加到尾部，
//      改个名字就成了一个独立的小应用
//
// 之所以一个二进制能干两件事：壳启动时读自己文件的**尾部**，找一段固定格式的
// 页脚，页脚里写着"内嵌的页面从第几个字节开始、多长、应用叫什么"。
// 找到了就加载它；找不到就退回加载 exe 旁边的 app.html（开发时方便）。
//
// 这样导出**完全不需要编译器**：只要往文件尾追加几个字节，普通程序照样能跑
// （PE 加载器只认头部，尾部多出来的数据它不管）。
#pragma once

#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif

// windows.h 默认把 min/max 定义成宏，会把 std::min / std::max 全搅坏
//（报的是 ":: 右边的非法标记"，看着莫名其妙）。关掉它。
#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>
#include <string>
#include <vector>
#include <cstdint>

// ============================================================
// 尾部页脚
// ============================================================
//
// 文件布局： [壳 exe][页面 HTML][应用名 UTF-8][页脚 40 字节]
//
// 页脚必须在**最后 40 个字节**，读取时直接 Seek 到文件尾往前数即可。
// magic 用来确认"这确实是我们的格式"，防止有人把普通 exe 当壳用。
#pragma pack(push, 1)
struct NvFooter {
    char     magic[8];      // "NVDSEXE1"
    uint64_t htmlOffset;    // 页面在文件里的绝对偏移
    uint64_t htmlLen;       // 页面字节数
    uint64_t nameOffset;    // 应用名在文件里的绝对偏移
    uint32_t nameLen;       // 应用名字节数（UTF-8）
    uint32_t reserved;      // 占位，凑够 40 字节并留扩展余地
};
#pragma pack(pop)

static_assert(sizeof(NvFooter) == 40, "页脚必须是 40 字节，改动要同步改 build.ps1");

constexpr char NV_MAGIC[8] = { 'N','V','D','S','E','X','E','1' };

/** 从当前 exe 尾部读出来的"我是谁" */
struct NvPayload {
    bool        ok = false;      // 尾部有没有合法的页脚
    std::string html;            // 内嵌页面
    std::wstring appName;        // 应用名（已转成宽字符）
    std::wstring exePath;        // 自己的完整路径
    uint64_t shellSize = 0;      // 纯壳的长度 = 页面数据的起始偏移
};

/** 读自己尾部拿到内嵌的应用信息 */
NvPayload NvLoadPayload();

/**
 * 用当前 exe 当模具，导出一个独立的小应用。
 *
 * 关键：取自己的**前 shellSize 字节**（那才是没嵌过东西的纯壳），
 * 而不是整份拷自己 —— 否则每导出一次，上一份内嵌的页面会跟着攒进去，
 * 越导出越胖。
 */
bool NvExportExe(const std::wstring& selfPath, uint64_t shellSize,
                 const std::string& html, const std::wstring& appName,
                 const std::wstring& outPath);

/** 默认导出目录：桌面；取不到就退回用户主目录 */
std::wstring NvExportDir();

// ============================================================
// 小工具
// ============================================================

std::string  NvUtf8(const std::wstring& w);
std::wstring NvWide(const std::string& s);

/** 把 JSON 字符串转义（只处理必须转的字符） */
std::string NvJsonEscape(const std::string& s);

/** 拼一个 JSON 对象用的极简构造器，别当通用库用 */
class NvJson {
public:
    NvJson& Str(const char* key, const std::string& val) {
        Sep(); m_buf += "\"" + std::string(key) + "\":\"" + NvJsonEscape(val) + "\"";
        return *this;
    }
    NvJson& Num(const char* key, long long val) {
        Sep(); m_buf += "\"" + std::string(key) + "\":" + std::to_string(val);
        return *this;
    }
    NvJson& Bool(const char* key, bool val) {
        Sep(); m_buf += "\"" + std::string(key) + "\":" + (val ? "true" : "false");
        return *this;
    }
    /** 塞一段已经拼好的 JSON（数组之类） */
    NvJson& Raw(const char* key, const std::string& json) {
        Sep(); m_buf += "\"" + std::string(key) + "\":" + json;
        return *this;
    }
    std::string Done() const { return "{" + m_buf + "}"; }
    /** 去掉外面那层花括号的正文 —— 拼进 NvOk() 时用这个，别拿 Done() 去 substr */
    std::string Body() const { return m_buf; }

private:
    void Sep() { if (!m_buf.empty()) m_buf += ","; }
    std::string m_buf;
};

/** 成功 / 失败两种结果的快捷构造 */
std::string NvOk(const std::string& body);
std::string NvFail(const std::string& msg);
std::string NvFailErr(const std::exception& e);

// ============================================================
// 设置（mode / folder），存在 %LOCALAPPDATA%\NovaDesk\desktop.ini
// ============================================================

struct NvSettings {
    std::wstring mode   = L"off";   // off / folder / all
    std::wstring folder;            // folder 模式下用户选中的目录
};

NvSettings    NvLoadSettings();
void          NvSaveSettings(const NvSettings& s);
std::wstring  NvDataDir();          // %LOCALAPPDATA%\NovaDesk

/**
 * 由 exe 路径算出的稳定短哈希。
 *
 * 每个 exe 分到自己的设置文件和运行目录 —— 否则 NovaDesk 和它导出的应用会
 * 共用同一份权限设置：用户在 NovaDesk 里开了"完全权限"，随手导出的那个小应用
 * 一启动就直接是全盘可读写，而他从来没给过那个应用授权。
 */
std::wstring  NvExeKey();

// ============================================================
// 文件操作（三档权限共用，mode 判断在桥里做）
// ============================================================

/** 把用户给的路径归一成绝对路径。folder 模式下按 folder 根解析相对路径。 */
std::wstring NvResolvePath(const NvSettings& s, const std::string& pathUtf8, bool* outAllowed);
