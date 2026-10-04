/**
 * 桌面壳的原生联网桥 —— 只服务"检查更新"。
 * 背景（为什么不能靠页面的 fetch）见 nv_net.cpp 顶部。
 */
#ifndef NV_NET_H
#define NV_NET_H

#include <windows.h>

#include <string>
#include <vector>

/** 后台线程干完了，往窗口发这个（UI 线程收到后去 Drain） */
#define WM_NV_NET_DONE (WM_APP + 3)

void NvNetInit(HWND hwnd);
void NvNetShutdown();

/**
 * 起一个后台 GET。
 * 返回空字符串表示"已经在跑了"（结果稍后由 NvNetDrain 回给页面）；
 * 返回非空表示立刻失败（参数不对之类）。
 */
std::string NvNetStart(const std::string& id, const std::vector<std::string>& args);

/** UI 线程调用：把已经完成的活儿逐个回给页面 */
void NvNetDrain(void (*reply)(const std::string& id, const std::string& json));

#endif  // NV_NET_H
