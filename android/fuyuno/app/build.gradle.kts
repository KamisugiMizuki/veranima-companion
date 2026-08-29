plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "io.github.kamisugimizuki.veranima"
    compileSdk = 34

    defaultConfig {
        applicationId = "io.github.kamisugimizuki.veranima"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "0.1-spike"
        ndk { abiFilters += listOf("x86_64", "arm64-v8a") }  // MuMu + 真机
    }
    // chaquopy 的 python 任务名带 abi 后缀，srcDir 方式对每个 abi 复用同一树
    packaging {
        resources { excludes += listOf("META-INF/DEPENDENCIES") }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    composeOptions { kotlinCompilerExtensionVersion = "1.5.14" }
}

chaquopy {
    defaultConfig {
        version = "3.12"
        pip {
            // 核心运行面（ANDROID_SCOPE_SPEC 定稿）：torch/aiocqhttp 等已出局
            install("httpx")
            install("pyyaml")
            install("tzdata")  // 安卓无系统 tz 库，zoneinfo 自动回退到此包
            install("numpy")   // 记忆向量 _knn（chaquopy 线 1.26.2）
            install("pillow")  // image_payload 图片校验（类型/炸弹检测是安全边界）
        }
    }
    sourceSets {
        getByName("main") {
            // 核心包零拷贝：src layout 的 veranima/ 直接进 APK assets
            srcDir(file("../../../src"))
        }
    }
}

dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.09.02"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.core:core-ktx:1.13.1")
}
