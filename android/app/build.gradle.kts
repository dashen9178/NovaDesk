plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.novadesk.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.novadesk.app"
        minSdk = 26
        targetSdk = 34
        // 每发一版都要同步改这里 —— 只改文件名的话，
        // 手机上「应用信息」里看到的版本号不会变。
        versionCode = 17
        versionName = "2.7"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            isShrinkResources = false
            signingConfig = signingConfigs.getByName("debug")
        }
        debug {
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
    packaging {
        resources.excludes.add("META-INF/*.kotlin_module")
    }

    testOptions {
        // 单元测试跑在 JVM 上，没有真的 Android 环境；
        // 让没被 mock 的 android.* 方法返回默认值，而不是抛 "not mocked"。
        unitTests.isReturnDefaultValues = true
    }

    // :shell 打出来的壳 APK 由下面的 copyShellApk 放到这里，一起塞进 assets
    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/shellAssets"))
}

/*
 * 把导出壳（:shell 的 release 未签名包）拷进 assets/shell.apk。
 *
 * 它是"生成 APK"功能的模具：导出时拷一份、改包名和应用名、塞进用户的页面、重签名。
 * 必须是**未签名**的 —— 壳里不能含任何签名密钥，签名在设备上现做。
 *
 * 这一步以前最容易漏（网页改了、壳没重打，导出的还是老界面），
 * 所以做成构建依赖，而不是靠人记得手动拷。
 */
val copyShellApk by tasks.registering(Copy::class) {
    dependsOn(":shell:assembleRelease")
    from(layout.projectDirectory.file(
        "../shell/build/outputs/apk/release/shell-release-unsigned.apk"))
    into(layout.buildDirectory.dir("generated/shellAssets"))
    rename { "shell.apk" }
}

tasks.matching { it.name.startsWith("merge") && it.name.endsWith("Assets") }
    .configureEach { dependsOn(copyShellApk) }

// 单元测试也要用真壳 APK 跑一遍导出链路，所以得先把它准备好
tasks.matching { it.name.startsWith("test") && it.name.endsWith("UnitTest") }
    .configureEach { dependsOn(copyShellApk) }

dependencies {
    // 只用 androidx.core 的 WindowInsets / WindowInsetsController，
    // 不需要 appcompat（它是 AppCompatActivity 那套主题约束的来源）
    implementation("androidx.core:core-ktx:1.13.1")
    // 文件桥用它操作 SAF 选中的文件夹（DocumentFile）。手写 DocumentsContract
    // 也能做，但子目录遍历和 createFile 的边界太多，不值当。
    implementation("androidx.documentfile:documentfile:1.0.1")
    // 官方 apksig：导出 APK 时在设备上做 v1+v2+v3 重签名并自检。
    // 和 build-tools 里那个 apksigner 是同一份代码。
    implementation("com.android.tools.build:apksig:8.7.3")
    // Shizuku：拿"ADB 权限"（shell 身份）的正规做法。
    // 安卓不允许应用自己开 ADB 权限，必须由 Shizuku 从外面把 shell 身份转进来。
    // 有了它才能装/卸载应用、给别的应用授权、截屏、模拟点击、改系统设置。
    implementation("dev.rikka.shizuku:api:13.1.5")
    implementation("dev.rikka.shizuku:provider:13.1.5")

    testImplementation("junit:junit:4.13.2")
}
