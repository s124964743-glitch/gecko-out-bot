plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// versionCode 由 GitHub Actions 的 run_number 傳入；固定簽章 + 遞增版本 => 新版可直接覆蓋安裝。
val buildNumber = (project.findProperty("versionCode") as String?)?.toIntOrNull() ?: 1

android {
    namespace = "app.memedit"
    compileSdk = 34

    defaultConfig {
        applicationId = "app.memedit"
        minSdk = 30
        targetSdk = 34
        versionCode = buildNumber
        versionName = "1.0.$buildNumber"
    }

    signingConfigs {
        create("fixed") {
            storeFile = file("release.keystore")
            storePassword = "geckobot"
            keyAlias = "gecko"
            keyPassword = "geckobot"
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("fixed")
        }
    }

    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
}
