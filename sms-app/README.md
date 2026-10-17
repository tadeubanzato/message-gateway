# Message Gateway SMS (Android)

An Android app that turns a phone into an SMS gateway: your systems call an API (or a server calls the phone), the phone
sends the text from its own SIM, and the phone reports what happened and any texts it receives.

It is built to be the phone side of the **Android phone (SMSGate)** provider in this repository's Message Gateway, and it
speaks the same API as the open-source [SMS Gateway for Android](https://github.com/capcom6/android-sms-gateway), so
existing clients keep working. It is an independent implementation (Kotlin, Jetpack Compose, Material 3), not a fork.

## What it does

- **Local mode.** The phone runs an HTTP API (default port 8080, Basic auth). Send with `POST /messages`, list and
  cancel messages, read the inbox and logs, manage webhooks. Same routes and JSON as SMS Gateway for Android
  (`/health`, `/devices`, `/messages`, `/inbox`, `/webhooks`, `/logs`).
- **Cloud mode.** The phone registers with an SMSGate server (the public cloud, or your own, such as the one the Message
  Gateway starts with `MG_SMSGATE=1`). The server tells the phone over a held-open event stream that a message is
  waiting; the phone fetches it, sends it, and reports `Pending → Processed → Sent → Delivered / Failed` back.
- **Webhooks.** `sms:received`, `sms:sent`, `sms:delivered` and `sms:failed`, signed with HMAC-SHA256
  (`X-Signature` over body + `X-Timestamp`), retried with growing pauses and kept across restarts. Webhooks set on the
  server are copied to the phone automatically.
- **At a glance.** Home shows what is on (Local API, Cloud) in green, amber or red, the addresses and credentials to
  connect with (Wi-Fi address, public IP, username and password), a 7 / 14 / 30 day activity chart (sent, failed,
  received) with a table view, and a setup checklist.
- **Built for a phone left running.** A foreground service, an optional wake lock, a battery-optimisation shortcut, a
  restart after reboot or update, reconnection with backoff, and a periodic backup sync.

### Not included (yet)

MMS, data SMS, RCS, end-to-end encryption, Firebase push (the event stream is used instead), and uploading received
texts to the server's inbox (that server only accepts encrypted inbox content; received texts reach your systems through
webhooks instead).

## Using it with the Message Gateway

1. In the gateway open **Channels → SMS → Android phone (SMSGate)** (through its https address). It shows an **API URL**
   and a **Private token**.
2. In the app open **Home → Cloud → Set up cloud**, choose **My own server**, paste both, leave **New account** selected
   and tap **Connect**.
3. The app shows a **username and password** (Home → Connect your system → Cloud). Enter them in the gateway's SMSGate
   fields. They must be this app's account: a gateway still pointing at another account sends to that account's phone.
4. Leave **Server address** and **Device ID** empty in the gateway (the built-in server is used, and an account with one
   phone needs no device ID).
5. For received texts, turn on **Incoming messages** in the gateway. The webhooks appear in the app's **Webhooks** tab.

## Building

Requirements: JDK 21, the Android SDK with platform 37 and build-tools 36.

```sh
echo "sdk.dir=/path/to/android/sdk" > local.properties   # not committed
./gradlew :app:assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
./gradlew :app:testDebugUnitTest      # unit tests
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

The debug build allows plain `http://` webhook addresses for testing on a local network. A release build requires
`https` and needs your own signing key, which is not part of this repository.

## How it is organised

```
app/src/main/java/app/messagegateway/sms/
  core/       settings, phone-number and time helpers, contracts the other packages depend on
  data/       Room database: messages, recipients, incoming texts, webhooks, delivery queue, logs
  domain/     message states and the rules for moving between them
  send/       the send queue and engine, the SMS transport, and the single place message state changes
  receiver/   incoming SMS, send/delivery results, boot and update
  webhooks/   webhook storage, signing, delivery and retries
  server/     the local HTTP API (Ktor) and the foreground service that keeps everything running
  cloud/      the SMSGate client: registration, event stream, pulling and reporting, webhook sync
  ui/         Jetpack Compose screens
```

Texts are sent straight from the running process: a message is written to the database, then handed to the radio with
no job scheduler in between. WorkManager is used only to wake the app for work that has to wait (a scheduled message,
the rate limit, a webhook retry, the backup sync).

## Good to know

- The phone must stay reachable: allow SMS permission, turn on **Unrestricted battery**, and don't force-stop the app.
- Keep the local API on a private network (or a VPN such as Tailscale). Anyone with its username and password can send
  texts from the phone.
- Credentials and webhook secrets are kept in the app's private storage and are never written to its log.
- Settings → Sending speed can pause between texts and cap the rate, if your carrier throttles bursts. It is off by
  default.

## Credits

The API shape and the webhook format follow the open-source
[SMS Gateway for Android](https://github.com/capcom6/android-sms-gateway) and its
[server](https://github.com/capcom6/sms-gateway) (Apache-2.0), whose public source and documentation were used as the
reference for compatibility.
