plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "net.ct3.garminsync"
    compileSdk = 36

    // Otherwise the artifact is the module name: "app-debug.apk", which is not
    // a useful thing to find in a downloads folder a year from now.
    base.archivesName = "garmin-sync"

    defaultConfig {
        applicationId = "net.ct3.garminsync"
        // Health Connect background reads need Android 15+; the app still
        // installs below that and simply syncs while it is open.
        minSdk = 30
        targetSdk = 36
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        // Debug only: the APK is sideloaded, never published, so there is no
        // release signing key to manage.
        getByName("debug") {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    testOptions {
        unitTests {
            isReturnDefaultValues = true
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.health.connect:connect-client:1.1.0")
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")

    // org.json ships in android.jar as stubs only, so unit tests need the real
    // implementation to exercise the wire format on a plain JVM.
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
