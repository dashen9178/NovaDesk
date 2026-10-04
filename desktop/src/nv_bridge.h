// NovaDesk 桌面壳 —— 文件桥（普通 C++ 类，不是 COM）
//
// 为什么不是 COM/IDispatch：
//   WebView2 的 AddHostObjectToScript 要靠 IDispatch 的**类型库**才能生成方法代理，
//   没有类型库的话代理是个空壳 —— 页面能拿到对象，但每个成员都被当成无参调用，
//   带参数的方法根本调不了。自己造类型库（ITypeInfo）比这件事本身复杂得多。
//
// 所以改走 WebView2 最基础也最可靠的通道：
//   页面 window.chrome.webview.postMessage(我们自己编码的字符串)
//     -> 原生 WebMessageReceived
//     -> 干完活 ExecuteScript("window.__novaNative(id, {...})")
//
// 顺带的好处：桥变成异步之后，读一个几百 KB 的文件不会卡住界面。
#pragma once

#include "nv.h"

/** 目录选完之后要通知页面 */
constexpr UINT WM_NV_FILE_CHANGED = WM_APP + 1;

/** 有权限升级在等着用户确认（见 ConfirmPendingMode） */
constexpr UINT WM_NV_CONFIRM_MODE = WM_APP + 2;

class NvFileBridge {
public:
    explicit NvFileBridge(HWND hwnd) : m_hwnd(hwnd), m_set(NvLoadSettings()) {}

    /** 权限状态（拼成 JSON） */
    std::string InfoJson();

    /**
     * 执行一个调用。method 是方法名，args 是已解码的参数。
     * 返回给页面的 JSON 字符串。
     */
    std::string Call(const std::string& method, const std::vector<std::string>& args);

    /**
     * 弹确认框问用户要不要升权限，然后落实。
     *
     * ★ 必须由主线程消息循环调用，**不能**在 Call() 里同步弹 ——
     *   Call() 跑在 WebView2 的消息回调里，同步弹一个模态框会把
     *   整个 JS↔原生的通道堵死（页面那边所有调用一起卡住）。
     */
    void ConfirmPendingMode();

private:
    std::string DoList(const std::string& path);
    std::string DoRead(const std::string& path);
    std::string DoWrite(const std::string& path, const std::string& content);
    std::string DoMkdir(const std::string& path);
    std::string DoDelete(const std::string& path);
    std::string DoStat(const std::string& path);

    void DoSetMode(const std::string& mode);
    void DoPickFolder();     // 弹目录选择框，选完 PostMessage 让主线程通知页面
    void DoRequestAll();     // 请求"完全权限"（要用户确认）

    /**
     * 这个路径是不是"不能整个删掉"的。
     *
     * 判定方向很关键：**要删的路径是受保护路径的祖先（或它本身）就拒绝**。
     * 空路径在 all 档会解析成用户主目录、在 folder 档会解析成授权文件夹根，
     * 而 delete 走的是 fs::remove_all。
     */
    bool IsProtectedRoot(const std::wstring& fullPath) const;

    HWND         m_hwnd;
    NvSettings   m_set;
    /** 等用户确认的那个目标档位（空表示没有待确认的） */
    std::wstring m_pendingMode;
};
