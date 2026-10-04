pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.PREFER_SETTINGS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "NovaDesk"
include(":app")
// 导出壳：一个极简 WebView 应用，只用来当"AI 做的东西"的外壳。
// 它自己也是个 APK，会被打进 :app 的 assets；用户点导出时拿出来改包名重签。
include(":shell")
