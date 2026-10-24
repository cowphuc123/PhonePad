plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.phonepad.app"
    buildFeatures { buildConfig = true }
    compileSdk = 34

    defaultConfig {
        applicationId = "com.phonepad.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 2
        versionName = "0.2"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            // Ký bằng khoá debug để cài thẳng APK, không cần lên CH Play
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
}
