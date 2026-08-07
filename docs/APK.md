# 打包 Android APK（学习助手 App）

学习助手网页版可以用 Capacitor 封装成 Android App，App 内直接加载你的服务器网址。

## 方式一：GitHub Actions 自动打包（推荐，无需本地环境）

1. 把代码推送到 GitHub（本仓库已带打包工作流）
2. 打开仓库页面 → **Actions** → 左侧选 **Build Android APK**
3. 点 **Run workflow**
4. 在 **server_url** 里填你的服务器地址，例如：
   - `http://123.207.58.61:8090`
   - 或以后上了 HTTPS：`https://study.example.com`
5. 点绿色 **Run workflow** 开始构建（约 5-10 分钟）
6. 构建完成后，点击该次运行记录 → 底部 **Artifacts** → 下载 **study-agent-apk**
7. 解压得到 `app-debug.apk`，传到手机安装即可

> 提示：`app-debug.apk` 是调试签名包，自己使用没问题；上架应用商店需要正式签名（release）。

## 方式二：本地构建（Windows）

需要先安装：

- Node.js 18+（https://nodejs.org）
- Android Studio + Android SDK（https://developer.android.com/studio）

然后在项目目录执行：

```bash
npm install
npx cap config set server.url "http://123.207.58.61:8090"
npx cap add android
npx cap sync android
```

用 Android Studio 打开 `android` 文件夹，点 **Run ▶** 即可安装到手机/模拟器；
或者命令行构建：

```bash
cd android
gradlew.bat assembleDebug
```

APK 输出在 `android/app/build/outputs/apk/debug/app-debug.apk`。

## 注意事项

- App 只是一个外壳，实际功能仍由你服务器上的学习助手提供，手机需要能访问到服务器
- 服务器目前是 `http://IP:8090`（明文），App 已开启 cleartext 允许访问；但 Chrome PWA 安装仍然需要 HTTPS
- 换服务器地址后重新按上面步骤构建一次即可
