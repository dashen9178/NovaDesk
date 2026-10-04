plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

/*
 * 导出壳。
 *
 * 它自己不是给用户点的应用，而是一块"模具"：
 * 用户点导出时，把这个 APK 拷一份、改写包名和应用名、塞进他自己的页面、
 * 重新签名，就成了一个独立的、能装到任何手机上的 APK。
 *
 * ★ namespace 和 applicationId 故意**不一样**，这是整套机制的关键：
 *
 *     namespace      = com.novadesk.shellhost   ← Activity 真实所在的包，dex 里就长这样
 *     applicationId  = com.novadesk.rt.a00000   ← 会被改写成用户包名的那一个
 *
 *   导出时改的是 applicationId（清单里的 package= 和 resources.arsc 里的包名），
 *   Activity 的 android:name 写的是**完整类名** com.novadesk.shellhost.ShellActivity，
 *   跟待改写的字符串毫不相干 —— 所以改完包名，类引用依然指向 dex 里真实存在的类。
 *
 *   如果这里偷懒让两者相同、清单里写 ".ShellActivity"，改完包名之后清单就会指向
 *   一个不存在的类，装上是能装上，一点就闪退。FAST 那边也是靠这个错位实现的。
 */
android {
    namespace = "com.novadesk.shellhost"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.novadesk.rt.a00000"
        minSdk = 26
        targetSdk = 34
        // 导出时会改写 versionName，versionCode 保持 1
        versionCode = 1
        versionName = "0.00"
    }

    buildTypes {
        release {
            // 关掉混淆：Activity 类名不能被改掉，清单里写死的就是这个名字
            isMinifyEnabled = false
            isShrinkResources = false
            // 刻意不给 signingConfig —— 出来的是 *-unsigned.apk，
            // 导出时由设备自己的密钥重签，壳里不含任何密钥材料。
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
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
}
