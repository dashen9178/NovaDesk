plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
    id("org.jetbrains.kotlin.jvm") version "1.9.24" apply false
}

// 让根项目也能解析插件（app 模块含安卓插件，tools 模块含纯 JVM 插件）
allprojects {
    repositories {
        google()
        mavenCentral()
    }
}
