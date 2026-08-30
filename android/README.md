# Android app

Reads `WeightRecord` from Health Connect and POSTs it to the sync service.

## Building locally

No Android Studio required. The toolchain installs user-local, no sudo:

```bash
T=~/.local/share/android-toolchain && mkdir -p $T && cd $T

curl -L -o jdk.tar.gz "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jdk/hotspot/normal/eclipse"
tar xzf jdk.tar.gz

curl -L -o cmdline.zip "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
unzip -q cmdline.zip && mkdir -p sdk/cmdline-tools && mv cmdline-tools sdk/cmdline-tools/latest

curl -L -o gradle.zip "https://services.gradle.org/distributions/gradle-8.11.1-bin.zip"
unzip -q gradle.zip

export JAVA_HOME=$T/jdk-17*  ANDROID_HOME=$T/sdk
export PATH=$JAVA_HOME/bin:$T/sdk/cmdline-tools/latest/bin:$T/gradle-8.11.1/bin:$PATH
yes | sdkmanager --licenses
sdkmanager --install "platforms;android-36" "build-tools;35.0.0" "platform-tools"
```

Then, from `android/`:

```bash
echo "sdk.dir=$ANDROID_HOME" > local.properties
gradle testDebugUnitTest assembleDebug
```

The APK lands at `app/build/outputs/apk/debug/app-debug.apk`.

## Version constraints

`androidx.health.connect:connect-client:1.1.0` requires **compileSdk 36** and
**AGP 8.9.1 or newer**. Both are hard build failures, not warnings, so do not
lower them to match an older toolchain.

## Tests

`app/src/test/` runs on a plain JVM - no device, no emulator. `PayloadTest`
covers the wire contract with the server and writes the payload it produces to
`build/wire-contract.json`, so the server's parser can be fed the exact bytes
this app emits:

```bash
gradle testDebugUnitTest
cd .. && .venv/bin/python -c "
import json
from garmin_stats_sync.health_connect import parse_payload
print(parse_payload(json.load(open('android/app/build/wire-contract.json'))))"
```

This is why `Payload.kt` takes a plain `WeighIn` rather than a Health Connect
type: keeping the wire format free of Android classes is what makes the contract
testable off-device.
