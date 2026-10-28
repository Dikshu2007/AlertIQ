# AlertIQ mobile app

AlertIQ uses Capacitor to package the existing React app for Android and iOS.

## Android

1. Install Android Studio and an Android SDK.
2. From this directory, run `npm run mobile:sync`.
3. Run `npm run mobile:open:android` and launch the app on an emulator or connected device from Android Studio.

To build an APK, use **Build > Build APK(s)** in Android Studio. The generated APK can be installed on an Android device.

## iOS

iOS builds require macOS with Xcode. On a Mac, run `npm install`, `npm run mobile:sync`, then `npm run mobile:open:ios`. Select a simulator or connected device in Xcode and run the app.

## Connect the app to the backend

On the sign-in screen, enter the backend's reachable base URL and save it before signing in, for example `https://api.example.com`. The phone must be able to reach that server.

For local Android debug testing, put the phone and development PC on the same Wi-Fi, start the backend with `--host 0.0.0.0`, and enter `http://<PC-Wi-Fi-IP>:8000` (find the PC address with `ipconfig`). Allow Python through Windows Firewall if prompted. Cleartext HTTP is enabled only in Android debug builds; release builds should use HTTPS. Physical iOS devices also need an HTTPS backend URL.

The backend's CORS settings include the Capacitor app origins by default. If `CORS_ORIGINS` is set in `backend/.env`, include `capacitor://localhost`, `https://localhost`, and `http://localhost` there as well.

After changing frontend code, run `npm run mobile:sync` again before rebuilding the native app.
