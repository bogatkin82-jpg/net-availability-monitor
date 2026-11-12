import org.gradle.api.tasks.testing.Test
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "ru.netmon.android"
    compileSdk = 35
    // Явно: в .toolchain/ установлены только build-tools 35.0.0,
    // авто-скачивание SDK отключено (android.builder.sdkDownload=false).
    buildToolsVersion = "35.0.0"

    defaultConfig {
        applicationId = "ru.netmon.android"
        minSdk = 26
        targetSdk = 35
        versionCode = 11
        versionName = "1.5.2"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    // Debug-keystore создаём в android/debug.keystore (gitignore *.keystore):
    // в песочнице и CI нет доступа к ~/.android, а AGP иначе падает на подписи.
    //
    // Release-подпись берётся из окружения (CI раскладывает keystore из GitHub Secrets):
    //   ANDROID_KEYSTORE_PATH, ANDROID_KEYSTORE_PASSWORD, ANDROID_KEY_ALIAS, ANDROID_KEY_PASSWORD.
    // Без этих переменных release-сборка остаётся unsigned (debug/тесты не затрагиваются).
    val releaseKeystorePath = providers.environmentVariable("ANDROID_KEYSTORE_PATH").orNull
    val releaseKeystorePassword = providers.environmentVariable("ANDROID_KEYSTORE_PASSWORD").orNull
    val releaseKeyAlias = providers.environmentVariable("ANDROID_KEY_ALIAS").orNull ?: "netmon"
    val releaseKeyPassword = providers.environmentVariable("ANDROID_KEY_PASSWORD").orNull
        ?: releaseKeystorePassword

    signingConfigs {
        getByName("debug") {
            storeFile = file("debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
        create("release") {
            if (!releaseKeystorePath.isNullOrBlank() && !releaseKeystorePassword.isNullOrBlank()) {
                storeFile = file(releaseKeystorePath)
                storePassword = releaseKeystorePassword
                keyAlias = releaseKeyAlias
                keyPassword = releaseKeyPassword
                enableV1Signing = true
                enableV2Signing = true
                enableV3Signing = true
            }
        }
    }

    buildTypes {
        debug {
            signingConfig = signingConfigs.getByName("debug")
        }
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = if (!releaseKeystorePath.isNullOrBlank()) {
                signingConfigs.getByName("release")
            } else {
                null
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    sourceSets["main"].assets.srcDirs("src/main/assets")

    // Локальная сборка с ресурсами HONOR: папка в .gitignore, в CI её нет.
    val localHonorRes = file("src/localHonor/res")
    if (localHonorRes.isDirectory) {
        sourceSets["main"].res.srcDir(localHonorRes)
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    val composeBom = platform("androidx.compose:compose-bom:2024.10.01")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")

    testImplementation("junit:junit:4.13.2")
}

// ---------------------------------------------------------------------------
// Генерация локального debug-keystore (не коммитится, *.keystore в .gitignore).
// ---------------------------------------------------------------------------
val debugKeystoreFile = layout.projectDirectory.file("debug.keystore").asFile

val ensureDebugKeystore by tasks.registering {
    description = "Создаёт локальный debug.keystore для подписи debug-сборки"
    group = "build"
    outputs.file(debugKeystoreFile)
    doLast {
        if (!debugKeystoreFile.exists()) {
            debugKeystoreFile.parentFile.mkdirs()
            val keytool = File(System.getProperty("java.home"), "bin/keytool").absolutePath
            providers.exec {
                commandLine(
                    keytool, "-genkeypair", "-noprompt",
                    "-keystore", debugKeystoreFile.absolutePath,
                    "-storepass", "android",
                    "-alias", "androiddebugkey",
                    "-keypass", "android",
                    "-keyalg", "RSA",
                    "-keysize", "2048",
                    "-validity", "10000",
                    "-dname", "CN=Android Debug,O=Android,C=US",
                )
            }.result.get()
        }
    }
}

tasks.matching {
    it.name == "validateSigningDebug" || it.name.startsWith("packageDebug")
}.configureEach {
    dependsOn(ensureDebugKeystore)
}

// ---------------------------------------------------------------------------
// Паритет конфига: единственный источник истины — config.json в корне репозитория.
// Копируем его в assets перед сборкой и перед unit-тестами, чтобы приложение
// и тесты читали ровно тот же файл, что и сервер/macOS-версия.
// ---------------------------------------------------------------------------
val repoConfigFile = rootProject.file("../config.json")
val assetsConfigFile = layout.projectDirectory.file("src/main/assets/config.json")

val syncConfigJson by tasks.registering(Copy::class) {
    description = "Копирует корневой config.json в app/src/main/assets/config.json"
    group = "config"
    from(repoConfigFile)
    into(layout.projectDirectory.dir("src/main/assets"))
    rename { "config.json" }
    doFirst {
        check(repoConfigFile.exists()) { "Не найден корневой config.json: $repoConfigFile" }
    }
}

tasks.matching { it.name == "preBuild" }.configureEach {
    dependsOn(syncConfigJson)
}

tasks.withType<Test>().configureEach {
    dependsOn(syncConfigJson)
    systemProperty("repo.config.path", repoConfigFile.absolutePath)
    systemProperty("assets.config.path", assetsConfigFile.asFile.absolutePath)
}

// Сборка APK всегда должна содержать актуальный config.json.
tasks.matching { it.name == "mergeDebugAssets" || it.name == "mergeReleaseAssets" }.configureEach {
    dependsOn(syncConfigJson)
}
