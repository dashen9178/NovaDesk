plugins {
    id("org.jetbrains.kotlin.jvm") version "1.9.24"
    application
}

repositories {
    mavenCentral()
}

kotlin {
    jvmToolchain(17)
}

/**
 * 内核验证台。
 *
 * 它不依赖 app 模块，而是直接把 kernel/apps/abi 的源码当作
 * 普通 Kotlin 源码编译进来。这正是"内核零安卓依赖"的证明：
 * 同一份源码，既能在安卓上跑，也能在 PC 的 JVM 上跑。
 */
sourceSets {
    main {
        kotlin.srcDirs(
            "../app/src/main/java/nova/abi",
            "../app/src/main/java/nova/kernel",
            "../app/src/main/java/nova/apps",
            "."
        )
    }
}

application {
    mainClass.set("nova.test.Main")
}

/** 图像内容检查器，可以单独运行 */
tasks.register<JavaExec>("inspect") {
    group = "verification"
    description = "Check rendered frames for structural correctness"
    classpath = sourceSets["main"].runtimeClasspath
    mainClass.set("nova.test.Inspect")
    args = listOf("D:\\DSH工作\\NovaDesk\\_verify")
}

tasks.named<JavaExec>("run") {
    // 让 PNG 输出到一个固定位置
    args = listOf("D:\\DSH工作\\NovaDesk\\_verify")
}
