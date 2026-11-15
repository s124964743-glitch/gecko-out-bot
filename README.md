# 壁虎自動過關（Gecko Out Bot）

Android App（ROG Phone 7，1080x2448）。用無障礙服務截圖＋模擬拖曳，Python(OpenCV)辨識棋盤與求解。

- 辨識棋盤格線、牆、洞、壁虎、冰塊(含倒數數字)，依顏色配對洞，求出進洞順序（含讓路步驟）。
- 壁虎頭尾任一端都能拖；進洞後洞會變地板；冰塊數字每有一隻壁虎進洞就減 1。
- 過關後自動處理：結算(點 40)、Island Quest、Ancient Climb、Tap to Continue、主畫面 PLAY、選道具視窗 PLAY。
- 不認得的畫面（廣告等）：等待 → 返回鍵 → 點右上/左上角；並把截圖存到 `Android/data/app.geckobot/files/unknown_*.png` 供加入辨識。

`pc-tool/` 是同一套邏輯的電腦版（adb）。

## 安裝與使用
1. 到 Releases 下載最新 `GeckoBot.apk` 安裝（金鑰固定，新版可直接覆蓋）。
2. 開啟 App →「開啟無障礙設定」→ 啟用「壁虎自動過關」。
3. 開 Gecko Out，按畫面上的綠色 ▶ 懸浮按鈕開始，再按一次暫停。

> 金鑰在 repo 內，請保持 private。
